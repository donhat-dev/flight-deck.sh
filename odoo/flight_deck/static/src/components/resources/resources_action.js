/** @odoo-module **/

import { Component, onMounted, onWillStart, onWillUnmount, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";

const REFRESH_MS = 5000;
const LOCAL_STALE_MS = 45000;

function age(seconds) {
    if (seconds === null || seconds === undefined) {
        return "?";
    }
    const m = Math.floor(seconds / 60);
    if (m < 1) {
        return "now";
    }
    if (m < 60) {
        return `${m}m`;
    }
    return `${Math.floor(m / 60)}h${m % 60}m`;
}
function ago(iso) {
    if (!iso) {
        return "";
    }
    const ms = Date.now() - new Date(iso).getTime();
    if (Number.isNaN(ms)) {
        return "";
    }
    const m = Math.round(Math.max(0, ms) / 60000);
    if (m < 1) {
        return "just now";
    }
    return m < 60 ? `${m}m ago` : `${Math.round(m / 60)}h ago`;
}

export class ResourcesAction extends Component {
    static template = "flight_deck.ResourcesAction";
    static props = { ...standardActionServiceProps };

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.state = useState({
            data: null, error: "", runnerId: null, selected: null,
            chatText: "", working: false, confirming: false,
        });
        this.pollGen = 0;
        this.loading = false;
        this.dead = false;
        this.pendingReload = false;
        onWillStart(() => this.load());
        onMounted(() => {
            this.timer = setInterval(() => {
                if (!document.hidden && !this.loading && !this.dead) {
                    this.load();
                }
            }, REFRESH_MS);
        });
        onWillUnmount(() => {
            this.dead = true;
            clearInterval(this.timer);
        });
    }

    async load() {
        if (this.dead) {
            return;
        }
        if (this.loading) {
            this.pendingReload = true;
            return;
        }
        const gen = ++this.pollGen;
        const runnerId = this.state.runnerId;
        this.loading = true;
        try {
            const data = await this.orm.silent.call(
                "flightdeck.runner", "fd_resource_dashboard", [runnerId]);
            if (this.dead || gen !== this.pollGen || this.state.runnerId !== runnerId) {
                return;
            }
            if (this.state.runnerId !== data.runner_id) {
                this.state.runnerId = data.runner_id;
                this.state.selected = null;
            }
            this.state.data = data;
            this.state.error = "";
        } catch {
            if (gen === this.pollGen && !this.dead) {
                this.state.error = "Could not read the resources; trying again.";
            }
        } finally {
            this.loading = false;
            if (this.pendingReload && !this.dead) {
                this.pendingReload = false;
                this.load();
            }
        }
    }

    get snapshot() {
        return this.state.data?.snapshot || {};
    }
    get processes() {
        return this.state.data?.snapshot?.processes || [];
    }
    get mcpServers() {
        return this.state.data?.snapshot?.mcp_servers || [];
    }
    get actions() {
        return this.state.data?.actions || [];
    }
    get agent() {
        return this.state.data?.agent || {};
    }
    get chat() {
        return this.state.data?.chat || [];
    }
    get canManage() {
        return !!this.state.data?.can_manage;
    }
    get usable() {
        const data = this.state.data;
        if (!data || this.state.error || data.runner_id !== this.state.runnerId) {
            return false;
        }
        const snap = data.snapshot;
        if (!snap?.available || !snap.complete || !data.snapshot_fresh) {
            return false;
        }
        const taken = new Date(snap.taken_at).getTime();
        if (Number.isNaN(taken)) {
            return false;
        }
        const ageMs = Date.now() - taken;
        return ageMs >= 0 && ageMs <= LOCAL_STALE_MS;
    }
    get selectedRow() {
        if (!this.state.selected || !this.state.data) {
            return null;
        }
        const { kind, key } = this.state.selected;
        if (kind === "mcp") {
            return this.mcpServers.find((s) => s.key === key) || null;
        }
        return this.processes.find((p) => p.key === key) || null;
    }

    select(kind, key) {
        this.state.selected = { kind, key };
    }
    async onRunnerChange(ev) {
        this.state.runnerId = parseInt(ev.target.value, 10);
        this.state.data = null;
        this.state.selected = null;
        this.state.error = "";
        this.pollGen++;
        this.load();
    }
    rowClass(p) {
        let c = "fd_res_row";
        if (this.state.selected?.key === p.key) {
            c += " fd_res_sel";
        }
        if (!p.manageable) {
            c += " fd_res_ro";
        }
        if (p.protected) {
            c += " fd_res_prot";
        }
        return c;
    }
    mcpClass(m) {
        return "fd_res_mcp" + (this.state.selected?.key === m.key ? " fd_res_sel" : "");
    }
    stateClass(a) {
        return "fd_res_state fd_res_state_" + a.state;
    }
    actionLabel(kind) {
        return { stop_process: "stop", disconnect_mcp: "disconnect", reconnect_mcp: "reconnect" }[kind] || kind;
    }
    mib(bytes) {
        if (bytes === null || bytes === undefined) {
            return "?";
        }
        return (bytes / 1048576).toFixed(1);
    }
    ago(utc) {
        return ago(utc);
    }
    age(seconds) {
        return age(seconds);
    }

    async rescan() {
        try {
            await this.orm.call("flightdeck.runner", "fd_resource_scan", [this.state.runnerId]);
            await this.load();
        } catch {
            this.notification.add("Rescan could not be submitted.", { type: "danger" });
        }
    }

    async askAction(kind, row) {
        if (!this.canManage || !this.usable || !this.state.data) {
            return;
        }
        const label = this.actionLabel(kind);
        const target = row.key;
        const reason = await new Promise((resolve) => {
            const value = window.prompt(
                `Confirm ${label} on ${target} — it interrupts whatever is attached. Why?`, "");
            resolve(value);
        });
        if (reason === null) {
            return;
        }
        try {
            await this.orm.call("flightdeck.runner", "fd_propose_resource_action",
                [this.state.runnerId, kind, target, reason || ""]);
            this.load();
        } catch (e) {
            this.notification.add(e.message || "The proposal was refused.", { type: "danger" });
        }
    }
    async confirmAction(a) {
        if (!this.canManage || !this.usable || !this.state.data ||
                this.state.data.runner_id !== this.state.runnerId) {
            return;
        }
        const target = a.resource_key;
        const msg = `Confirm ${this.actionLabel(a.action_kind)} on ${target}?` +
            (a.reason ? `\nReason: ${a.reason}` : "") +
            `\nIt interrupts whatever is attached to it.`;
        if (!window.confirm(msg)) {
            return;
        }
        try {
            const res = await this.orm.call("flightdeck.runner", "fd_confirm_resource_action", [a.id]);
            if (!res?.ok) {
                this.notification.add(`Refused: ${res?.error || "not confirmed"}.`, { type: "danger" });
            }
            this.load();
        } catch (e) {
            this.notification.add(e.message || "The action could not be confirmed.", { type: "danger" });
        }
    }
    async denyAction(a) {
        try {
            await this.orm.call("flightdeck.runner", "fd_deny_resource_action", [a.id]);
            this.load();
        } catch {
            this.notification.add("Could not deny the proposal.", { type: "danger" });
        }
    }

    async sendChat() {
        const text = this.state.chatText.trim();
        if (!text || !this.canManage || this.agent.busy || this.agent.starting ||
                this.state.working || !this.state.data ||
                this.state.data.runner_id !== this.state.runnerId) {
            return;
        }
        this.state.working = true;
        try {
            const keys = this.state.selected ? [this.state.selected.key] : [];
            await this.orm.call("flightdeck.runner", "fd_resource_chat",
                [this.state.runnerId, text, keys]);
            this.state.chatText = "";
            this.load();
        } catch (e) {
            this.notification.add(e.message || "The message could not be sent.", { type: "danger" });
        } finally {
            this.state.working = false;
        }
    }
    onChatKey(ev) {
        if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) {
            ev.preventDefault();
            if (!this.state.working) {
                this.sendChat();
            }
        }
    }
}

registry.category("actions").add("flight_deck.resources", ResourcesAction);

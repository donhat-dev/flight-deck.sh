/** Orchestrator: the live Claude sessions on this machine and the agents each runs.
 *
 * One tree, laid out left to right: the Orchestrator, then every session in
 * the CLI's roster, then the agents a session started, then the agents those
 * started. Wires leave a node's right side and enter its child's left side
 * at right angles, turning in the middle of the gap between the columns, so
 * the wires from one parent share one vertical line.
 *
 * Clicking a node shows it in a side panel. The Orchestrator's panel runs its
 * relay process: start or stop it, ask it about the sessions. A session's
 * panel sends that session a message through the relay, after a confirm;
 * the relay's gate lets through only what was confirmed here.
 *
 * A session that goes from busy to idle while the page is open gets a toast
 * and a marker until it is looked at.
 */
import { Component, onMounted, onWillStart, onWillUnmount, useRef, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { deserializeDateTime, formatDateTime } from "@web/core/l10n/dates";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { useService } from "@web/core/utils/hooks";
import { AgentTranscriptDialog } from "../agent_map/agent_map";
import { modelLabel } from "../model_stats/model_stats_action";

const NODE_W = 272;
const ROOT_H = 72;
const SESSION_H = 80;
const AGENT_H = 64;
const COL_GAP = 104;
const ROW_GAP = 14;
const PAD = 40;
const REFRESH_MS = 5000;
const MIN_SCALE = 0.25;
const MAX_SCALE = 2;

const GRID = 20;
// The corner radius Workflow Studio gives its right-angled connections.
const CORNER_R = 16;
// Quiet wires first, so a live one is drawn over the vertical line it shares.
const WIRE_RANK = { busy: 2, working: 2, waiting: 2, failed: 1, stopped: 1 };

const LEGEND = [
    { key: "busy", label: "Busy" },
    { key: "idle", label: "Idle" },
    { key: "working", label: "Running" },
    { key: "waiting", label: "Waiting" },
    { key: "finished", label: "Finished" },
    { key: "failed", label: "Failed" },
];

const AGENT_STATUS = {
    working: "running", waiting: "waiting", finished: "finished", failed: "failed", stopped: "stopped",
};

function compact(n) {
    if (n >= 1000000) {
        return `${(n / 1000000).toFixed(1)}M`;
    }
    return n >= 1000 ? `${Math.round(n / 1000)}k` : `${Math.round(n)}`;
}

function duration(seconds) {
    const s = Math.round(seconds || 0);
    if (s < 60) {
        return `${s}s`;
    }
    const m = Math.floor(s / 60);
    if (m < 60) {
        return `${m}m ${s % 60}s`;
    }
    return `${Math.floor(m / 60)}h ${m % 60}m`;
}

/** "3h ago" from a naive UTC string. */
function ago(utc) {
    if (!utc) {
        return "";
    }
    const minutes = Math.max(0, Math.round((Date.now() - new Date(utc.replace(" ", "T") + "Z")) / 60000));
    if (minutes < 60) {
        return `${minutes}m ago`;
    }
    const hours = Math.round(minutes / 60);
    return hours < 48 ? `${hours}h ago` : `${Math.round(hours / 24)}d ago`;
}

/** The relay process in a word or two, for the root node and its panel. */
export function relayLabel(orch) {
    if (!orch || orch.runtime === "stopped") {
        return "stopped";
    }
    return orch.gate === "ok" ? `${orch.runtime}, gate working` : `gate ${orch.gate_label.toLowerCase()}`;
}

/** The session and agent rows as one tree of drawable nodes. */
export function buildTree(data) {
    const sessions = data.sessions.map((s) => {
        const byId = new Map(s.agents.map((a) => [a.id, { agent: a, children: [] }]));
        const tops = [];
        for (const item of byId.values()) {
            const parent = item.agent.parent_id && byId.get(item.agent.parent_id);
            (parent && parent !== item ? parent.children : tops).push(item);
        }
        const agentNode = ({ agent, children }) => ({
            key: `agent-${agent.id}`,
            kind: "agent",
            h: AGENT_H,
            label: agent.description,
            // A zero duration means no end was recorded, not an instant run.
            meta: [AGENT_STATUS[agent.status] || agent.status, agent.seconds ? duration(agent.seconds) : "",
                   agent.tokens ? `${compact(agent.tokens)} tokens` : "",
                   agent.background ? "background" : ""].filter(Boolean).join(" · "),
            status: agent.status,
            ref: { agent, sessionId: s.id },
            openable: agent.has_transcript,
            children: children.map(agentNode),
        });
        return {
            key: `session-${s.key}`,
            kind: "session",
            h: SESSION_H,
            label: s.name,
            sub: s.title,
            meta: [s.status, s.model ? modelLabel(s.model) : "", s.started_at ? `started ${ago(s.started_at)}` : ""]
                .filter(Boolean).join(" · "),
            status: s.status,
            ref: { session: s },
            children: tops.map(agentNode),
        };
    });
    return {
        key: "root",
        kind: "root",
        h: ROOT_H,
        label: "Orchestrator",
        meta: [`This machine · ${sessions.length} session${sessions.length === 1 ? "" : "s"}`,
               data.orchestrator ? `relay ${relayLabel(data.orchestrator)}` : "relay not started"].join(" · "),
        status: "root",
        children: sessions,
    };
}

/** A right-angled wire with rounded corners: out, up or down in the middle of the gap, in. */
export function wirePath(x1, y1, x2, y2) {
    const mid = x1 + (x2 - x1) / 2;
    const dy = y2 - y1;
    const r = Math.min(CORNER_R, Math.abs(dy) / 2, (x2 - x1) / 2);
    if (r < 1) {
        return `M ${x1} ${y1} L ${x2} ${y2}`;
    }
    const s = Math.sign(dy);
    return `M ${x1} ${y1} H ${mid - r} Q ${mid} ${y1} ${mid} ${y1 + s * r} `
        + `V ${y2 - s * r} Q ${mid} ${y2} ${mid + r} ${y2} H ${x2}`;
}

/** Positions for a left-to-right tree: a column per level, a row per leaf,
 * and each parent centred on the span of its children. */
export function layoutTree(root) {
    const nodes = [];
    let next = PAD;
    let depthMax = 0;
    const place = (node, depth) => {
        depthMax = Math.max(depthMax, depth);
        node.x = PAD + depth * (NODE_W + COL_GAP);
        node.w = NODE_W;
        if (!node.children.length) {
            node.y = next;
            next += node.h + ROW_GAP;
        } else {
            node.children.forEach((child) => place(child, depth + 1));
            const first = node.children[0];
            const last = node.children[node.children.length - 1];
            node.y = (first.y + first.h / 2 + last.y + last.h / 2) / 2 - node.h / 2;
        }
        nodes.push(node);
    };
    place(root, 0);
    // A parent taller than its only child sits a little above that child's row.
    const shift = Math.max(0, PAD - Math.min(...nodes.map((n) => n.y)));
    nodes.forEach((n) => (n.y += shift));
    const edges = [];
    for (const node of nodes) {
        for (const child of node.children) {
            const x1 = node.x + node.w;
            const y1 = node.y + node.h / 2;
            const x2 = child.x;
            const y2 = child.y + child.h / 2;
            edges.push({
                key: `${node.key}>${child.key}`,
                d: wirePath(x1, y1, x2, y2),
                status: child.status,
            });
        }
    }
    edges.sort((a, b) => (WIRE_RANK[a.status] || 0) - (WIRE_RANK[b.status] || 0));
    return {
        nodes,
        edges,
        width: PAD * 2 + (depthMax + 1) * NODE_W + depthMax * COL_GAP,
        height: Math.max(...nodes.map((n) => n.y + n.h)) + PAD,
    };
}

export class OrchestratorAction extends Component {
    static template = "flight_deck.OrchestratorAction";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.dialog = useService("dialog");
        this.notification = useService("notification");
        // Last seen status per session, to notice a turn ending between reads.
        this.lastStatus = null;
        this.viewRef = useRef("view");
        this.state = useState({
            data: null, error: "", scale: 1, x: 0, y: 0, dragging: false,
            selected: null, detail: null, detailError: "",
            fresh: {}, sendText: "", askText: "", working: false,
        });
        this.drag = null;
        this.onKeydown = (ev) => {
            if (ev.key === "Escape" && this.state.selected && !document.querySelector(".modal")) {
                this.closePanel();
            }
        };
        onWillStart(() => this.load());
        onMounted(() => {
            this.fit();
            window.addEventListener("keydown", this.onKeydown);
            this.timer = setInterval(() => {
                if (!document.hidden && !this.state.dragging) {
                    this.load();
                    this.loadDetail();
                }
            }, REFRESH_MS);
        });
        onWillUnmount(() => {
            clearInterval(this.timer);
            window.removeEventListener("keydown", this.onKeydown);
        });
    }

    async load() {
        try {
            const data = await this.orm.silent.call("flightdeck.session", "fd_orchestrator_tree", []);
            this.noticeFinished(data.sessions);
            this.state.data = data;
            this.state.error = "";
        } catch {
            this.state.error = "Could not read the sessions; trying again.";
        }
    }

    /** A toast and a marker for every session that went from busy to idle. */
    noticeFinished(sessions) {
        const now = Object.fromEntries(sessions.map((s) => [s.key, s.status]));
        if (this.lastStatus) {
            for (const s of sessions) {
                if (this.lastStatus[s.key] === "busy" && s.status === "idle") {
                    this.state.fresh[s.key] = true;
                    this.notification.add(`${s.name} finished a turn.`, { type: "info" });
                }
            }
        }
        this.lastStatus = now;
    }

    async loadDetail() {
        const selected = this.state.selected;
        if (!selected || selected.kind === "root") {
            return;
        }
        try {
            const detail = await this.orm.silent.call(
                "flightdeck.session", "fd_orchestrator_detail", [selected.kind, selected.id]);
            // A slower answer for a node the user has since left is dropped.
            if (this.state.selected === selected) {
                this.state.detail = detail;
                this.state.detailError = detail ? "" : "This record no longer exists.";
            }
        } catch {
            if (this.state.selected === selected) {
                this.state.detailError = "Could not read the details; trying again.";
            }
        }
    }

    get layout() {
        return layoutTree(buildTree(this.state.data || { sessions: [] }));
    }

    get summary() {
        const sessions = this.state.data?.sessions || [];
        const agents = sessions.flatMap((s) => s.agents);
        const busy = sessions.filter((s) => s.status === "busy").length;
        const count = (...statuses) => agents.filter((a) => statuses.includes(a.status)).length;
        const failed = count("failed", "stopped");
        return [`${sessions.length} sessions`, `${busy} busy`, `${count("working", "waiting")} agents running`,
                `${count("finished")} finished`, failed ? `${failed} failed or stopped` : ""]
            .filter(Boolean).join(" · ");
    }

    get legend() {
        return LEGEND;
    }

    /** The dot grid moves and scales with the plane, so panning reads as moving the canvas. */
    get viewStyle() {
        const { x, y, scale } = this.state;
        const step = GRID * scale;
        return `background-size: ${step}px ${step}px; background-position: ${x}px ${y}px;`;
    }

    get planeStyle() {
        const { x, y, scale } = this.state;
        return `transform: translate(${x}px, ${y}px) scale(${scale});`;
    }

    nodeClass(node) {
        const selected = this.state.selected?.key === node.key ? " fd_orch_selected" : "";
        const fresh = this.state.fresh[node.key.replace(/^session-/, "")] ? " fd_orch_fresh" : "";
        return `fd_orch_node fd_orch_${node.kind} fd_orch_s_${node.status}${selected}${fresh}`;
    }

    nodeStyle(node) {
        return `left: ${node.x}px; top: ${node.y}px; width: ${node.w}px; height: ${node.h}px;`;
    }

    // --- moving around ---------------------------------------------------------------
    fit() {
        const view = this.viewRef.el;
        if (!view) {
            return;
        }
        const { width, height } = this.layout;
        const scale = Math.min(Math.max(Math.min(view.clientWidth / width, view.clientHeight / height), MIN_SCALE), 1);
        Object.assign(this.state, {
            scale,
            // The tree starts at the left edge, where it is read from.
            x: 0,
            y: Math.max((view.clientHeight - height * scale) / 2, 0),
        });
    }

    zoom(factor, cx, cy) {
        const view = this.viewRef.el;
        const px = cx ?? view.clientWidth / 2;
        const py = cy ?? view.clientHeight / 2;
        const scale = Math.min(Math.max(this.state.scale * factor, MIN_SCALE), MAX_SCALE);
        const k = scale / this.state.scale;
        Object.assign(this.state, {
            scale,
            x: px - (px - this.state.x) * k,
            y: py - (py - this.state.y) * k,
        });
    }

    /** Scroll pans; with Ctrl or Cmd held it zooms around the pointer. */
    onWheel(ev) {
        ev.preventDefault();
        if (ev.ctrlKey || ev.metaKey) {
            const rect = this.viewRef.el.getBoundingClientRect();
            this.zoom(Math.exp(-ev.deltaY * 0.002), ev.clientX - rect.left, ev.clientY - rect.top);
        } else {
            this.state.x -= ev.deltaX;
            this.state.y -= ev.deltaY;
        }
    }

    onPointerDown(ev) {
        if (ev.button !== 0 || ev.target.closest(".fd_orch_node")) {
            return;
        }
        this.drag = { id: ev.pointerId, x: ev.clientX - this.state.x, y: ev.clientY - this.state.y };
        this.state.dragging = true;
        ev.currentTarget.setPointerCapture(ev.pointerId);
    }

    onPointerMove(ev) {
        if (!this.drag || ev.pointerId !== this.drag.id) {
            return;
        }
        this.state.x = ev.clientX - this.drag.x;
        this.state.y = ev.clientY - this.drag.y;
    }

    onPointerUp(ev) {
        if (this.drag && ev.pointerId === this.drag.id) {
            this.drag = null;
            this.state.dragging = false;
        }
    }

    // --- the side panel ------------------------------------------------------------
    select(node) {
        if (this.state.selected?.key === node.key) {
            this.closePanel();
            return;
        }
        if (node.kind === "session") {
            delete this.state.fresh[node.ref.session.key];
        }
        this.state.sendText = "";
        const id = { root: 0, session: node.ref?.session?.id, agent: node.ref?.agent?.id }[node.kind];
        this.state.selected = { key: node.key, kind: node.kind, id, node };
        this.state.detail = null;
        this.state.detailError = "";
        this.loadDetail();
    }

    closePanel() {
        this.state.selected = null;
        this.state.detail = null;
    }

    /** The selected node as the latest refresh drew it, so its status stays current. */
    get selectedNode() {
        const selected = this.state.selected;
        return selected && (this.layout.nodes.find((n) => n.key === selected.key) || selected.node);
    }

    get orchestrator() {
        return this.state.data?.orchestrator || null;
    }

    get canSend() {
        return !!this.state.data?.can_send;
    }

    get supervisor() {
        return this.state.data?.supervisor || null;
    }

    setKeepalive(on) {
        return this.relay("fd_orchestrator_set_keepalive", [on]);
    }

    openWatches() {
        this.action.doAction("flight_deck.action_orchestrator_watch");
    }

    openLedger() {
        this.action.doAction("flight_deck.action_orchestrator_event");
    }

    /** A new watch on the selected session, its form prefilled. */
    watchSession() {
        const node = this.selectedNode;
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "flightdeck.orchestrator.watch",
            views: [[false, "form"]],
            target: "new",
            context: {
                default_scope: "session",
                default_session_id: this.state.selected.id,
                default_name: `${node?.label || "Session"}: turn ended`,
            },
        }, { onClose: () => this.load() });
    }

    relayLabel(orch) {
        return relayLabel(orch);
    }

    /** Run one relay call; the page reloads after it, whatever it answered. */
    async relay(method, args, done) {
        this.state.working = true;
        try {
            await this.orm.call("flightdeck.session", method, args);
            done?.();
        } finally {
            this.state.working = false;
            await this.load();
            await this.loadDetail();
        }
    }

    startRelay() {
        return this.relay("fd_orchestrator_start", [], () =>
            this.notification.add("The relay is starting and testing its gate.", { type: "info" }));
    }

    stopRelay() {
        return this.relay("fd_orchestrator_stop", []);
    }

    ask() {
        const question = this.state.askText.trim();
        if (question) {
            return this.relay("fd_orchestrator_ask", [question], () => (this.state.askText = ""));
        }
    }

    /** Send only after the person has read where it goes and what it means. */
    confirmSend() {
        const text = this.state.sendText.trim();
        const node = this.selectedNode;
        if (!text || node?.kind !== "session") {
            return;
        }
        this.dialog.add(ConfirmationDialog, {
            title: `Send to ${node.label}?`,
            body: `fd-orchestrator will send this to ${node.label} exactly as written. ` +
                "It arrives as a message from another session: that session's Claude reads it " +
                "and decides whether to act on it.\n\n" + text,
            confirmLabel: "Send",
            confirm: () => this.relay("fd_orchestrator_send", [node.ref.session.id, text], () => {
                this.state.sendText = "";
                this.notification.add(`Queued for ${node.label}.`, { type: "success" });
            }),
            cancel: () => {},
        });
    }

    openLogbook() {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "flightdeck.session",
            res_id: this.state.selected.id,
            views: [[false, "form"]],
        });
    }

    async openTranscript() {
        const { agent, sessionId } = this.selectedNode.ref;
        await this.orm.call("flightdeck.session.agent", "action_load_transcript", [[agent.id]]);
        this.dialog.add(AgentTranscriptDialog, { agentId: agent.id, sessionId, title: agent.description });
    }

    when(utc) {
        return utc ? formatDateTime(deserializeDateTime(utc)) : "";
    }

    ago(utc) {
        return ago(utc);
    }

    money(value) {
        return `$${(value || 0).toFixed(2)}`;
    }

    duration(seconds) {
        return seconds ? duration(seconds) : "";
    }

    compact(n) {
        return compact(n || 0);
    }

    titleOf(node) {
        return this.state.selected?.key === node.key ? "Hide the details" : "Show the details";
    }
}

registry.category("actions").add("flight_deck.orchestrator", OrchestratorAction);

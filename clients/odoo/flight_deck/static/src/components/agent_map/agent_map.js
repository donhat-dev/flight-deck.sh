/** The agents a session spawned, drawn as the tree they were spawned in.
 *
 * Drawn down the page the way Odoo's own org chart is, because the map shares a
 * form with the transcript pane and has one narrow column to live in. The
 * session is the root and is not clickable: its transcript is already the page
 * this sits on. Only an agent opens a dialog.
 *
 * Watching only. There is no button here that reaches an agent — the session
 * that owns it is the only thing Odoo drives.
 *
 * The map starts at the session's last compact boundary, which is the rule the
 * CLI's own reader applies, so this and the terminal show the same agents.
 */
import { Component, onMounted, onWillStart, onWillUnmount, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { useService } from "@web/core/utils/hooks";
import { TranscriptBox } from "../transcript_panel/transcript_box";

const MODEL = "flightdeck.session.agent";
const SESSION_MODEL = "flightdeck.session";
const BUS_AGENTS = "flightdeck.session/agents";
const FIELDS = ["parent_agent_id", "agent_id", "description", "agent_type", "status",
                "waiting", "is_background", "duration_seconds", "total_tokens",
                "transcript_path"];
const SESSION_FIELDS = ["name", "live_model", "text_count", "runtime_state"];
// Mirrors MODELS in models/runner.py.
const MODEL_LABELS = {
    "claude-sonnet-5-5": "Sonnet 5.5", "claude-opus-5-5": "Opus 5.5", fable: "Fable 5.1",
    "claude-haiku-5-5": "Haiku 5.5", opus: "Opus (CLI alias)", sonnet: "Sonnet (CLI alias)",
    haiku: "Haiku (CLI alias)",
};

/** One agent's own transcript, in a dialog over the session. */
export class AgentTranscriptDialog extends Component {
    static template = "flight_deck.AgentTranscriptDialog";
    static components = { Dialog, TranscriptBox };
    static props = {
        agentId: Number,
        sessionId: Number,
        title: { type: String, optional: true },
        close: { type: Function, optional: true },
    };
}

export class AgentMap extends Component {
    static template = "flight_deck.AgentMap";
    static props = { resId: { type: [Number, Boolean], optional: true } };

    setup() {
        this.orm = useService("orm");
        this.dialog = useService("dialog");
        this.bus = useService("bus_service");
        this.state = useState({ agents: [], session: null, loading: true, opening: 0 });
        this.onAgent = this.onAgent.bind(this);
        onMounted(() => {
            if (this.channel) {
                this.bus.addChannel(this.channel);
                this.bus.subscribe(BUS_AGENTS, this.onAgent);
            }
        });
        onWillUnmount(() => {
            if (this.channel) {
                this.bus.unsubscribe(BUS_AGENTS, this.onAgent);
                this.bus.deleteChannel(this.channel);
            }
        });
        onWillStart(() => this.load());
    }

    get channel() {
        return this.props.resId ? `flightdeck.session_${this.props.resId}` : null;
    }

    async load() {
        if (!this.props.resId) {
            this.state.agents = [];
            this.state.loading = false;
            return;
        }
        const [rows, [session]] = await Promise.all([
            this.orm.searchRead(
                MODEL, [["session_ref_id", "=", this.props.resId]], FIELDS, { order: "id" }),
            this.orm.read(SESSION_MODEL, [this.props.resId], SESSION_FIELDS),
        ]);
        this.state.session = session || null;
        this.state.agents = rows.map((row) => ({
            id: row.id,
            parent_id: row.parent_agent_id ? row.parent_agent_id[0] : false,
            agent_id: row.agent_id || "",
            description: row.description || "Agent",
            agent_type: row.agent_type || "",
            status: row.waiting ? "waiting" : row.status,
            is_background: row.is_background,
            duration_seconds: row.duration_seconds || 0,
            total_tokens: row.total_tokens || 0,
            has_transcript: !!row.transcript_path,
        }));
        this.state.loading = false;
    }

    /** A row changed, arrived, or went away.
     *
     * A rebuild keeps the ids a browser is already showing, so the usual case
     * is an update in place. `reset` is the one case that reloads: it means the
     * map was read again from its start and the whole list should be taken
     * fresh rather than merged.
     */
    onAgent(payload) {
        if (!payload) {
            return;
        }
        if (payload.reset) {
            this.load();
            return;
        }
        if (!payload.id) {
            return;
        }
        const at = this.state.agents.findIndex((agent) => agent.id === payload.id);
        if (payload.removed) {
            if (at !== -1) {
                this.state.agents.splice(at, 1);
            }
            return;
        }
        if (at === -1) {
            this.state.agents.push({ ...payload, parent_id: payload.parent_id || false });
        } else {
            Object.assign(this.state.agents[at], payload);
        }
    }

    /** The agents a session started itself, each carrying what it started. */
    get roots() {
        const byId = new Map(this.state.agents.map((a) => [a.id, { ...a, children: [] }]));
        const roots = [];
        for (const node of byId.values()) {
            const parent = node.parent_id ? byId.get(node.parent_id) : undefined;
            if (parent && parent !== node) {
                parent.children.push(node);
            } else {
                roots.push(node);
            }
        }
        return roots;
    }

    /** The session's own card: the root the agents hang off. */
    get root() {
        const session = this.state.session;
        if (!session) {
            return { title: "This session", meta: "", status: "idle" };
        }
        const parts = [];
        if (session.live_model && session.live_model !== "default") {
            parts.push(MODEL_LABELS[session.live_model] || session.live_model);
        }
        if (session.text_count) {
            parts.push(`${this.compact(session.text_count)} turns`);
        }
        return {
            title: session.name || "This session",
            meta: parts.join(" · "),
            status: session.runtime_state === "busy" ? "working" : "finished",
        };
    }

    get counts() {
        const found = {};
        for (const agent of this.state.agents) {
            found[agent.status] = (found[agent.status] || 0) + 1;
        }
        return Object.entries(found).map(([state, n]) => `${n} ${state}`).join(" · ");
    }

    titleOf(agent) {
        return agent.has_transcript
            ? "Open the transcript this agent wrote"
            : "This agent left no transcript file";
    }

    compact(n) {
        if (n >= 1000000) {
            return `${(n / 1000000).toFixed(1)}M`;
        }
        return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : `${n}`;
    }

    /** The line under an agent's name: how long it ran, and what it spent. */
    meta(agent) {
        const parts = [];
        const seconds = Math.round(agent.duration_seconds || 0);
        if (seconds) {
            parts.push(seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`);
        }
        if (agent.total_tokens) {
            parts.push(`${this.compact(agent.total_tokens)} tokens`);
        }
        if (!parts.length && agent.agent_type) {
            parts.push(agent.agent_type);
        }
        return parts.join(" · ");
    }

    /** Read the agent's own file, then show it. The follow tick keeps the
     * running agents current; this is what catches up an agent that stopped
     * before anyone asked for it. */
    async open(agent) {
        if (!agent.has_transcript || this.state.opening) {
            return;
        }
        this.state.opening = agent.id;
        try {
            await this.orm.call(MODEL, "action_load_transcript", [[agent.id]]);
        } finally {
            this.state.opening = 0;
        }
        this.dialog.add(AgentTranscriptDialog, {
            agentId: agent.id,
            sessionId: this.props.resId,
            title: agent.description,
        });
    }
}

export class AgentMapField extends Component {
    static template = "flight_deck.AgentMapField";
    static components = { AgentMap };
    static props = { ...standardFieldProps };

    get resId() {
        return this.props.record.resId || false;
    }
}

registry.category("fields").add("fd_agent_map", {
    component: AgentMapField,
    supportedTypes: ["one2many"],
});

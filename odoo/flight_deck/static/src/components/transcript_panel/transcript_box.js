/** A session transcript as a chat box.
 *
 * One component, two mount points: the `fd_transcript` field widget in the
 * form's notebook, and the form's side pane through our own form compiler. The
 * rendering is the same in both, so the two never drift.
 *
 * The last turn is what a reader wants first, so the box opens at the end of
 * the transcript and older turns arrive by scrolling up. The scroll container
 * is `column-reverse`: rows are held newest first and painted bottom up, which
 * puts the newest at the bottom, starts the view there, and keeps the reading
 * position steady when older rows are added.
 *
 * The box owns its height and scrolls inside itself, so the form page never
 * grows to the length of a transcript.
 */
import {
    Component,
    onMounted,
    onWillStart,
    onWillUnmount,
    onWillUpdateProps,
    useExternalListener,
    useRef,
    useState,
} from "@odoo/owl";
import { deserializeDateTime, formatDateTime } from "@web/core/l10n/dates";
import { useService } from "@web/core/utils/hooks";
import { roleFilter } from "../chatter/role_filter";
import { renderMarkdown } from "./markdown";
import { rendererFor } from "./tool_renderers";

const PAGE_SIZE = 60;
const MODEL = "flightdeck.message.text";
const SESSION_MODEL = "flightdeck.session";
const BUS_APPEND = "flightdeck.transcript/append";
// A tool result lands on a later line than its call, so a turn already drawn
// has to be replaced.
const BUS_UPDATE = "flightdeck.transcript/update";
// The runner's view of the process behind this session: stopped, starting,
// idle or busy. Drives the chip in the head and the composer's placeholder.
const BUS_STATE = "flightdeck.session/state";
// A tool the permission mode did not settle: the process is parked until
// somebody answers here.
const BUS_ASK = "flightdeck.session/permission";
const BUS_DECIDED = "flightdeck.session/decision";
// The runner's coalesced snapshot of an in-progress assistant reply. Drawn as
// an unfinished line ahead of the real one, which always supersedes it.
const BUS_DELTA = "flightdeck.transcript/delta";
const DECISION_MODEL = "flightdeck.decision";
const DECISION_FIELDS = [
    "request_id", "tool_name", "tool_input", "summary", "description", "can_remember",
    "blocked_path",
];
// Mirrors PERMISSION_MODES in models/session_send.py.
const MODES = [
    ["default", "Ask me"],
    ["acceptEdits", "Accept edits"],
    ["plan", "Plan only"],
    ["auto", "Auto"],
    ["dontAsk", "Never ask"],
];
const FIELDS = ["role", "ts", "seq", "text", "tool_name", "tool_input", "tool_output",
                "tool_images", "tool_call_offset", "peer_name", "peer_mode"];
// The tool one session messages another with; a line can name several tools.
const PEER_TOOL = "SendMessage";
const PEER_DOMAIN = ["|", ["role", "=", "peer"], ["tool_name", "ilike", PEER_TOOL]];

/** A line that is part of a conversation with another session. */
function isPeerLine(line) {
    return line.role === "peer" || (line.tool_name || "").includes(PEER_TOOL);
}

// How many boxes are on each channel. The bus worker keeps no count, so the
// second box to leave a session would take the channel away from the first —
// which is what two panes on one session, or an agent dialog over a form, are.
const joined = new Map();

function joinChannel(bus, channel) {
    const held = joined.get(channel) || 0;
    joined.set(channel, held + 1);
    if (!held) {
        bus.addChannel(channel);
    }
}

function leaveChannel(bus, channel) {
    const held = joined.get(channel) || 0;
    if (held > 1) {
        joined.set(channel, held - 1);
        return;
    }
    joined.delete(channel);
    bus.deleteChannel(channel);
}

export class TranscriptBox extends Component {
    static template = "flight_deck.TranscriptBox";
    static props = {
        resId: { type: [Number, Boolean], optional: true },
        class: { type: String, optional: true },
        // The form's own record, when the box is mounted inside one. Changing
        // the permission mode writes the session, so the form has to be told
        // to re-read it or it keeps showing the old one.
        record: { type: Object, optional: true },
        // Set when the box shows one agent's own transcript instead of the
        // session's. An agent is watched, never driven, so the composer and the
        // pickers stay out of the way.
        agentId: { type: [Number, Boolean], optional: true },
    };

    setup() {
        this.orm = useService("orm");
        this.roles = useState(roleFilter);
        this.scroller = useRef("scroller");
        this.loadingOlder = false;
        // Rendered bodies are kept because a role toggle re-renders every row,
        // and parsing the same turn again is the expensive part.
        this.bodies = new Map();
        this.bus = useService("bus_service");
        this.onAppend = this.onAppend.bind(this);
        this.onUpdate = this.onUpdate.bind(this);
        this.onState = this.onState.bind(this);
        this.onAsk = this.onAsk.bind(this);
        this.onDecided = this.onDecided.bind(this);
        this.onDelta = this.onDelta.bind(this);
        this.modes = MODES;
        // The picker closes when the click lands anywhere else, the way a
        // menu does.
        useExternalListener(window, "click", (ev) => {
            if (this.state.picking && !ev.target.closest(".fd_box_picker")) {
                this.state.picking = false;
            }
        });
        // The channel the box is actually subscribed to. Not the same as
        // `channel`, which follows the props: on the pager they part company
        // for exactly as long as it takes to swap them.
        this.joined = null;
        onMounted(() => this.listen(this.channel));
        onWillUnmount(() => this.unlisten());
        this.state = useState({
            lines: [],
            total: 0,
            loading: true,
            open: {},
            draft: "",
            // The runner's latest coalesced snapshot of an in-progress reply.
            // A separate field from `draft` above, which is the person's own
            // unsent text in the composer — the two would otherwise collide
            // under one name and a streamed token would overwrite whatever
            // somebody was mid-typing.
            streamDraft: "",
            sending: false,
            error: "",
            runtime: "",
            runtimeError: "",
            mode: "",
            model: "",
            effort: "",
            models: [],
            efforts: [],
            picking: false,
            modeSaving: false,
            asks: [],
            deciding: false,
            // One turn is a prompt somebody wrote and everything the session
            // did about it. The box opens on the last one.
            turns: [],
            turnIndex: 0,
            view: "transcript",
            steps: [],
            stepsLoading: false,
            // Only the messages this session sent other sessions and received
            // from them. A conversation runs across turns, so the turn range is
            // set aside while this is on.
            peersOnly: false,
        });
        onWillStart(() => this.load());
        onWillUpdateProps((next) => {
            if (next.resId !== this.props.resId || next.agentId !== this.props.agentId) {
                return this.swap(next);
            }
        });
    }

    listen(channel) {
        if (!channel) {
            return;
        }
        this.joined = channel;
        joinChannel(this.bus, channel);
        this.bus.subscribe(BUS_APPEND, this.onAppend);
        this.bus.subscribe(BUS_UPDATE, this.onUpdate);
        this.bus.subscribe(BUS_STATE, this.onState);
        this.bus.subscribe(BUS_ASK, this.onAsk);
        this.bus.subscribe(BUS_DECIDED, this.onDecided);
        this.bus.subscribe(BUS_DELTA, this.onDelta);
    }

    unlisten() {
        if (!this.joined) {
            return;
        }
        this.bus.unsubscribe(BUS_APPEND, this.onAppend);
        this.bus.unsubscribe(BUS_UPDATE, this.onUpdate);
        this.bus.unsubscribe(BUS_STATE, this.onState);
        this.bus.unsubscribe(BUS_ASK, this.onAsk);
        this.bus.unsubscribe(BUS_DECIDED, this.onDecided);
        this.bus.unsubscribe(BUS_DELTA, this.onDelta);
        leaveChannel(this.bus, this.joined);
        this.joined = null;
    }

    /** The pager moved to another record, or an agent dialog to another agent.
     *
     * The component is kept and its props are replaced, so everything the box
     * holds is about the record it was first drawn for: the rows, the parsed
     * bodies, the expanded calls, the pending requests, and the bus channel it
     * listens on. All of it is exchanged here, and the old channel is left
     * before the new rows are asked for, so nothing from the previous session
     * can land in the list mid-load.
     */
    async swap(next) {
        this.unlisten();
        this.bodies.clear();
        this.loadingOlder = false;
        Object.assign(this.state, {
            lines: [], total: 0, open: {}, asks: [], draft: "", error: "",
            runtime: "", runtimeError: "", mode: "", model: "", effort: "",
            picking: false, turns: [], turnIndex: 0, view: "transcript", steps: [],
            streamDraft: "", peersOnly: false,
        });
        this.listen(next.resId ? `flightdeck.session_${next.resId}` : null);
        await this.load(next);
    }

    /** Enter sends; Shift+Enter is a new line, the way a chat box behaves. */
    onKeydown(ev) {
        if (ev.key === "Enter" && !ev.shiftKey) {
            ev.preventDefault();
            this.send();
        }
    }

    /** Queue one turn on the session.
     *
     * The call returns as soon as the command is written for the runner. The
     * reply is deliberately not added here: claude appends it to the transcript
     * file, the follower reads it, and it arrives over the bus with the same
     * shape as every other turn.
     */
    async send() {
        const text = this.state.draft.trim();
        if (!text || this.state.sending || !this.props.resId) {
            return;
        }
        this.state.sending = true;
        this.state.error = "";
        try {
            await this.orm.call(SESSION_MODEL, "send_message", [[this.props.resId], text]);
            this.state.draft = "";
        } catch (err) {
            // Shown in the box rather than a dialog: the reason is usually
            // something to act on (the session is open elsewhere), and the
            // message the reader just typed is still in the box.
            this.state.error = err?.data?.message || err?.message || String(err);
        } finally {
            this.state.sending = false;
        }
    }

    /** The server turns this string into the session record, after checking
     * read access on it. */
    get channel() {
        return this.props.resId ? `flightdeck.session_${this.props.resId}` : null;
    }

    /** A turn arrives. Rows are held newest first, so the front of the list is
     * the bottom of the box. */
    onAppend(payload) {
        if (!this.mine(payload)) {
            return;
        }
        if (this.state.lines.some((line) => line.id === payload.id)) {
            return;
        }
        if (this.state.peersOnly && !isPeerLine(payload)) {
            this.state.streamDraft = "";
            return;
        }
        this.state.lines = [payload, ...this.state.lines];
        this.state.total += 1;
        // The durable row always wins: whatever draft was streaming for this
        // session is now superseded by the real line that just landed.
        this.state.streamDraft = "";
    }

    /** A coalesced snapshot of an in-progress assistant reply. Overwritten,
     * never concatenated: the payload already carries the full text
     * accumulated so far, so an out-of-order or repeated one is harmless. */
    onDelta(payload) {
        if (!payload || payload.session_id !== this.props.resId) {
            return;
        }
        this.state.streamDraft = payload.text || "";
    }

    /** A tool is waiting on a person. Requests queue: the process can park
     * more than one before anybody looks. */
    onAsk(payload) {
        if (!payload || payload.session_id !== this.props.resId) {
            return;
        }
        if (this.state.asks.some((ask) => ask.id === payload.id)) {
            return;
        }
        this.state.asks = [...this.state.asks, payload];
    }

    /** Answered — by this browser, another one, or the runner's timeout. */
    onDecided(payload) {
        if (!payload || payload.session_id !== this.props.resId) {
            return;
        }
        this.state.asks = this.state.asks.filter((ask) => ask.id !== payload.id);
    }

    get ask() {
        return this.state.asks[0] || null;
    }

    /** Enter allows, Escape denies: the keys the terminal prompt uses. */
    onAskKeydown(ev) {
        if (!this.ask || this.state.deciding) {
            return;
        }
        if (ev.key === "Enter") {
            ev.preventDefault();
            this.decide("allow", "once");
        } else if (ev.key === "Escape") {
            ev.preventDefault();
            this.decide("deny");
        }
    }

    async decide(behavior, scope = "once") {
        const ask = this.ask;
        if (!ask || this.state.deciding) {
            return;
        }
        this.state.deciding = true;
        try {
            await this.orm.call(DECISION_MODEL, "decide", [[ask.id], behavior, scope]);
            this.state.asks = this.state.asks.filter((a) => a.id !== ask.id);
        } catch (err) {
            this.state.error = err?.data?.message || err?.message || String(err);
        } finally {
            this.state.deciding = false;
        }
    }

    /** Switch the permission mode, the way shift+tab does in the terminal.
     *
     * The pick is written to the session, so it survives a reload and a
     * stopped session starts in it. A running one is told over the control
     * channel and changes on its next turn.
     */
    async setMode(ev) {
        const mode = ev.target.value;
        const previous = this.state.mode;
        this.state.mode = mode;
        this.state.modeSaving = true;
        try {
            await this.orm.call(SESSION_MODEL, "action_set_mode", [[this.props.resId], mode]);
            await this.reloadRecord();
        } catch (err) {
            this.state.mode = previous;
            this.state.error = err?.data?.message || err?.message || String(err);
        } finally {
            this.state.modeSaving = false;
        }
    }

    get modelLabel() {
        const found = this.state.models.find((m) => m.value === this.state.model);
        // A session started before a model was ever picked carries none; it
        // runs on whatever the account uses, which is what Default means.
        return found ? found.label : this.state.models[0]?.label || "Default";
    }

    get effortLabel() {
        const found = this.state.efforts.find((e) => e.value === this.state.effort);
        return found ? found.label : "";
    }

    togglePicker() {
        this.state.picking = !this.state.picking;
    }

    async pickModel(value) {
        const previous = this.state.model;
        this.state.model = value;
        this.state.picking = false;
        try {
            await this.orm.call(SESSION_MODEL, "action_set_model", [[this.props.resId], value]);
            await this.reloadRecord();
        } catch (err) {
            this.state.model = previous;
            this.state.error = err?.data?.message || err?.message || String(err);
        }
    }

    /** Effort costs a turn on a running session: the CLI takes it as a
     * command, not over the control channel. */
    async pickEffort(value) {
        const previous = this.state.effort;
        this.state.effort = value;
        try {
            await this.orm.call(SESSION_MODEL, "action_set_effort", [[this.props.resId], value]);
            await this.reloadRecord();
        } catch (err) {
            this.state.effort = previous;
            this.state.error = err?.data?.message || err?.message || String(err);
        }
    }

    /** Re-read the form's record so its own fields show what just changed.
     * The box is mounted both as a field widget and as the form's side pane,
     * so the record arrives as a prop in one case and through the view's
     * model in the other. */
    async reloadRecord() {
        const record = this.props.record || this.env.model?.root;
        try {
            await record?.load?.();
        } catch {
            // A record the form has already left. Nothing to refresh.
        }
    }

    /** The process behind the session changed state. */
    onState(payload) {
        if (!payload || payload.session_id !== this.props.resId) {
            return;
        }
        this.state.runtime = payload.runtime_state || "";
        this.state.runtimeError = payload.error || "";
        if (payload.mode) {
            this.state.mode = payload.mode;
        }
        if (payload.model) {
            this.state.model = payload.model;
        }
        if (payload.effort) {
            this.state.effort = payload.effort;
        }
    }

    get placeholder() {
        if (this.state.sending) {
            return "Sending…";
        }
        if (this.state.runtime === "busy") {
            return "Claude is working… type to queue the next message.";
        }
        if (this.state.runtime === "starting") {
            return "Starting the session…";
        }
        return "Message this session…";
    }

    /** A turn already on screen gained its tool output. */
    onUpdate(payload) {
        if (!this.mine(payload)) {
            return;
        }
        const at = this.state.lines.findIndex((line) => line.id === payload.id);
        if (at === -1) {
            return;
        }
        this.bodies.delete(payload.id);
        this.state.lines = [
            ...this.state.lines.slice(0, at),
            payload,
            ...this.state.lines.slice(at + 1),
        ];
    }

    get domain() {
        return this.domainOf(this.props);
    }

    domainOf(props) {
        const scope = props.agentId
            ? [["agent_ref_id", "=", props.agentId]]
            : [["session_ref_id", "=", props.resId], ["agent_ref_id", "=", false]];
        if (this.state.peersOnly) {
            return [...scope, ...PEER_DOMAIN];
        }
        const turn = this.turn;
        if (!turn) {
            return scope;
        }
        scope.push(["seq", ">=", turn.seq_start]);
        // The newest turn has no end while the session is still writing into
        // it, so it is left open rather than cut at the last line seen.
        if (!this.onNewest) {
            scope.push(["seq", "<=", turn.seq_end]);
        }
        return scope;
    }

    /** The turn the box is showing, or nothing when it shows the lot. */
    get turn() {
        return this.state.turns.find((t) => t.index === this.state.turnIndex) || null;
    }

    get onNewest() {
        const turns = this.state.turns;
        return !!turns.length && this.state.turnIndex === turns[turns.length - 1].index;
    }

    /** The turns this transcript is made of, newest last.
     *
     * Read again whenever the picker is opened rather than on every line that
     * arrives: a turn only begins when somebody writes a prompt, and nothing
     * here should ask the server on each tool call to find that out.
     */
    async loadTurns(select, props = this.props) {
        if (!props.resId) {
            return;
        }
        this.state.turns = await this.orm.call(
            SESSION_MODEL, "fd_turns", [[props.resId], props.agentId || null]);
        if (select && this.state.turns.length) {
            this.state.turnIndex = this.state.turns[this.state.turns.length - 1].index;
        }
    }

    async pickTurn(value) {
        this.state.turnIndex = parseInt(value, 10) || 0;
        this.state.steps = [];
        await this.load();
        if (this.state.view === "gantt") {
            await this.loadSteps();
        }
    }

    turnLabel(turn) {
        // The date is the session's, not the turn's: only the clock separates
        // one turn from the next in a list this long.
        const when = (turn.ts_start || "").slice(11, 16);
        return `#${turn.index} · ${when} · ${turn.steps} steps · ${turn.prompt}`;
    }

    async showView(view) {
        this.state.view = view;
        if (view === "gantt" && !this.state.steps.length) {
            await this.loadSteps();
        }
    }

    async loadSteps() {
        const turn = this.turn;
        if (!turn) {
            this.state.steps = [];
            return;
        }
        this.state.stepsLoading = true;
        try {
            this.state.steps = await this.orm.call(
                SESSION_MODEL, "fd_turn_steps",
                [[this.props.resId], turn.seq_start, turn.seq_end,
                 this.props.agentId || null]);
        } finally {
            this.state.stepsLoading = false;
        }
    }

    /** The chart rows: where each line sits in the turn, and how wide.
     *
     * Laid out on the gaps between lines rather than on the clock, because the
     * gaps are what the file actually records. The widest few are called out.
     * Where the turn's tool calls have been read, the solid part of a bar is
     * the tool running and the rest is the model thinking or waiting; a failed
     * call is coloured by whose failure it was, and a call made before with the
     * same whole input is marked as a repeat.
     */
    get chart() {
        const steps = this.state.steps;
        const total = steps.reduce((n, step) => n + step.gap_ms, 0) || 1;
        // The few steps the turn actually went on, not merely the longest
        // three: on a turn of three steps every one of them is in the top three
        // and the call-out says nothing.
        const ranked = [...steps].sort((a, b) => b.gap_ms - a.gap_ms).slice(0, 3);
        const heavy = new Set(ranked
            .filter((step) => step.gap_ms / total >= 0.15)
            .map((step) => step.seq));
        let at = 0;
        return steps.map((step) => {
            const left = (at / total) * 100;
            at += step.gap_ms;
            const toolPct = step.tool_ms != null && step.gap_ms ? (step.tool_ms / step.gap_ms) * 100 : null;
            const title = [step.head,
                toolPct != null ? `tool ${this.spell(step.tool_ms)}, then ${this.spell(step.gap_ms - step.tool_ms)}` : "",
                step.family ? `${step.family}: ${step.error_class}` : "",
                step.error_head, step.repeat ? "Same call, same input, earlier in this turn" : ""]
                .filter(Boolean).join("\n");
            return {
                ...step,
                left,
                width: Math.max((step.gap_ms / total) * 100, 0.4),
                heavy: heavy.has(step.seq),
                spent: this.spell(step.gap_ms),
                toolPct,
                title,
            };
        });
    }

    /** What the head line counts: failures by whose they were, and repeats. */
    get chartCounts() {
        const steps = this.state.steps;
        const count = (test) => steps.filter(test).length;
        return {
            errors: count((s) => ["avoidable", "failed", "environment"].includes(s.family)),
            blocked: count((s) => s.family === "guard"),
            repeats: count((s) => s.repeat),
            read: steps.some((s) => s.tool_ms != null),
        };
    }

    get chartTotal() {
        return this.spell(this.state.steps.reduce((n, step) => n + step.gap_ms, 0));
    }

    spell(ms) {
        const seconds = ms / 1000;
        if (seconds < 60) {
            return `${seconds.toFixed(1)}s`;
        }
        return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
    }

    /** An agent's transcript is read: nothing here can send it a turn. */
    get watching() {
        return !!this.props.agentId;
    }

    /** A stored timestamp as the reader's own clock reads it.
     *
     * Both the ORM and the bus hand over the naive UTC string the column
     * holds, so a transcript printed straight from it is the only datetime in
     * Odoo that ignores the user's timezone.
     */
    stamp(value) {
        return value ? formatDateTime(deserializeDateTime(value)) : "";
    }

    /** Whether a bus payload belongs in this box. */
    mine(payload) {
        if (!payload || payload.session_id !== this.props.resId) {
            return false;
        }
        if ((payload.agent_id || false) !== (this.props.agentId || false)) {
            return false;
        }
        const turn = this.turn;
        if (!turn) {
            return true;
        }
        // A line written after the turn on screen belongs to a later one, and
        // only the newest turn is still open at its end.
        return payload.seq >= turn.seq_start
            && (this.onNewest || payload.seq <= turn.seq_end);
    }

    async load(props = this.props) {
        if (!props.resId) {
            this.state.lines = [];
            this.state.total = 0;
            this.state.loading = false;
            return;
        }
        this.state.loading = true;
        // Before the domain, because the domain is a turn's range of lines.
        if (!this.state.turns.length) {
            await this.loadTurns(true, props);
        }
        const domain = this.domainOf(props);
        const [lines, total, [session], asks, choices] = await Promise.all([
            this.orm.searchRead(MODEL, domain, FIELDS, {
                limit: PAGE_SIZE,
                offset: 0,
                order: "seq desc",
            }),
            this.orm.searchCount(MODEL, domain),
            this.orm.read(SESSION_MODEL, [props.resId],
                ["runtime_state", "runtime_error", "live_permission_mode", "live_model",
                 "live_effort"]),
            // A request parked before this box was opened is still waiting.
            this.orm.searchRead(DECISION_MODEL, [
                ["session_ref_id", "=", props.resId], ["state", "=", "pending"],
            ], DECISION_FIELDS, { order: "create_date asc" }),
            this.orm.call(SESSION_MODEL, "fd_model_choices", []),
        ]);
        this.state.lines = lines;
        this.state.total = total;
        this.state.runtime = session?.runtime_state || "";
        this.state.runtimeError = session?.runtime_error || "";
        this.state.mode = session?.live_permission_mode || "";
        this.state.model = session?.live_model || "";
        this.state.effort = session?.live_effort || "";
        this.state.models = choices.models;
        this.state.efforts = choices.efforts;
        this.state.asks = asks.map((ask) => ({ ...ask, session_id: props.resId }));
        this.state.loading = false;
    }

    /** Older turns are painted above, because the container is reversed. */
    async loadOlder() {
        if (this.loadingOlder || !this.hasOlder) {
            return;
        }
        this.loadingOlder = true;
        try {
            const older = await this.orm.searchRead(MODEL, this.domain, FIELDS, {
                limit: PAGE_SIZE,
                offset: this.state.lines.length,
                order: "seq desc",
            });
            this.state.lines = [...this.state.lines, ...older];
        } finally {
            this.loadingOlder = false;
        }
    }

    /** Near the visual top means near the far end of a reversed scroller, where
     * `scrollTop` counts away from the bottom. */
    onScroll() {
        const el = this.scroller.el;
        if (!el) {
            return;
        }
        const fromTop = el.scrollHeight - el.clientHeight - Math.abs(el.scrollTop);
        if (fromTop < 200) {
            this.loadOlder();
        }
    }

    get shown() {
        // A line arriving over the bus is not filtered by the server.
        return this.state.lines.filter((line) => this.roles[line.role] !== false
            && (!this.state.peersOnly || isPeerLine(line)));
    }

    async togglePeers() {
        this.state.peersOnly = !this.state.peersOnly;
        this.state.open = {};
        await this.load();
    }

    peerLabel(line) {
        const mode = line.peer_mode === "queued" ? " · mid-turn" : "";
        return `from ${line.peer_name || "another session"}${mode}`;
    }

    get hasOlder() {
        return this.state.lines.length < this.state.total;
    }

    /** A turn's prose is markdown; a tool call is not, and stays raw. */
    bodyOf(line) {
        if (!this.bodies.has(line.id)) {
            this.bodies.set(line.id, renderMarkdown(line.text));
        }
        return this.bodies.get(line.id);
    }

    /** The draft has no id to cache under, and is cheap enough (one short
     * snapshot, at most every 60ms) that there is nothing worth caching. */
    get streamDraftBody() {
        return renderMarkdown(this.state.streamDraft);
    }

    /** Which component draws this call's output. The box does not decide; the
     * renderer registry does, so a new output shape is a new entry there. */
    rendererOf(line) {
        return rendererFor(line);
    }

    toggleRole(role) {
        this.roles[role] = !this.roles[role];
    }

    toggleInput(id) {
        this.state.open[id] = !this.state.open[id];
    }
}

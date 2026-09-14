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
    useExternalListener,
    useRef,
    useState,
} from "@odoo/owl";
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
                "tool_images"];

export class TranscriptBox extends Component {
    static template = "flight_deck.TranscriptBox";
    static props = {
        resId: { type: [Number, Boolean], optional: true },
        class: { type: String, optional: true },
        // The form's own record, when the box is mounted inside one. Changing
        // the permission mode writes the session, so the form has to be told
        // to re-read it or it keeps showing the old one.
        record: { type: Object, optional: true },
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
        this.modes = MODES;
        // The picker closes when the click lands anywhere else, the way a
        // menu does.
        useExternalListener(window, "click", (ev) => {
            if (this.state.picking && !ev.target.closest(".fd_box_picker")) {
                this.state.picking = false;
            }
        });
        onMounted(() => {
            if (this.channel) {
                this.bus.addChannel(this.channel);
                this.bus.subscribe(BUS_APPEND, this.onAppend);
                this.bus.subscribe(BUS_UPDATE, this.onUpdate);
                this.bus.subscribe(BUS_STATE, this.onState);
                this.bus.subscribe(BUS_ASK, this.onAsk);
                this.bus.subscribe(BUS_DECIDED, this.onDecided);
            }
        });
        onWillUnmount(() => {
            if (this.channel) {
                this.bus.unsubscribe(BUS_APPEND, this.onAppend);
                this.bus.unsubscribe(BUS_UPDATE, this.onUpdate);
                this.bus.unsubscribe(BUS_STATE, this.onState);
                this.bus.unsubscribe(BUS_ASK, this.onAsk);
                this.bus.unsubscribe(BUS_DECIDED, this.onDecided);
                this.bus.deleteChannel(this.channel);
            }
        });
        this.state = useState({
            lines: [],
            total: 0,
            loading: true,
            open: {},
            draft: "",
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
        });
        onWillStart(() => this.load());
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
        if (!payload || payload.session_id !== this.props.resId) {
            return;
        }
        if (this.state.lines.some((line) => line.id === payload.id)) {
            return;
        }
        this.state.lines = [payload, ...this.state.lines];
        this.state.total += 1;
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
        if (!payload || payload.session_id !== this.props.resId) {
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
        return [["session_ref_id", "=", this.props.resId]];
    }

    async load() {
        if (!this.props.resId) {
            this.state.lines = [];
            this.state.total = 0;
            this.state.loading = false;
            return;
        }
        this.state.loading = true;
        const [lines, total, [session], asks, choices] = await Promise.all([
            this.orm.searchRead(MODEL, this.domain, FIELDS, {
                limit: PAGE_SIZE,
                offset: 0,
                order: "seq desc",
            }),
            this.orm.searchCount(MODEL, this.domain),
            this.orm.read(SESSION_MODEL, [this.props.resId],
                ["runtime_state", "runtime_error", "live_permission_mode", "live_model",
                 "live_effort"]),
            // A request parked before this box was opened is still waiting.
            this.orm.searchRead(DECISION_MODEL, [
                ["session_ref_id", "=", this.props.resId], ["state", "=", "pending"],
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
        this.state.asks = asks.map((ask) => ({ ...ask, session_id: this.props.resId }));
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
        return this.state.lines.filter((line) => this.roles[line.role] !== false);
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

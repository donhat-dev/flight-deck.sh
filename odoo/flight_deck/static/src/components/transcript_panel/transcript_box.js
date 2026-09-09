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
import { Component, onMounted, onWillStart, onWillUnmount, useRef, useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { roleFilter } from "../chatter/role_filter";
import { renderMarkdown } from "./markdown";

const PAGE_SIZE = 60;
const MODEL = "flightdeck.message.text";
const SESSION_MODEL = "flightdeck.session";
const BUS_APPEND = "flightdeck.transcript/append";
// A tool result lands on a later line than its call, so a turn already drawn
// has to be replaced.
const BUS_UPDATE = "flightdeck.transcript/update";
const FIELDS = ["role", "ts", "seq", "text", "tool_name", "tool_input", "tool_output"];

export class TranscriptBox extends Component {
    static template = "flight_deck.TranscriptBox";
    static props = {
        resId: { type: [Number, Boolean], optional: true },
        class: { type: String, optional: true },
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
        onMounted(() => {
            if (this.channel) {
                this.bus.addChannel(this.channel);
                this.bus.subscribe(BUS_APPEND, this.onAppend);
                this.bus.subscribe(BUS_UPDATE, this.onUpdate);
            }
        });
        onWillUnmount(() => {
            if (this.channel) {
                this.bus.unsubscribe(BUS_APPEND, this.onAppend);
                this.bus.unsubscribe(BUS_UPDATE, this.onUpdate);
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

    /** Run one turn on the session.
     *
     * The reply is deliberately not added to the list here. The backend appends
     * it to the transcript file, the follower reads it, and it arrives over the
     * bus with the same shape as every other turn — so a turn sent from here
     * looks identical to one sent from a terminal.
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
        const [lines, total] = await Promise.all([
            this.orm.searchRead(MODEL, this.domain, FIELDS, {
                limit: PAGE_SIZE,
                offset: 0,
                order: "seq desc",
            }),
            this.orm.searchCount(MODEL, this.domain),
        ]);
        this.state.lines = lines;
        this.state.total = total;
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

    toggleRole(role) {
        this.roles[role] = !this.roles[role];
    }

    toggleInput(id) {
        this.state.open[id] = !this.state.open[id];
    }
}

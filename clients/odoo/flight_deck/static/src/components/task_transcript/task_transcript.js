/** One task's transcript, read from the session file by the turn's offsets.
 *
 * The logbook's transcript box shows a whole session from the lines stored in
 * Odoo, which exist only for sessions someone followed. This reads the lines
 * of one turn straight from the file instead, so any task can be read, and a
 * task with subtasks gets a picker over its turns.
 */
import { Component, onWillStart, onWillUpdateProps, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardWidgetProps } from "@web/views/widgets/standard_widget_props";
import { deserializeDateTime, formatDateTime } from "@web/core/l10n/dates";
import { renderMarkdown } from "../transcript_panel/markdown";

// A user line the CLI wrote rather than a person: reminders, hooks, command
// output. Hidden until the reader asks for them.
const MACHINE = /^(<|\[Request interrupted|\[Image:|Caveat:)/;
const OUTPUT_CHARS = 4000;

export class TaskTranscript extends Component {
    static template = "flight_deck.TaskTranscript";
    static props = { ...standardWidgetProps };

    setup() {
        this.orm = useService("orm");
        this.state = useState({
            turns: [], turnId: null, lines: [], missing: false, cut: false,
            loading: false, showMachine: false, open: {},
        });
        onWillStart(() => this.loadTurns(this.props.record.resId));
        onWillUpdateProps((next) => {
            if (next.record.resId !== this.props.record.resId) {
                return this.loadTurns(next.record.resId);
            }
        });
    }

    async loadTurns(resId) {
        if (!resId) {
            return;
        }
        const data = await this.orm.call("flightdeck.turn", "fd_transcript", [[resId]]);
        this.state.turns = data.turns;
        const current = data.turns.find((t) => t.current) || data.turns[0];
        await this.pick(current ? current.id : null);
    }

    async pick(turnId) {
        this.state.turnId = turnId;
        this.state.open = {};
        if (!turnId) {
            this.state.lines = [];
            return;
        }
        this.state.loading = true;
        try {
            const data = await this.orm.call("flightdeck.turn", "fd_turn_lines", [[turnId]]);
            this.state.lines = data.lines;
            this.state.missing = data.missing;
            this.state.cut = data.cut;
        } finally {
            this.state.loading = false;
        }
    }

    onPick(ev) {
        this.pick(parseInt(ev.target.value, 10));
    }

    get rows() {
        return this.state.lines
            .map((line) => ({
                ...line,
                machine: line.role === "user" && !line.tool_name && MACHINE.test(line.text || ""),
            }))
            .filter((line) => this.state.showMachine || !line.machine);
    }

    turnLabel(turn) {
        const when = turn.started_at ? formatDateTime(deserializeDateTime(turn.started_at)) : "";
        return `${when} · ${turn.kind || "not labelled"} · ${turn.label}`;
    }

    stamp(value) {
        return value ? formatDateTime(deserializeDateTime(value.slice(0, 19).replace("T", " "))) : "";
    }

    markdown(text) {
        return renderMarkdown(text);
    }

    head(line) {
        const first = (line.tool_input || "").split("\n")[0];
        return first.length > 140 ? first.slice(0, 140) + "…" : first;
    }

    output(line) {
        const text = line.tool_output || "";
        return text.length > OUTPUT_CHARS ? text.slice(0, OUTPUT_CHARS) + "\n…" : text;
    }

    toggle(seq) {
        this.state.open[seq] = !this.state.open[seq];
    }
}

registry.category("view_widgets").add("fd_task_transcript", { component: TaskTranscript });

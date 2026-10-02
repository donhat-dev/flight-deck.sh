/** The panel above Cost per task: the figures, and the review queue.
 *
 * The queue holds the turns the label pass was not sure of, for Type, for
 * Feedback, or both. Each card says what Jev and Luna answered and takes the
 * Type in one click, or the Feedback labels with Save feedback; the list and
 * the figures read again after it, since a relabelled turn moves cost and
 * counts from one task to another.
 */
import { Component, onWillStart, onWillUpdateProps, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { ListRenderer } from "@web/views/list/list_renderer";
import { listView } from "@web/views/list/list_view";
import { deserializeDateTime, formatDateTime } from "@web/core/l10n/dates";
import { usd } from "../trends/trend_format";

const OPEN_KEY = "flight_deck.task_panel.queue_open";
// The intents a person can set; the highlighted one is the engine's answer,
// and pressing it confirms the answer.
const LABELS = [
    { kind: "task", label: "task" },
    { kind: "follow_up", label: "follow-up" },
    { kind: "ask", label: "question about the work" },
    { kind: "side", label: "side question" },
    { kind: "no_work", label: "no work" },
];

function readOpen() {
    try {
        return window.localStorage.getItem(OPEN_KEY) !== "0";
    } catch {
        return true;
    }
}

export class TaskPanel extends Component {
    static template = "flight_deck.TaskPanel";
    static props = { domain: { type: Array, optional: true } };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.labels = LABELS;
        // Feedback chips toggled on a card and not saved yet, by turn id.
        this.state = useState({ data: null, open: readOpen(), busy: null, picked: {} });
        onWillStart(() => this.load(this.props.domain));
        onWillUpdateProps((next) => {
            if (JSON.stringify(next.domain) !== JSON.stringify(this.props.domain)) {
                return this.load(next.domain);
            }
        });
    }

    async load(domain) {
        this.state.data = await this.orm.call("flightdeck.turn", "fd_task_panel", [domain || []]);
        this.state.picked = {};
    }

    typeLabel(card) {
        return LABELS.find((l) => l.kind === card.kind)?.label || "none";
    }

    picked(card) {
        return this.state.picked[card.id] || card.feedback;
    }

    toggleFeedback(card, code) {
        const now = this.picked(card);
        this.state.picked[card.id] = now.includes(code) ? now.filter((c) => c !== code) : [...now, code];
    }

    async saveFeedback(card) {
        this.state.busy = card.id;
        try {
            await this.orm.call("flightdeck.turn", "fd_set_feedback", [card.id, this.picked(card)]);
            await this.load(this.props.domain);
            await this.env.searchModel?.search();
        } finally {
            this.state.busy = null;
        }
    }

    toggle() {
        this.state.open = !this.state.open;
        try {
            window.localStorage.setItem(OPEN_KEY, this.state.open ? "1" : "0");
        } catch {
            // A browser that keeps nothing still gets a working toggle.
        }
    }

    usd(value) {
        return usd(value);
    }

    when(value) {
        return value ? formatDateTime(deserializeDateTime(value), { format: "MMM d" }) : "";
    }

    said(card) {
        const parts = [];
        if (card.llm_kind) {
            parts.push(`Luna said ${card.llm_kind} · ${Math.round(card.llm_confidence * 100)}%`);
        }
        if (card.jev_kind) {
            parts.push(`Jev said ${card.jev_kind} · ${Math.round(card.jev_top1 * 100)}%`);
        }
        return parts.join(" · ");
    }

    async setLabel(card, kind) {
        this.state.busy = card.id;
        try {
            await this.orm.call("flightdeck.turn", "fd_set_label", [card.id, kind]);
            await this.load(this.props.domain);
            await this.env.searchModel?.search();
        } finally {
            this.state.busy = null;
        }
    }

    openTurn(card) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "flightdeck.turn",
            res_id: card.id,
            views: [[false, "form"]],
        });
    }
}

export class TaskListRenderer extends ListRenderer {
    static template = "flight_deck.TaskListRenderer";
    static components = { ...ListRenderer.components, TaskPanel };
}

export const taskListView = {
    ...listView,
    Renderer: TaskListRenderer,
};

registry.category("views").add("flight_deck_task_list", taskListView);

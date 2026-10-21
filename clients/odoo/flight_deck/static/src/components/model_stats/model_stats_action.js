/** Tasks > Adjustments per task: how often a person had to redirect each model.
 *
 * Tiles compare one model with a baseline over the weeks both ran; the chart
 * follows one measure per model week by week; the table has every model in
 * the range. The figures come from `flightdeck.turn.fd_model_stats`.
 */
import { Component, onWillStart, onWillUnmount, useEffect, useRef, useState } from "@odoo/owl";
import { loadBundle } from "@web/core/assets";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { themeColors } from "../trends/trend_chart";
import { browserZone, dayLabel, usd } from "../trends/trend_format";

// Checked as a set with the dataviz palette validator (light surface). A
// model's slot is the order models were first used, so its colour stays the
// same whatever range is shown. Three slots are under 3:1 contrast; the table
// below the chart names every coloured model.
export const MODEL_COLORS = [
    "#1668e3", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948",
];

export const MEASURES = [
    { key: "adjustments", label: "Adjustments" },
    { key: "wrong_results", label: "Agent wrong" },
    { key: "revisions", label: "Revisions" },
    { key: "decisions", label: "Decisions" },
    { key: "pulls", label: "Pulls" },
    { key: "follow_ups", label: "Follow-ups" },
    { key: "steers", label: "Steer messages" },
];

const TILES = ["adjustments", "wrong_results", "revisions", "pulls"];

/** "Opus 5.5" from "claude-opus-5-5"; other names as they are. */
export function modelLabel(name) {
    if (!name) {
        return "Unknown model";
    }
    const m = /^claude-([a-z]+)-(\d+)(?:-(\d{1,2}))?(?:-\d{8})?$/.exec(name);
    if (!m) {
        return name;
    }
    const family = m[1][0].toUpperCase() + m[1].slice(1);
    return `${family} ${m[2]}${m[3] ? "." + m[3] : ""}`;
}

function perTask(value) {
    return value === null || value === undefined ? "–" : value.toFixed(2);
}

export class ModelStatsAction extends Component {
    static template = "flight_deck.ModelStatsAction";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.canvasRef = useRef("canvas");
        this.chart = null;
        this.ranges = [4, 8, 13, 26];
        this.measures = MEASURES;
        this.state = useState({ data: null, weeks: 8, model: null, baseline: null, measure: "adjustments" });
        onWillStart(async () => {
            await loadBundle("web.chartjs_lib");
            await this.load();
        });
        useEffect(() => this.draw(), () => [this.state.data, this.state.measure]);
        onWillUnmount(() => this.chart?.destroy());
    }

    async load() {
        const data = await this.orm.call("flightdeck.turn", "fd_model_stats", [
            browserZone(), this.state.weeks, this.state.model, this.state.baseline,
        ]);
        this.state.model = data.model;
        this.state.baseline = data.baseline;
        this.state.data = data;
    }

    async setWeeks(weeks) {
        this.state.weeks = weeks;
        await this.load();
    }

    async pick(which, ev) {
        this.state[which] = ev.target.value || null;
        await this.load();
    }

    openPivot() {
        this.action.doAction("flight_deck.action_adjustments");
    }

    // --- what the template reads ---------------------------------------------
    label(name) {
        return modelLabel(name);
    }

    color(row) {
        return row.slot === null || row.slot === undefined ? null : MODEL_COLORS[row.slot];
    }

    perTask(value) {
        return perTask(value);
    }

    usd(value) {
        return usd(value);
    }

    get names() {
        return (this.state.data?.models || []).map((r) => r.name).filter(Boolean);
    }

    get compareLine() {
        const c = this.state.data?.compare;
        if (!c) {
            return "Not enough tasks to compare two models in this range.";
        }
        const a = modelLabel(c.model);
        const b = modelLabel(c.baseline);
        if (!c.shared_weeks) {
            return `${a} and ${b} never ran in the same week of this range: nothing to compare.`;
        }
        const weeks = c.shared_weeks === 1 ? "the 1 week" : `the ${c.shared_weeks} weeks`;
        return `${a} against ${b}, in ${weeks} both ran: ${c.model_tasks} and ${c.baseline_tasks} tasks.`;
    }

    get tiles() {
        const c = this.state.data?.compare;
        if (!c || !c.shared_weeks) {
            return [];
        }
        return TILES.map((key) => {
            const m = c.metrics[key];
            let note = "Not clear yet: the 95% ranges overlap.";
            if (m.verdict === "fewer") {
                note = "Fewer: the 95% ranges do not overlap.";
            } else if (m.verdict === "more") {
                note = "More: the 95% ranges do not overlap.";
            } else if (m.model.lo === null || m.baseline.lo === null) {
                note = "Too few tasks for a range.";
            }
            return {
                key,
                label: MEASURES.find((x) => x.key === key).label,
                ratio: m.ratio === null ? "–" : `${m.ratio.toFixed(2)}×`,
                model: perTask(m.model.mean),
                baseline: perTask(m.baseline.mean),
                note,
            };
        });
    }

    get baselineLabel() {
        return modelLabel(this.state.data?.baseline);
    }

    rangeText(fig) {
        return fig.lo === null ? "" : `${fig.lo.toFixed(2)}–${fig.hi.toFixed(2)}`;
    }

    // --- the chart ---------------------------------------------------------------
    draw() {
        this.chart?.destroy();
        this.chart = null;
        const data = this.state.data;
        if (!data || !this.canvasRef.el) {
            return;
        }
        const key = this.state.measure;
        const theme = themeColors();
        const focus = new Set([data.model, data.baseline]);
        const datasets = [];
        for (const row of data.models) {
            const points = data.trend[row.name];
            if (!points || !points.some((p) => p[key] !== undefined)) {
                continue;
            }
            const color = MODEL_COLORS[row.slot];
            const strong = focus.has(row.name);
            datasets.push({
                type: "line",
                label: modelLabel(row.name),
                data: points.map((p) => (p[key] === undefined ? null : p[key])),
                counts: points.map((p) => p.n),
                borderColor: strong ? color : color + "73",
                backgroundColor: color,
                borderWidth: 2,
                borderDash: row.name === data.baseline ? [6, 4] : [],
                pointRadius: 4,
                pointHoverRadius: 6,
                pointBorderColor: theme.surface,
                pointBorderWidth: 2,
                spanGaps: false,
                order: strong ? 0 : 1,
            });
        }
        this.chart = new window.Chart(this.canvasRef.el, {
            data: { labels: data.weeks.map(dayLabel), datasets },
            options: {
                maintainAspectRatio: false,
                animation: false,
                interaction: { mode: "index", intersect: false },
                plugins: {
                    legend: {
                        position: "bottom",
                        labels: { color: theme.text, boxWidth: 10, usePointStyle: true, font: { size: 11 } },
                    },
                    tooltip: {
                        filter: (item) => item.parsed.y !== null,
                        callbacks: {
                            title: (items) => `Week of ${items[0]?.label || ""}`,
                            label: (ctx) => {
                                const n = ctx.dataset.counts[ctx.dataIndex];
                                return `${ctx.dataset.label}: ${ctx.parsed.y.toFixed(2)} per task (${n} tasks)`;
                            },
                        },
                    },
                },
                scales: {
                    x: { grid: { display: false },
                         ticks: { color: theme.text, maxRotation: 0, autoSkipPadding: 12, font: { size: 10 } } },
                    y: { min: 0, grid: { color: theme.grid },
                         ticks: { color: theme.text, font: { size: 10 }, callback: (v) => v.toFixed(1) } },
                },
            },
        });
    }
}

registry.category("actions").add("flight_deck.model_stats", ModelStatsAction);

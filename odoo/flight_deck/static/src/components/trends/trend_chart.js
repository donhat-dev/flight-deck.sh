/** Cost, token, work-hour and task trends: daily bars and a 7-day line.
 *
 * Six charts on one day axis, because the point of reading them together is
 * that they move differently: list prices fall, so the cost line understates
 * how much work the agents are doing, and only the token and hour lines show
 * that. The three ratio charts (cost and output per work-hour, and tasks per
 * week) turn the raw totals into a rate, which is what answers "is the work
 * getting more or less expensive" better than either total alone.
 *
 * Chart.js is Odoo's own bundle, loaded the way the graph view loads it.
 */
import { Component, onWillStart, onWillUnmount, useEffect, useRef, useState } from "@odoo/owl";
import { loadBundle } from "@web/core/assets";
import {
    COLORS, compact, count, dayLabel, hours, movingAverage, movingRatio, movingSum, usd, usdWhole,
} from "./trend_format";

export const TOKEN_MEASURES = [
    { key: "output", label: "Output" },
    { key: "cache_write", label: "Cache write" },
    { key: "input", label: "Input" },
    { key: "total", label: "Total" },
    { key: "stacked", label: "By kind" },
];

export function themeColors() {
    const style = getComputedStyle(document.body);
    const read = (name, fallback) => style.getPropertyValue(name).trim() || fallback;
    return {
        text: read("--bs-secondary-color", "#6b7280"),
        grid: read("--bs-border-color-translucent", "rgba(0,0,0,0.08)"),
        surface: read("--bs-body-bg", "#ffffff"),
    };
}

function totalTokens(row) {
    return row.output + row.input + row.cache_read + row.cache_write;
}

/** The datasets and the axis format for one chart. */
function chartSpec(kind, series, measure) {
    if (kind === "cost") {
        const values = series.map((r) => r.cost);
        return {
            format: usd,
            tick: usdWhole,
            datasets: [
                { type: "bar", label: "Daily", data: values, backgroundColor: COLORS.cost + "99", order: 2 },
                { type: "line", label: "7-day average", data: movingAverage(values),
                  borderColor: COLORS.cost, borderWidth: 2, pointRadius: 0,
                  // Monotone, so the average never swings below zero between bars.
                  cubicInterpolationMode: "monotone", order: 1 },
            ],
        };
    }
    if (kind === "hours") {
        const main = series.map((r) => r.main_hours);
        const sub = series.map((r) => r.sub_hours);
        return {
            format: hours,
            stacked: true,
            datasets: [
                { type: "bar", label: "Main agents", data: main, backgroundColor: COLORS.hours + "cc", stack: "h", order: 2 },
                { type: "bar", label: "Subagents", data: sub, backgroundColor: COLORS.subHours + "cc", stack: "h", order: 2 },
                { type: "line", label: "7-day average", data: movingAverage(main.map((v, i) => v + sub[i])),
                  borderColor: COLORS.hours, borderWidth: 2, pointRadius: 0,
                  cubicInterpolationMode: "monotone", order: 1 },
            ],
        };
    }
    if (kind === "cost_per_hour" || kind === "output_per_hour") {
        const worked = series.map((r) => r.main_hours + r.sub_hours);
        const raw = series.map((r) => (kind === "cost_per_hour" ? r.cost : r.output));
        // A day with no busy time has no rate to show, not a zero rate: leave
        // it as a gap in the bars, and drop its numerator from the 7-day sum
        // too, so the ratio never counts cost or output against hours that
        // were never tracked.
        const bars = worked.map((h, i) => (h ? raw[i] / h : null));
        const forRatio = worked.map((h, i) => (h ? raw[i] : 0));
        return {
            format: kind === "cost_per_hour" ? usd : compact,
            tick: kind === "cost_per_hour" ? usdWhole : undefined,
            datasets: [
                { type: "bar", label: "Daily", data: bars,
                  backgroundColor: (kind === "cost_per_hour" ? COLORS.cost : COLORS.tokens) + "99",
                  order: 2 },
                { type: "line", label: "7-day ratio", data: movingRatio(forRatio, worked),
                  borderColor: kind === "cost_per_hour" ? COLORS.cost : COLORS.tokens,
                  borderWidth: 2, pointRadius: 0,
                  cubicInterpolationMode: "monotone", order: 1 },
            ],
        };
    }
    if (kind === "tasks") {
        const values = series.map((r) => r.tasks);
        return {
            format: count,
            precision: 0,
            datasets: [
                { type: "bar", label: "Daily", data: values, backgroundColor: COLORS.tasks + "99", order: 2 },
                { type: "line", label: "Last 7 days", data: movingSum(values),
                  borderColor: COLORS.tasks, borderWidth: 2, pointRadius: 0,
                  cubicInterpolationMode: "monotone", order: 1 },
            ],
        };
    }
    if (measure === "stacked") {
        const bar = (label, key, color) => ({
            type: "bar", label, data: series.map((r) => r[key]), backgroundColor: color + "cc", stack: "t",
        });
        return {
            format: compact,
            stacked: true,
            datasets: [
                bar("Cache read", "cache_read", COLORS.cacheRead),
                bar("Cache write", "cache_write", COLORS.cacheWrite),
                bar("Input", "input", COLORS.input),
                bar("Output", "output", COLORS.tokens),
            ],
        };
    }
    const values = series.map((r) => (measure === "total" ? totalTokens(r) : r[measure]));
    return {
        format: compact,
        datasets: [
            { type: "bar", label: "Daily", data: values, backgroundColor: COLORS.tokens + "99", order: 2 },
            { type: "line", label: "7-day average", data: movingAverage(values),
              borderColor: COLORS.tokens, borderWidth: 2, pointRadius: 0,
              cubicInterpolationMode: "monotone", order: 1 },
        ],
    };
}

export class TrendChart extends Component {
    static template = "flight_deck.TrendChart";
    static props = {
        kind: String,
        series: Array,
        measure: { type: String, optional: true },
        height: { type: Number, optional: true },
    };
    static defaultProps = { measure: "output", height: 180 };

    setup() {
        this.canvasRef = useRef("canvas");
        this.chart = null;
        onWillStart(() => loadBundle("web.chartjs_lib"));
        useEffect(() => this.draw(), () => [this.props.series, this.props.measure]);
        onWillUnmount(() => this.chart?.destroy());
    }

    draw() {
        this.chart?.destroy();
        const spec = chartSpec(this.props.kind, this.props.series, this.props.measure);
        const theme = themeColors();
        const labels = this.props.series.map((r) => dayLabel(r.day));
        this.chart = new window.Chart(this.canvasRef.el, {
            data: { labels, datasets: spec.datasets },
            options: {
                maintainAspectRatio: false,
                animation: false,
                interaction: { mode: "index", intersect: false },
                plugins: {
                    legend: { position: "bottom", labels: { color: theme.text, boxWidth: 10, font: { size: 11 } } },
                    tooltip: { callbacks: { label: (ctx) => `${ctx.dataset.label}: ${spec.format(ctx.parsed.y)}` } },
                },
                scales: {
                    x: { stacked: !!spec.stacked, grid: { display: false },
                         ticks: { color: theme.text, maxRotation: 0, autoSkipPadding: 12, font: { size: 10 } } },
                    y: { stacked: !!spec.stacked, min: 0, grid: { color: theme.grid },
                         ticks: { color: theme.text, callback: (v) => (spec.tick || spec.format)(v),
                                  precision: spec.precision, font: { size: 10 } } },
                },
            },
        });
    }
}

/** The six charts with their range and token-measure controls. */
export class TrendPanel extends Component {
    static template = "flight_deck.TrendPanel";
    static components = { TrendChart };
    static props = {
        series: Array,
        days: Number,
        onDays: Function,
        hoursSince: { optional: true },
    };

    setup() {
        this.state = useState({ measure: "output" });
        this.measures = TOKEN_MEASURES;
        this.ranges = [14, 30, 90];
    }

    get totals() {
        const sum = (key) => this.props.series.reduce((acc, r) => acc + (r[key] || 0), 0);
        const output = sum("output");
        const workedHours = sum("main_hours") + sum("sub_hours");
        // Same rule as the per-day bars: a day with no busy time drops out of
        // both sides of the ratio, so it never drags the total rate down.
        const costWorked = this.props.series.reduce(
            (acc, r) => acc + (r.main_hours + r.sub_hours ? r.cost : 0), 0);
        const outputWorked = this.props.series.reduce(
            (acc, r) => acc + (r.main_hours + r.sub_hours ? r.output : 0), 0);
        return {
            cost: usd(sum("cost")),
            output: compact(output),
            hours: hours(workedHours),
            costPerHour: usd(workedHours ? costWorked / workedHours : 0),
            outputPerHour: compact(workedHours ? outputWorked / workedHours : 0),
            tasks: count(sum("tasks")),
        };
    }

    get hoursNote() {
        if (!this.props.hoursSince) {
            return "No busy time read yet.";
        }
        const since = this.props.hoursSince.slice(0, 10);
        return `Known from ${since}: older transcripts are no longer on disk.`;
    }
}

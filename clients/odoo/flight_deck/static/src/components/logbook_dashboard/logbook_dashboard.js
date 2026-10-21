/** The panel above the logbook: value, work-hours and quota at a glance.
 *
 * Three ways to show it, chosen by the reader and remembered in this browser:
 * a one-line strip for when the list is what matters, cards with today, this
 * week and this month side by side, and the cards with the trend charts under
 * them. A period cell filters the logbook to the sessions written in it, the
 * way the purchase dashboard's cards filter orders.
 */
import { Component, onMounted, onWillStart, onWillUnmount, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { ListRenderer } from "@web/views/list/list_renderer";
import { listView } from "@web/views/list/list_view";
import { TrendPanel } from "../trends/trend_chart";
import {
    COLORS, browserZone, compact, delta, hours, pct, until, usd,
} from "../trends/trend_format";

const MODE_KEY = "flight_deck.logbook_dashboard.mode";
const DAYS_KEY = "flight_deck.logbook_dashboard.days";
const MODES = [
    { key: "compact", label: "Strip", icon: "fa-minus" },
    { key: "cards", label: "Cards", icon: "fa-th-large" },
    { key: "charts", label: "Cards and charts", icon: "fa-area-chart" },
];
// The panel reads the ledger again this often while it is on screen.
const REFRESH_MS = 60 * 1000;
const PERIODS = [
    { key: "today", label: "Today", against: "yesterday", filter: "written_today" },
    { key: "week", label: "This week", against: "last week", filter: "written_week" },
    { key: "month", label: "This month", against: "last month", filter: "written_month" },
];
const SPARK_DAYS = 14;

function readPref(key, fallback) {
    try {
        return window.localStorage.getItem(key) || fallback;
    } catch {
        return fallback;
    }
}

function writePref(key, value) {
    try {
        window.localStorage.setItem(key, value);
    } catch {
        // A private window or blocked storage: the choice lasts this page only.
    }
}

/** An SVG polyline for the last `SPARK_DAYS` values, scaled to 100 x 28. */
function sparkPath(values) {
    const tail = values.slice(-SPARK_DAYS);
    const max = Math.max(...tail, 0);
    if (!tail.length || !max) {
        return "";
    }
    const step = tail.length > 1 ? 100 / (tail.length - 1) : 0;
    return tail.map((v, i) => `${(i * step).toFixed(1)},${(27 - (v / max) * 25).toFixed(1)}`).join(" ");
}

export class LogbookDashboard extends Component {
    static template = "flight_deck.LogbookDashboard";
    static components = { TrendPanel };
    static props = {};

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.modes = MODES;
        this.periods = PERIODS;
        this.colors = COLORS;
        this.state = useState({
            data: null,
            error: false,
            mode: readPref(MODE_KEY, "cards"),
            days: Number(readPref(DAYS_KEY, "30")) || 30,
            refreshing: false,
        });
        onWillStart(() => this.load());
        onMounted(() => {
            this.timer = setInterval(() => this.load(), REFRESH_MS);
        });
        onWillUnmount(() => {
            clearInterval(this.timer);
            clearTimeout(this.quotaTimer);
        });
    }

    async load() {
        try {
            this.state.data = await this.orm.call(
                "flightdeck.session", "fd_dashboard", [browserZone(), this.state.days]);
            this.state.error = false;
        } catch {
            // A failed refresh keeps the last figures on screen; the next one
            // tries again.
            this.state.error = true;
        }
    }

    setMode(mode) {
        this.state.mode = mode;
        writePref(MODE_KEY, mode);
    }

    async setDays(days) {
        this.state.days = days;
        writePref(DAYS_KEY, String(days));
        await this.load();
    }

    /** Replace the logbook's filters with the one for this period. */
    applyPeriod(period) {
        const searchModel = this.env.searchModel;
        if (!searchModel) {
            return;
        }
        const items = searchModel.getSearchItems((item) => item.name === period.filter);
        searchModel.query = [];
        for (const item of items) {
            searchModel.toggleSearchItem(item.id);
        }
    }

    async refreshQuota() {
        this.state.refreshing = true;
        try {
            const asked = await this.orm.call("flightdeck.runner", "fd_refresh_quota", []);
            if (!asked) {
                this.notification.add("No runner is set to report the quota.", { type: "warning" });
                this.state.refreshing = false;
                return;
            }
            // The report comes back through the runner's events, read every
            // minute; look again once it has had time to land.
            clearTimeout(this.quotaTimer);
            this.quotaTimer = setTimeout(async () => {
                await this.load();
                this.state.refreshing = false;
            }, 20000);
        } catch (e) {
            this.state.refreshing = false;
            throw e;
        }
    }

    // --- what the template reads ------------------------------------------
    get ready() {
        return !!this.state.data;
    }

    cell(periodKey) {
        const { current, previous } = this.state.data.periods[periodKey];
        return {
            cost: usd(current.cost),
            costDelta: delta(current.cost, previous.cost),
            hours: hours(current.hours),
            hoursDelta: delta(current.hours, previous.hours),
            subShare: current.hours ? current.sub_hours / current.hours : 0,
            output: compact(current.output),
            outputDelta: delta(current.output, previous.output),
            calls: current.calls,
        };
    }

    deltaText(value, against) {
        return value === null ? `nothing ${against}` : `${pct(value)} vs ${against}`;
    }

    deltaClass(value) {
        if (value === null || Math.abs(value) < 0.05) {
            return "text-muted";
        }
        return value > 0 ? "fd_up" : "fd_down";
    }

    spark(key) {
        const series = this.state.data.series;
        if (key === "hours") {
            return sparkPath(series.map((r) => r.main_hours + r.sub_hours));
        }
        return sparkPath(series.map((r) => r[key]));
    }

    get quota() {
        const q = this.state.data.quota;
        if (!q) {
            return null;
        }
        const bar = (label, pctValue, resetsAt) => ({
            label, pct: Math.min(100, Math.max(0, pctValue || 0)), resets: until(resetsAt),
            level: pctValue >= 90 ? "bg-danger" : pctValue >= 70 ? "bg-warning" : "bg-primary",
        });
        return {
            bars: [
                bar("Session (5 h)", q.session.pct, q.session.resets_at),
                bar("Week, all models", q.week.pct, q.week.resets_at),
                ...q.models.map((m) => bar(`Week, ${m.label}`, m.pct, m.resets_at)),
            ],
            age: q.age_minutes < 1 ? "just now" : `${Math.round(q.age_minutes)} min ago`,
            stale: q.stale,
            spark: sparkPath(q.history.map((h) => h.session)),
        };
    }

    get strip() {
        const today = this.cell("today");
        const week = this.cell("week");
        const month = this.cell("month");
        const q = this.state.data.quota;
        return {
            cost: `${today.cost} today · ${week.cost} week · ${month.cost} month`,
            hours: `${today.hours} today · ${week.hours} week · ${month.hours} month`,
            quota: q ? `session ${Math.round(q.session.pct)}% · week ${Math.round(q.week.pct)}%` : "no report",
        };
    }
}

export class LogbookListRenderer extends ListRenderer {
    static template = "flight_deck.LogbookListRenderer";
    static components = { ...ListRenderer.components, LogbookDashboard };
}

export const logbookListView = {
    ...listView,
    Renderer: LogbookListRenderer,
};

registry.category("views").add("flight_deck_logbook_list", logbookListView);

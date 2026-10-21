/** Number formats and colours shared by the logbook panel and the trend charts. */

export const COLORS = {
    cost: "#1668e3",
    tokens: "#0d9488",
    cacheRead: "#94a3b8",
    cacheWrite: "#6366f1",
    input: "#f59e0b",
    hours: "#d97706",
    subHours: "#fbbf24",
    tasks: "#a855f7",
};

const usdFmt = new Intl.NumberFormat("en-US", {
    style: "currency", currency: "USD", maximumFractionDigits: 0,
});
const usdSmall = new Intl.NumberFormat("en-US", {
    style: "currency", currency: "USD", maximumFractionDigits: 2,
});
const compactFmt = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });

export function usd(value) {
    const v = value || 0;
    return Math.abs(v) < 100 ? usdSmall.format(v) : usdFmt.format(v);
}

/** Whole dollars, for an axis where cents would only add noise. */
export function usdWhole(value) {
    return usdFmt.format(value || 0);
}

export function compact(value) {
    return compactFmt.format(value || 0);
}

const countFmt = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });

/** A plain whole-number count, for tasks. */
export function count(value) {
    return countFmt.format(value || 0);
}

/** 5.4 h as "5h 24m"; under an hour as "24m". */
export function hours(value) {
    const minutes = Math.round((value || 0) * 60);
    const h = Math.floor(minutes / 60);
    const m = minutes % 60;
    if (!h) {
        return `${m}m`;
    }
    return m ? `${h}h ${m}m` : `${h}h`;
}

/** Change against the previous period, or null when there is nothing to compare. */
export function delta(current, previous) {
    if (!previous) {
        return null;
    }
    return (current - previous) / previous;
}

export function pct(value) {
    const v = Math.round((value || 0) * 100);
    return `${v > 0 ? "+" : ""}${v}%`;
}

/** "in 1h 12m" until a naive UTC string from the server; "" when past or unknown. */
export function until(utcString) {
    if (!utcString) {
        return "";
    }
    const target = new Date(utcString.replace(" ", "T") + "Z");
    const minutes = Math.round((target - Date.now()) / 60000);
    if (minutes <= 0) {
        return "";
    }
    const d = Math.floor(minutes / 1440);
    const h = Math.floor((minutes % 1440) / 60);
    const m = minutes % 60;
    if (d) {
        return `in ${d}d ${h}h`;
    }
    return h ? `in ${h}h ${m}m` : `in ${m}m`;
}

/** Trailing mean over `size` points; the first points average what they have. */
export function movingAverage(values, size = 7) {
    const out = [];
    let sum = 0;
    for (let i = 0; i < values.length; i++) {
        sum += values[i];
        if (i >= size) {
            sum -= values[i - size];
        }
        out.push(sum / Math.min(i + 1, size));
    }
    return out;
}

/** Trailing sum over `size` points; the first points sum what they have.
 *
 * Each window is summed afresh: a running add-and-subtract leaves a float
 * residue after a gap, and `movingRatio` would divide by it. */
export function movingSum(values, size = 7) {
    return values.map((_, i) =>
        values.slice(Math.max(0, i - size + 1), i + 1).reduce((acc, v) => acc + v, 0));
}

/** Trailing ratio over `size` points: the sum of numerators divided by the sum
 * of denominators over the same window, not the average of daily ratios (a
 * near-zero denominator on one day would otherwise dominate). `null` where the
 * window's denominator sum is 0. */
export function movingRatio(numerators, denominators, size = 7) {
    const nums = movingSum(numerators, size);
    const dens = movingSum(denominators, size);
    return nums.map((n, i) => (dens[i] ? n / dens[i] : null));
}

/** "Sep 3" from "2026-09-03". */
export function dayLabel(iso) {
    const [y, m, d] = iso.split("-").map(Number);
    return new Date(y, m - 1, d).toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

/** The browser's own zone, which is what "today" means to the person reading. */
export function browserZone() {
    try {
        return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
    } catch {
        return "UTC";
    }
}

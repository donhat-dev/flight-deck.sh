"""Adjustments per task by model: the figures behind Tasks > Adjustments per task.

Free of Odoo. A task comes in as a dict with its local `week` (the Monday),
`model`, `cost` and the counts in METRICS; everything is a mean per task.

Two models are compared only over the weeks both ran: the tasks a person
gives an agent change from week to week, so a model's all-time rate against
another's one week would compare the weeks as much as the models.
"""
import math
from datetime import timedelta

METRICS = ("adjustments", "wrong_results", "revisions", "decisions", "pulls", "follow_ups", "steers")
# A model's week with fewer tasks is a gap in its line: two or three tasks
# swing a mean too far to read as a trend.
WEEK_FLOOR = 5
# Tasks a model needs, ever, for a colour and a line of its own. Models are
# coloured in the order they were first used, so a colour stays with its model
# whatever range is shown; fewer tasks stay in the table.
CHART_FLOOR = 5
PALETTE_SIZE = 8
# The table marks a model with fewer tasks in the range, and the page does not
# pick it as the model to compare by default.
FEW_TASKS = 10
Z95 = 1.96


def week_of(day):
    return day - timedelta(days=day.weekday())


def mean_range(total, squares, n):
    """Mean per task and a rough 95% range (normal approximation).

    No range under two tasks. The lower end is cut at 0: a count per task is
    never negative. A few tasks that happen to agree would give a range of
    width 0, so the variance is at least the mean (what a Poisson count would
    have), and a run of zeros still leaves room up to 3/n (the rule of three).
    """
    if not n:
        return {"mean": None, "lo": None, "hi": None}
    mean = total / n
    if n < 2:
        return {"mean": mean, "lo": None, "hi": None}
    var = max(max(squares - total * total / n, 0.0) / (n - 1), mean)
    half = Z95 * math.sqrt(var / n)
    return {"mean": mean, "lo": max(mean - half, 0.0), "hi": mean + half if total else 3 / n}


def _add(acc, task):
    acc["n"] += 1
    acc["cost"] += task.get("cost") or 0.0
    for m in METRICS:
        v = task.get(m) or 0
        acc["sum"][m] += v
        acc["sq"][m] += v * v


def _empty():
    return {"n": 0, "cost": 0.0, "sum": dict.fromkeys(METRICS, 0), "sq": dict.fromkeys(METRICS, 0)}


def _figures(acc):
    return {m: mean_range(acc["sum"][m], acc["sq"][m], acc["n"]) for m in METRICS}


def colour_slots(first_seen, counts):
    """Palette slot per model: first used first, among models with enough tasks ever."""
    ranked = sorted((name for name in first_seen if counts.get(name, 0) >= CHART_FLOOR),
                    key=lambda name: (first_seen[name], name))
    return {name: i for i, name in enumerate(ranked[:PALETTE_SIZE])}


def verdict(model, baseline):
    """"fewer" or "more" when the two 95% ranges do not overlap, else "unclear"."""
    if None in (model["lo"], model["hi"], baseline["lo"], baseline["hi"]):
        return "unclear"
    if model["hi"] < baseline["lo"]:
        return "fewer"
    if model["lo"] > baseline["hi"]:
        return "more"
    return "unclear"


def compare(tasks, model, baseline):
    """`model` against `baseline`, over the weeks both have tasks."""
    weeks_of = {}
    for t in tasks:
        weeks_of.setdefault(t["model"], set()).add(t["week"])
    shared = weeks_of.get(model, set()) & weeks_of.get(baseline, set())
    accs = {model: _empty(), baseline: _empty()}
    for t in tasks:
        if t["week"] in shared and t["model"] in accs:
            _add(accs[t["model"]], t)
    a, b = _figures(accs[model]), _figures(accs[baseline])
    metrics = {}
    for m in METRICS:
        am, bm = a[m]["mean"], b[m]["mean"]
        metrics[m] = {
            "model": a[m], "baseline": b[m],
            "ratio": am / bm if am is not None and bm else None,
            "verdict": verdict(a[m], b[m]),
        }
    return {
        "model": model, "baseline": baseline, "shared_weeks": len(shared),
        "model_tasks": accs[model]["n"], "baseline_tasks": accs[baseline]["n"],
        "metrics": metrics,
    }


def summarize(tasks, weeks, slots, model=None, baseline=None, first_used=None):
    """The page's figures for the tasks of the weeks shown.

    `first_used` is each model's first task ever, as a local date: the table
    shows it so a reader sees how long two models overlapped.
    """
    first_used = first_used or {}
    per_model, per_week = {}, {}
    for t in tasks:
        acc = per_model.setdefault(t["model"], dict(_empty(), weeks=set(), first=t["week"]))
        _add(acc, t)
        acc["weeks"].add(t["week"])
        acc["first"] = min(acc["first"], t["week"])
        _add(per_week.setdefault((t["model"], t["week"]), _empty()), t)
    names = sorted(per_model, key=lambda name: (-per_model[name]["n"], name))

    if model not in per_model:
        model = None
    if baseline not in per_model:
        baseline = None
    if not model:
        # The newest model with enough tasks: the one a person is trying out.
        enough = [n for n in names if per_model[n]["n"] >= FEW_TASKS and n]
        model = max(enough, key=lambda n: (first_used.get(n) or per_model[n]["first"], per_model[n]["n"]),
                    default=None)
    if not baseline:
        baseline = next((n for n in names if n != model and n), None)

    rows = []
    for name in names:
        acc = per_model[name]
        vs = compare(tasks, name, baseline) if baseline and name != baseline else None
        rows.append({
            "name": name,
            "slot": slots.get(name),
            "tasks": acc["n"],
            "weeks": len(acc["weeks"]),
            "first": (first_used.get(name) or acc["first"]).isoformat(),
            "cost": acc["cost"] / acc["n"],
            "metrics": _figures(acc),
            "vs_baseline": vs and {
                "ratio": vs["metrics"]["adjustments"]["ratio"],
                "shared_weeks": vs["shared_weeks"],
            },
        })

    trend = {}
    for name in names:
        if slots.get(name) is None:
            continue
        points = []
        for week in weeks:
            acc = per_week.get((name, week))
            if not acc or acc["n"] < WEEK_FLOOR:
                points.append({"n": acc["n"] if acc else 0})
                continue
            point = {"n": acc["n"]}
            point.update({m: acc["sum"][m] / acc["n"] for m in METRICS})
            points.append(point)
        trend[name] = points

    return {
        "weeks": [w.isoformat() for w in weeks],
        "models": rows,
        "trend": trend,
        "model": model,
        "baseline": baseline,
        "compare": compare(tasks, model, baseline) if model and baseline else None,
        "week_floor": WEEK_FLOOR,
        "few_tasks": FEW_TASKS,
    }

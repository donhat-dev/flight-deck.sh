"""Every /usage reading found in transcripts, plus Odoo snapshots -> readings.json."""
import glob, json, re
from datetime import datetime
from zoneinfo import ZoneInfo
TZ = ZoneInfo("Asia/Ho_Chi_Minh")
PAT = {
 "session": re.compile(r"Current session: (\d+(?:\.\d+)?)% used · resets ([A-Z][a-z]{2} \d+, \d+(?::\d+)?[ap]m)"),
 "week": re.compile(r"Current week \(all models\): (\d+(?:\.\d+)?)% used · resets ([A-Z][a-z]{2} \d+, \d+(?::\d+)?[ap]m)"),
 "fable": re.compile(r"Current week \(Fable\): (\d+(?:\.\d+)?)% used · resets ([A-Z][a-z]{2} \d+, \d+(?::\d+)?[ap]m)"),
}
def parse_reset(txt, ts):
    fmt = "%Y %b %d, %I:%M%p" if ":" in txt.split(", ")[1] else "%Y %b %d, %I%p"
    d = datetime.strptime(f"{ts.year} {txt}", fmt).replace(tzinfo=TZ)
    if d < ts.astimezone(TZ).replace(tzinfo=TZ) and (ts - d).days > 200:
        d = d.replace(year=d.year + 1)
    return d
out = {}
for f in glob.glob("/home/nathando/.claude/projects/*/*.jsonl"):
    for line in open(f, errors="ignore"):
        if "Current week (all models)" not in line: continue
        try: o = json.loads(line)
        except Exception: continue
        ts_s = o.get("timestamp")
        if not ts_s: continue
        blob = json.dumps(o, ensure_ascii=False).replace("\\n", "\n")
        m = PAT["week"].search(blob)
        if not m: continue
        ts = datetime.fromisoformat(ts_s.replace("Z", "+00:00"))
        r = {"ts": ts.isoformat(), "week": float(m.group(1)), "week_reset": parse_reset(m.group(2), ts).isoformat()}
        for k in ("session", "fable"):
            mm = PAT[k].search(blob)
            if mm: r[k] = float(mm.group(1)); r[k + "_reset"] = parse_reset(mm.group(2), ts).isoformat()
        out[ts_s[:19]] = r
rows = sorted(out.values(), key=lambda r: r["ts"])
json.dump(rows, open("readings.json", "w"), indent=0)
import collections
by = collections.Counter(r["week_reset"][:16] for r in rows)
print(len(rows), "readings"); [print(k, v) for k, v in sorted(by.items())]
print(rows[0]["ts"], rows[-1]["ts"])

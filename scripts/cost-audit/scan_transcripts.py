"""Scan Claude Code transcripts and compare dedupe keys for usage accounting (read-only)."""
import glob, json, os, sys, collections
roots = sys.argv[1:] or [os.path.expanduser("~/.claude/projects")]
recs = {}          # uuid -> record (FlightDeck's key)
by_msg = {}        # (message.id, requestId) -> record (first seen)
msg_files = collections.defaultdict(set)
uuid_per_msg = collections.defaultdict(set)
usage_keys = collections.Counter(); speeds = collections.Counter(); tiers = collections.Counter()
files = 0; lines = 0; no_id = 0; sidechain = 0
for root in roots:
    for path in glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True):
        files += 1
        with open(path, "rb") as fh:
            for raw in fh:
                if not raw.endswith(b"\n"): break
                try: o = json.loads(raw.decode("utf-8", "replace"))
                except Exception: continue
                if o.get("type") != "assistant": continue
                m = o.get("message") or {}; u = m.get("usage")
                if not u: continue
                lines += 1
                usage_keys.update(u.keys()); speeds[u.get("speed")] += 1; tiers[u.get("service_tier")] += 1
                cc = u.get("cache_creation") or {}
                r = dict(model=m.get("model"), ts=o.get("timestamp"), side=bool(o.get("isSidechain")),
                         t=(u.get("input_tokens", 0) or 0, u.get("cache_read_input_tokens", 0) or 0,
                            cc.get("ephemeral_5m_input_tokens", 0) or 0, cc.get("ephemeral_1h_input_tokens", 0) or 0,
                            u.get("output_tokens", 0) or 0), speed=u.get("speed"))
                if o.get("uuid") and o["uuid"] not in recs: recs[o["uuid"]] = r
                mid, rid = m.get("id"), o.get("requestId")
                if not mid: no_id += 1; continue
                k = (mid, rid)
                msg_files[k].add(path); uuid_per_msg[k].add(o.get("uuid"))
                prev = by_msg.get(k)
                # keep the line with the largest output (streamed lines may carry partial usage)
                if prev is None or r["t"][4] > prev["t"][4]: by_msg[k] = r
def tot(rs, model_prefix="claude-"):
    s = [0]*5; n = 0
    for r in rs:
        if not (r["model"] or "").startswith(model_prefix): continue
        n += 1
        for i in range(5): s[i] += r["t"][i]
    return n, s
print("files", files, "assistant usage lines", lines, "lines without message.id", no_id)
print("usage keys", usage_keys.most_common(20)); print("speed", speeds.most_common()); print("tier", tiers.most_common())
for label, rs in (("by uuid (FlightDeck)", recs.values()), ("by message.id+requestId", by_msg.values())):
    n, s = tot(rs); print(f"{label:26s} n={n:,} in={s[0]:,} cr={s[1]:,} cw5={s[2]:,} cw1={s[3]:,} out={s[4]:,}")
multi_uuid = sum(1 for k, v in uuid_per_msg.items() if len(v) > 1)
multi_file = sum(1 for k, v in msg_files.items() if len(v) > 1)
print("messages with >1 uuid:", multi_uuid, "of", len(uuid_per_msg), "| messages seen in >1 file:", multi_file)
json.dump({"by_msg": [(k[0], k[1], v["model"], v["ts"], v["side"], v["t"], v["speed"]) for k, v in by_msg.items()]},
          open(os.environ.get("OUT", "/dev/null"), "w"))

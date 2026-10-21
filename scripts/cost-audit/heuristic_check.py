"""On the on-disk corpus: does the ledger-reproducible key (session, model, input-side tuple; max output)
give the same token totals as the true key (message.id, requestId; max output)?"""
import glob, json, os
root = os.path.expanduser("~/.claude/projects")
true, heur, byuuid, glob_ = {}, {}, {}, {}
for path in glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True):
    with open(path, "rb") as fh:
        for raw in fh:
            if not raw.endswith(b"\n"): break
            try: o = json.loads(raw.decode("utf-8", "replace"))
            except Exception: continue
            if o.get("type") != "assistant": continue
            m = o.get("message") or {}; u = m.get("usage")
            if not u or not (m.get("model") or "").startswith("claude-"): continue
            cc = u.get("cache_creation") or {}
            t = (u.get("input_tokens",0) or 0, u.get("cache_read_input_tokens",0) or 0, cc.get("ephemeral_5m_input_tokens",0) or 0, cc.get("ephemeral_1h_input_tokens",0) or 0, u.get("output_tokens",0) or 0)
            gk = ((m.get("model"),) + t[:4]) if t[1] + t[2] + t[3] >= 10000 else ((o.get("sessionId"), m.get("model")) + t[:4])
            for d, k in ((true, (m.get("id"), o.get("requestId"))), (heur, (o.get("sessionId"), m.get("model")) + t[:4]), (glob_, gk)):
                if k not in d or t[4] > d[k][4]: d[k] = t
            byuuid.setdefault(o.get("uuid"), t)
def s(d): return [sum(v[i] for v in d.values()) for i in range(5)]
for n, d in (("uuid", byuuid), ("true", true), ("heuristic", heur), ("global", glob_)):
    print(f"{n:10s} n={len(d):,}", [f"{x:,}" for x in s(d)])
st, sh = s(true), s(heur)
print("heuristic/true per class:", [round(b/a, 4) if a else None for a, b in zip(st, sh)])

sg = s(glob_); print("global/true per class:", [round(b/a, 4) if a else None for a, b in zip(st, sg)])

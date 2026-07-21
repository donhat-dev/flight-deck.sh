import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";

/* ---- Diff instrument: a local, read-only git diff viewer inside FlightDeck.
   Idea adopted from the one-off deliverable-vs-odoo12CE_legal.html artifact.
   Backend endpoints (backend/flightdeck/repodiff.py) only ever run allowlisted git;
   the sole network ops are fetch/pull. ---------------------------------- */

const esc = (s) => String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));

async function rget(path) {
  const r = await fetch(path);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || j.error || `HTTP ${r.status}`);
  return j;
}
async function rpost(path, body) {
  const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || j.error || `HTTP ${r.status}`);
  return j;
}

// Parse a unified `git diff` for ONE file into a side-by-side HTML table.
// Consecutive '-'/'+' runs are paired index-by-index (GitHub-style); leftovers
// render one-sided against a blank cell.
function sideBySideHTML(diff) {
  const lines = diff.split("\n");
  let i = 0, ln = 0, rn = 0;
  const rows = [];
  const push = (cls, lnum, ltxt, rnum, rtxt) => {
    rows.push(
      `<tr class="${cls}">` +
      `<td class="num l">${lnum ?? ""}</td><td class="ln l${ltxt === null ? " blank" : ""}">${ltxt === null ? "" : esc(ltxt)}</td>` +
      `<td class="num r sep">${rnum ?? ""}</td><td class="ln r${rtxt === null ? " blank" : ""}">${rtxt === null ? "" : esc(rtxt)}</td></tr>`
    );
  };
  while (i < lines.length && !lines[i].startsWith("@@")) i++;
  for (; i < lines.length; i++) {
    const l = lines[i];
    if (l.startsWith("@@")) {
      const m = l.match(/@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/);
      if (m) { ln = +m[1] - 1; rn = +m[2] - 1; }
      rows.push(`<tr class="hunkrow"><td colspan="4">${esc(l)}</td></tr>`);
      continue;
    }
    if (l.startsWith("\\")) continue;
    if (l[0] === "-" || l[0] === "+") {
      const dels = [], adds = [];
      while (i < lines.length && (lines[i][0] === "-" || lines[i][0] === "+")) {
        (lines[i][0] === "-" ? dels : adds).push(lines[i].slice(1));
        i++;
      }
      i--;
      const n = Math.max(dels.length, adds.length);
      for (let k = 0; k < n; k++) {
        const d = dels[k], a = adds[k];
        if (d !== undefined && a !== undefined) push("pair", ++ln, d, ++rn, a);
        else if (d !== undefined) push("del", ++ln, d, null, null);
        else push("add", null, null, ++rn, a);
      }
      continue;
    }
    if (l[0] === " ") { push("ctx", ++ln, l.slice(1), ++rn, l.slice(1)); continue; }
  }
  return `<table class="rd-sxs"><colgroup><col class="gc"><col class="cc"><col class="gc"><col class="cc"></colgroup><tbody>${rows.join("")}</tbody></table>`;
}

const anchorId = (p) => "rd-f-" + p.replace(/[^\w]+/g, "-");

/* ---- one file's collapsible diff (lazy-loads on open) ---- */
function FileDiff({ repo, base, head, file, defaultOpen }) {
  const [open, setOpen] = useState(defaultOpen);
  const [html, setHtml] = useState(null);
  const [err, setErr] = useState(null);
  const [big, setBig] = useState(null); // {lines, diff} when over cap
  const loadedFor = useRef("");

  useEffect(() => {
    if (!open) return;
    const key = `${base}|${head}|${file.path}`;
    if (loadedFor.current === key) return;
    loadedFor.current = key;
    setHtml(null); setErr(null); setBig(null);
    rget(`/api/repo/filediff?repo=${encodeURIComponent(repo)}&base=${encodeURIComponent(base)}&head=${encodeURIComponent(head)}&path=${encodeURIComponent(file.path)}`)
      .then(({ diff, lines }) => {
        if (lines > 4000) setBig({ lines, diff });
        else setHtml(sideBySideHTML(diff));
      })
      .catch((e) => setErr(String(e.message || e)));
  }, [open, repo, base, head, file.path]);

  return (
    <details id={anchorId(file.path)} open={open}
      onToggle={(e) => setOpen(e.currentTarget.open)}
      className="rounded-xl border border-zinc-800/80 bg-zinc-900/40">
      <summary className="rd-file-summary flex cursor-pointer items-center gap-2 rounded-t-xl px-3 py-2 font-mono text-[12px]">
        <span className={`rounded px-1.5 text-[9px] font-semibold uppercase ${
          file.status === "A" ? "bg-emerald-500/10 text-emerald-400"
          : file.status === "D" ? "bg-rose-500/10 text-rose-400"
          : file.status === "R" ? "bg-sky-500/10 text-sky-300"
          : "bg-zinc-800 text-zinc-400"}`}>{file.status}</span>
        <span className="min-w-0 flex-1 break-all text-zinc-200">
          {file.old_path && <span className="text-zinc-600 line-through">{file.old_path} </span>}
          {file.path}
        </span>
        <span className="shrink-0 font-mono text-[11px]">
          <span className="text-emerald-400">+{file.additions}</span>{" "}
          <span className="text-rose-400">−{file.deletions}</span>
        </span>
      </summary>
      <div className="rd-diff">
        {err && <div className="p-3 text-[12px] text-rose-400">{err}</div>}
        {big && (
          <div className="p-3 text-[12px] text-zinc-500">
            Large diff ({big.lines} lines).{" "}
            <button type="button" className="text-emerald-400 hover:underline"
              onClick={() => { setHtml(sideBySideHTML(big.diff)); setBig(null); }}>render anyway</button>
          </div>
        )}
        {!err && !big && !html && <div className="p-3 text-[12px] text-zinc-600">loading diff…</div>}
        {html && <div dangerouslySetInnerHTML={{ __html: html }} />}
      </div>
    </details>
  );
}

/* ---- changed-file tree (grouped by dir, GitHub-ish) ---- */
function FileTree({ files, onGo }) {
  const groups = useMemo(() => {
    const g = {};
    for (const f of files) {
      const d = f.path.includes("/") ? f.path.slice(0, f.path.lastIndexOf("/")) : "";
      (g[d] ??= []).push(f);
    }
    return g;
  }, [files]);
  if (!files.length) return <div className="p-3 text-xs text-zinc-600">no files</div>;
  return (
    <div className="py-1.5">
      {Object.keys(groups).sort().map((d) => (
        <div key={d || "_root"}>
          {d && <div className="truncate px-3 py-0.5 font-mono text-[11px] text-zinc-600" title={d}>{d}/</div>}
          {groups[d].map((f) => (
            <button key={f.path} type="button" onClick={() => onGo(f.path)} title={f.path}
              className="flex w-full items-center gap-1.5 px-3 py-0.5 text-left text-[12px] transition-colors hover:bg-zinc-800/50"
              style={{ paddingLeft: d ? 20 : 12 }}>
              <span className={`shrink-0 rounded px-1 text-[9px] font-semibold uppercase ${
                f.status === "A" ? "text-emerald-400" : f.status === "D" ? "text-rose-400"
                : f.status === "R" ? "text-sky-300" : "text-zinc-500"}`}>{f.status}</span>
              <span className="min-w-0 flex-1 truncate text-zinc-300">{f.path.split("/").pop()}</span>
              <span className="shrink-0 font-mono text-[10px]">
                <span className="text-emerald-400">+{f.additions}</span>{" "}
                <span className="text-rose-400">−{f.deletions}</span>
              </span>
            </button>
          ))}
        </div>
      ))}
    </div>
  );
}

/* ---- the view ---- */
export default function RepoDiff() {
  const [repos, setRepos] = useState([]);
  const [repo, setRepo] = useState("");
  const [refs, setRefs] = useState({ local: [], remote: [] });
  const [base, setBase] = useState("");
  const [head, setHead] = useState("");
  const [commits, setCommits] = useState([]);     // full base..head, newest-first, each .idx
  const [checked, setChecked] = useState(new Set());
  const [span, setSpan] = useState(null);          // { base2, head2, files, shortstat, partial, contiguous, count }
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  // load repos, prefill the nakivo deliverable case
  useEffect(() => {
    rget("/api/repo/list").then(({ repos }) => {
      setRepos(repos);
      const has = repos.find((r) => r.name === "nakivo");
      setRepo(has ? "nakivo" : (repos[0]?.name || ""));
    }).catch((e) => setErr(String(e.message || e)));
  }, []);

  useEffect(() => {
    if (!repo) return;
    setRefs({ local: [], remote: [] });
    rget(`/api/repo/refs?repo=${encodeURIComponent(repo)}`).then((r) => {
      setRefs(r);
      const all = [...r.remote, ...r.local];
      const find = (needle) => all.find((x) => x.name.includes(needle))?.name || "";
      if (repo === "nakivo") {
        setBase(find("odoo12CE_legal") || all[0]?.name || "");
        setHead(find("CRM-11475-presale") || all[0]?.name || "");
      } else {
        setBase(all[0]?.name || ""); setHead(all[0]?.name || "");
      }
    }).catch((e) => setErr(String(e.message || e)));
  }, [repo]);

  const runCompare = useCallback(async () => {
    if (!repo || !base || !head) return;
    setErr(""); setMsg("Comparing…");
    try {
      const d = await rget(`/api/repo/compare?repo=${encodeURIComponent(repo)}&base=${encodeURIComponent(base)}&head=${encodeURIComponent(head)}`);
      setCommits(d.commits);
      setChecked(new Set(d.commits.map((c) => c.idx)));
      setMsg("");
    } catch (e) { setErr(String(e.message || e)); setMsg(""); }
  }, [repo, base, head]);

  // effective span from the checked commits (contiguous = exact; scattered = bounding span)
  const range = useMemo(() => {
    if (!commits.length || checked.size === 0) return null;
    const idxs = [...checked].sort((a, b) => a - b);
    const lo = idxs[0], hi = idxs[idxs.length - 1];
    const byIdx = {}; commits.forEach((c) => (byIdx[c.idx] = c));
    return {
      base2: lo === 0 ? base : byIdx[lo - 1].sha,
      head2: byIdx[hi].sha,
      partial: !(lo === 0 && hi === commits.length - 1),
      contiguous: hi - lo + 1 === idxs.length,
      count: idxs.length,
    };
  }, [commits, checked, base]);

  // recompute the file list + stats for the current span
  useEffect(() => {
    if (!range) { setSpan(null); return; }
    let alive = true;
    rget(`/api/repo/compare?repo=${encodeURIComponent(repo)}&base=${encodeURIComponent(range.base2)}&head=${encodeURIComponent(range.head2)}`)
      .then((d) => { if (alive) setSpan({ ...range, files: d.files, shortstat: d.shortstat }); })
      .catch((e) => { if (alive) setErr(String(e.message || e)); });
    return () => { alive = false; };
  }, [range, repo]);

  const gitOp = async (kind) => {
    if (!repo || busy) return;
    setBusy(true); setErr(""); setMsg(kind === "fetch" ? "Fetching…" : "Pulling…");
    try {
      const j = await rpost(`/api/repo/${kind}`, { repo });
      setMsg((j.output || "done").split("\n").slice(-2).join(" ").slice(0, 120));
      const r = await rget(`/api/repo/refs?repo=${encodeURIComponent(repo)}`);
      setRefs(r);
    } catch (e) { setErr(String(e.message || e)); setMsg(""); }
    finally { setBusy(false); }
  };

  const goFile = (path) => {
    const el = document.getElementById(anchorId(path));
    if (el) { el.open = true; el.scrollIntoView({ behavior: "smooth", block: "start" }); }
  };

  const refOptions = (
    <>
      <optgroup label="remote">{refs.remote.map((r) => <option key={"r" + r.name} value={r.name}>{r.name} · {r.sha}</option>)}</optgroup>
      <optgroup label="local">{refs.local.map((r) => <option key={"l" + r.name} value={r.name}>{r.name} · {r.sha}</option>)}</optgroup>
    </>
  );
  const sel = "rounded-lg border border-zinc-800 bg-zinc-900/60 px-2.5 py-1.5 text-sm text-zinc-200 outline-none focus:border-emerald-500";
  const lbl = "mb-1 block font-mono text-[10px] uppercase tracking-wider text-zinc-500";

  return (
    <main className="mx-auto h-full max-w-[1500px] overflow-y-auto px-5 py-6 md:px-8">
      {/* controls */}
      <div className="sticky top-0 z-20 -mt-6 flex flex-wrap items-end gap-3 border-b border-zinc-800/80 bg-zinc-950/95 pb-4 pt-6 backdrop-blur-xl md:-mt-6">
        <div>
          <span className={lbl}>Repo</span>
          <select className={sel} value={repo} onChange={(e) => setRepo(e.target.value)}>
            {repos.map((r) => <option key={r.name} value={r.name}>{r.name} ({r.head} {r.short})</option>)}
          </select>
        </div>
        <div>
          <span className={lbl}>Base · origin/target</span>
          <select className={sel} value={base} onChange={(e) => setBase(e.target.value)}>{refOptions}</select>
        </div>
        <button type="button" title="Swap base/head" onClick={() => { const b = base; setBase(head); setHead(b); }}
          className="mb-1 rounded-lg border border-zinc-800 bg-zinc-900/60 px-2 py-1.5 text-sm text-zinc-400 hover:text-zinc-200">⇄</button>
        <div>
          <span className={lbl}>Head · your branch</span>
          <select className={sel} value={head} onChange={(e) => setHead(e.target.value)}>{refOptions}</select>
        </div>
        <button type="button" onClick={runCompare}
          className="rounded-lg bg-emerald-500 px-4 py-1.5 text-sm font-semibold text-white transition-colors hover:bg-emerald-600">
          Compare
        </button>
        <button type="button" disabled={busy} onClick={() => gitOp("fetch")} title="git fetch --all --prune"
          className="rounded-lg border border-zinc-800 bg-zinc-900/60 px-3 py-1.5 text-sm text-zinc-300 hover:text-zinc-100 disabled:opacity-50">Fetch</button>
        <button type="button" disabled={busy} onClick={() => gitOp("pull")} title="git pull --ff-only"
          className="rounded-lg border border-zinc-800 bg-zinc-900/60 px-3 py-1.5 text-sm text-zinc-300 hover:text-zinc-100 disabled:opacity-50">Pull</button>
        <span className={`ml-auto max-w-[340px] self-center whitespace-pre-wrap text-xs ${err ? "text-rose-400" : "text-zinc-500"}`}>
          {err || msg}
        </span>
      </div>

      {commits.length === 0 ? (
        <p className="py-16 text-center text-sm text-zinc-500">
          Pick a repo, a base and a head ref, then Compare. Only <span className="font-mono">fetch</span> / <span className="font-mono">pull</span> ever touch the network.
        </p>
      ) : (
        <>
          {span && (
            <div className="flex flex-wrap gap-2 pt-4">
              <span className="rounded-full border border-zinc-800 bg-zinc-900/50 px-3 py-1 text-xs">{span.shortstat.files} files</span>
              <span className="rounded-full border border-zinc-800 bg-zinc-900/50 px-3 py-1 text-xs text-emerald-400">+{span.shortstat.additions}</span>
              <span className="rounded-full border border-zinc-800 bg-zinc-900/50 px-3 py-1 text-xs text-rose-400">−{span.shortstat.deletions}</span>
              <span className="rounded-full border border-zinc-800 bg-zinc-900/50 px-3 py-1 font-mono text-xs text-zinc-400">{span.base2.slice(0, 9)} → {span.head2.slice(0, 9)}</span>
              {span.partial && <span className="rounded-full border border-emerald-500/40 bg-emerald-500/10 px-3 py-1 text-xs text-emerald-300">{span.count} of {commits.length} commits{span.contiguous ? "" : " (span)"}</span>}
            </div>
          )}

          <div className="grid grid-cols-1 gap-6 pt-4 lg:grid-cols-[300px_minmax(0,1fr)]">
            {/* sidebar: commits filter + file tree */}
            <aside className="hidden min-w-0 lg:block">
              <div className="sticky top-24 max-h-[calc(100dvh-7rem)] min-w-0 overflow-auto rounded-xl border border-zinc-800/80 bg-zinc-900/40">
                <div className="border-b border-zinc-800/70">
                  <div className="flex items-center justify-between px-3 pb-1.5 pt-2.5 font-mono text-[10px] uppercase tracking-wider text-zinc-500">
                    <span>Commits</span>
                    <span>
                      <button type="button" className="hover:text-emerald-400" onClick={() => setChecked(new Set(commits.map((c) => c.idx)))}>all</button>
                      <span className="px-1 text-zinc-700">·</span>
                      <button type="button" className="hover:text-emerald-400" onClick={() => setChecked(new Set())}>none</button>
                    </span>
                  </div>
                  {commits.map((c) => (
                    <label key={c.sha} className="flex cursor-pointer gap-2 px-3 py-1 text-[12px] hover:bg-zinc-800/40">
                      <input type="checkbox" className="mt-1 accent-[#FF5133]" checked={checked.has(c.idx)}
                        onChange={(e) => { const n = new Set(checked); e.target.checked ? n.add(c.idx) : n.delete(c.idx); setChecked(n); }} />
                      <span className="min-w-0">
                        <span className="font-mono text-emerald-400">{c.short}</span>{" "}
                        <span className="break-words text-zinc-300">{c.subject}</span>
                        <span className="block font-mono text-[10px] text-zinc-600">{c.author} · {c.date}</span>
                      </span>
                    </label>
                  ))}
                </div>
                <div className="px-3 pb-0.5 pt-2.5 font-mono text-[10px] uppercase tracking-wider text-zinc-500">Files changed</div>
                {span && <FileTree files={span.files} onGo={goFile} />}
              </div>
            </aside>

            {/* diffs */}
            <div className="flex min-w-0 flex-col gap-3.5">
              {!span && <div className="text-sm text-zinc-500">Loading…</div>}
              {span && span.files.length === 0 && <div className="text-sm text-zinc-500">No changes in the selected commits.</div>}
              {span && span.files.map((f, i) => (
                <FileDiff key={f.path} repo={repo} base={span.base2} head={span.head2} file={f} defaultOpen={i < 20} />
              ))}
            </div>
          </div>
        </>
      )}
    </main>
  );
}

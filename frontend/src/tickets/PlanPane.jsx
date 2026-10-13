import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Editor, rootCtx, defaultValueCtx } from "@milkdown/core";
import { commonmark } from "@milkdown/preset-commonmark";
import { gfm } from "@milkdown/preset-gfm";
import { getMarkdown } from "@milkdown/utils";
import { Milkdown, MilkdownProvider, useEditor } from "@milkdown/react";
import "@milkdown/prose/view/style/prosemirror.css";
import { post, patch, del } from "../api.js";
import { STEP_STATUS, CHILD_KIND } from "./common.js";

/* Milkdown ships no theme here on purpose (same call TreasureDetail makes): a
 * theme package brings its own light-leaning palette and fights the app tokens.
 * So the document's prose rules live here, scoped to this editor alone. */
const EDITOR_SKIN = `
.fd-plan-editor .ProseMirror { outline: none; color: rgb(var(--z-300)); font-size: 13.5px; line-height: 1.62; }
.fd-plan-editor .ProseMirror > * + * { margin-top: 0.85em; }
.fd-plan-editor .ProseMirror h1 { font-size: 1.55rem; font-weight: 800; letter-spacing: -0.02em; color: rgb(var(--z-100)); line-height: 1.2; }
.fd-plan-editor .ProseMirror h2 { font-size: 0.72rem; font-weight: 700; letter-spacing: 0.16em; text-transform: uppercase; color: var(--fdx-signal); }
.fd-plan-editor .ProseMirror h3 { font-size: 0.95rem; font-weight: 700; color: rgb(var(--z-200)); }
.fd-plan-editor .ProseMirror blockquote {
  margin-left: 0; padding: 0.7em 1em; border-left: 2px solid var(--fdx-signal);
  background: rgb(var(--z-900) / 0.6); border-radius: 5px; color: rgb(var(--z-400));
}
.fd-plan-editor .ProseMirror blockquote > * + * { margin-top: 0.5em; }
.fd-plan-editor .ProseMirror ul, .fd-plan-editor .ProseMirror ol { padding-left: 1.35em; }
.fd-plan-editor .ProseMirror li { margin: 0.3em 0; }
.fd-plan-editor .ProseMirror li p { margin: 0; }
.fd-plan-editor .ProseMirror input[type="checkbox"] { accent-color: var(--fdx-signal); margin-right: 0.5em; }
.fd-plan-editor .ProseMirror code {
  font-family: var(--fdx-font-mono); font-size: 0.86em; padding: 0.12em 0.42em;
  border: 1px solid rgb(var(--z-800)); border-radius: 5px; background: rgb(var(--z-900));
  color: rgb(var(--z-100));
}
.fd-plan-editor .ProseMirror pre {
  font-family: var(--fdx-font-mono); font-size: 0.82em; padding: 0.9em 1em;
  border: 1px solid rgb(var(--z-800)); border-radius: 5px; background: rgb(var(--z-950));
  overflow-x: auto;
}
.fd-plan-editor .ProseMirror pre code { border: 0; padding: 0; background: none; }
.fd-plan-editor .ProseMirror table { width: 100%; border-collapse: collapse; font-size: 0.92em; }
.fd-plan-editor .ProseMirror th, .fd-plan-editor .ProseMirror td {
  border-bottom: 1px solid rgb(var(--z-800)); padding: 0.5em 0.7em; text-align: left; vertical-align: top;
}
.fd-plan-editor .ProseMirror th {
  font-family: var(--fdx-font-mono); font-size: 0.72rem; font-weight: 600;
  letter-spacing: 0.12em; text-transform: uppercase; color: rgb(var(--z-500));
  border-bottom-color: rgb(var(--z-700));
}
.fd-plan-editor .ProseMirror a { color: rgb(var(--z-200)); text-underline-offset: 2px; }
.fd-plan-editor .ProseMirror hr { border: 0; border-top: 1px solid rgb(var(--z-800)); }
`;

const STATUSES = ["planned", "doing", "done", "dropped"];
const KINDS = ["TODO", "FIX", "BUG", "AMEND", "RERUN"];

/* Milkdown over the step body. No theme package: the editor inherits the app's
 * tokens instead of shipping a second visual system (same call the Treasures
 * editor makes). Remounted per step by its key, so switching milestones cannot
 * leave the previous document in the view. */
function BodyEditor({ defaultValue, apiRef }) {
  const { get, loading } = useEditor(
    (root) => Editor.make()
      .config((ctx) => { ctx.set(rootCtx, root); ctx.set(defaultValueCtx, defaultValue || ""); })
      .use(commonmark).use(gfm),
    []);
  useEffect(() => {
    if (loading) return;
    apiRef.current = () => get()?.action(getMarkdown()) ?? "";
  }, [loading, get, apiRef]);
  return <Milkdown />;
}

function HourField({ value, onCommit, tone = "text-zinc-200" }) {
  const [draft, setDraft] = useState(String(value ?? 0));
  useEffect(() => { setDraft(String(value ?? 0)); }, [value]);
  const commit = () => {
    const n = parseFloat(draft);
    if (Number.isNaN(n) || n === value) { setDraft(String(value ?? 0)); return; }
    onCommit(n);
  };
  return (
    <span className="inline-flex items-center rounded-full border border-zinc-700 px-1.5 focus-within:border-emerald-500">
      <input value={draft} onChange={(e) => setDraft(e.target.value)} onBlur={commit}
        onKeyDown={(e) => { if (e.key === "Enter") e.currentTarget.blur(); }}
        aria-label="Estimate in hours" inputMode="decimal"
        className={`w-8 bg-transparent py-0.5 text-right font-mono text-[10px] font-semibold ${tone} focus:outline-none`} />
      <span className="pr-0.5 font-mono text-[10px] font-semibold text-zinc-600">h</span>
    </span>
  );
}

function StatusSelect({ value, onChange, small }) {
  const s = STEP_STATUS[value] || STEP_STATUS.planned;
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${s.dot}`} />
      <select value={value} onChange={(e) => onChange(e.target.value)} aria-label="Step status"
        className={`cursor-pointer appearance-none bg-transparent font-mono font-semibold uppercase tracking-wider focus:outline-none ${s.text} ${small ? "text-[8px]" : "text-[9px]"}`}>
        {STATUSES.map((k) => <option key={k} value={k} className="bg-zinc-900 text-zinc-200">{k}</option>)}
      </select>
    </span>
  );
}

export default function PlanPane({ ticket, onChanged }) {
  const plan = ticket.plan || [];
  const [selected, setSelected] = useState(() => plan.find((m) => m.status === "doing")?.id || plan[0]?.id || null);
  const [saving, setSaving] = useState("");
  const [newChild, setNewChild] = useState({ title: "", kind: "TODO" });
  const apiRef = useRef(null);

  useEffect(() => {
    if (!plan.some((m) => m.id === selected)) setSelected(plan[0]?.id || null);
  }, [plan, selected]);

  const step = useMemo(() => plan.find((m) => m.id === selected) || null, [plan, selected]);
  const rollup = ticket.rollup || { planned_h: 0, emergent_h: 0 };

  const patchStep = useCallback((id, body) =>
    patch(`/api/tickets/steps/${id}`, body).then(onChanged), [onChanged]);

  const saveBody = () => {
    if (!step || !apiRef.current) return;
    setSaving("saving");
    patchStep(step.id, { body: apiRef.current() })
      .then(() => setSaving("saved"))
      .catch(() => setSaving("failed"));
  };

  const addChild = (e) => {
    e.preventDefault();
    const title = newChild.title.trim();
    if (!title || !step) return;
    post(`/api/tickets/${ticket.key}/steps`, {
      title, parent_id: step.id, kind: newChild.kind, status: "planned",
      estimate_h: 0, origin: "raised by you",
    }).then(() => { setNewChild({ title: "", kind: newChild.kind }); onChanged(); });
  };

  return (
    <div className="grid gap-4 lg:grid-cols-[4fr_6fr]">
      {/* ---- the list ---- */}
      <div className="flex flex-col rounded-[5px] border border-zinc-800 bg-zinc-900/40 p-4">
        <div className="flex items-center gap-3 border-b border-zinc-800 pb-3">
          <h3 className="text-[15px] font-bold tracking-tight text-zinc-100">Steps</h3>
          <span className="flex-1" />
          <span className="font-mono text-[10px] font-semibold tracking-wider text-zinc-600">
            {rollup.milestones} + {rollup.children}
          </span>
        </div>

        <div className="min-h-0 flex-1 py-1">
          {plan.map((m) => (
            <div key={m.id}>
              <div className={`flex items-center gap-2.5 rounded-[5px] px-2 py-2 ${
                m.id === selected ? "border border-emerald-500 bg-white/5" : "border border-transparent"}`}>
                <button type="button" onClick={() => setSelected(m.id)}
                  className="flex min-w-0 flex-1 items-center gap-2.5 text-left">
                  <span className={`grid h-6 w-6 shrink-0 place-items-center rounded-full border font-mono text-[9px] font-semibold ${
                    m.status === "done" ? "border-teal-400 bg-teal-400 text-zinc-950"
                      : m.id === selected ? "border-emerald-500 bg-emerald-500 text-zinc-950"
                      : "border-zinc-700 text-zinc-500"}`}>{m.code}</span>
                  <span className={`min-w-0 flex-1 truncate text-[13px] ${m.id === selected ? "font-semibold text-zinc-100" : "text-zinc-300"}`}>
                    {m.title}
                  </span>
                </button>
                <HourField value={m.estimate_h} onCommit={(n) => patchStep(m.id, { estimate_h: n })} />
                <StatusSelect value={m.status} onChange={(v) => patchStep(m.id, { status: v })} />
              </div>
              {(m.children || []).map((c) => (
                <div key={c.id} className="flex items-center gap-2 py-1.5 pl-11 pr-2">
                  <span className="h-px w-2.5 shrink-0 bg-zinc-700" />
                  <span className={`shrink-0 rounded-[5px] border px-1.5 py-0.5 font-mono text-[8px] font-semibold tracking-wider ${CHILD_KIND[c.kind] || CHILD_KIND.TODO}`}>
                    {c.kind || "TODO"}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-xs text-zinc-400">{c.title}</span>
                  <StatusSelect small value={c.status} onChange={(v) => patchStep(c.id, { status: v })} />
                </div>
              ))}
            </div>
          ))}
        </div>

        <div className="space-y-2 border-t border-zinc-800 px-2 pt-3">
          {[["Planned", `${rollup.planned_h}h`, "text-zinc-100"],
            ["Emergent", `+${rollup.emergent_h}h`, "text-amber-400"]].map(([label, value, tone]) => (
            <div key={label} className="flex items-center gap-3">
              <span className="font-mono text-[9px] font-semibold uppercase tracking-[0.14em] text-zinc-600">{label}</span>
              <span className="flex-1" />
              <span className={`font-mono text-base font-semibold ${tone}`}>{value}</span>
            </div>
          ))}
        </div>
      </div>

      {/* ---- the detail, beside the list rather than behind a route ---- */}
      <div className="flex min-w-0 flex-col rounded-[5px] border border-zinc-800 bg-zinc-900/40">
        {!step ? (
          <div className="p-8 text-sm text-zinc-500">This ticket has no plan steps yet.</div>
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-2.5 border-b border-zinc-800 px-4 py-2.5">
              <span className="font-mono text-[10px] font-semibold uppercase tracking-[0.16em] text-emerald-400">
                {step.code} · {step.title}
              </span>
              <HourField value={step.estimate_h} onCommit={(n) => patchStep(step.id, { estimate_h: n })} />
              <StatusSelect value={step.status} onChange={(v) => patchStep(step.id, { status: v })} />
              <span className="flex-1" />
              <span className="font-mono text-[9px] font-semibold uppercase tracking-wider text-zinc-600">
                {saving === "saving" ? "saving…" : saving === "saved" ? "saved" : saving === "failed" ? "save failed" : "markdown · wysiwyg"}
              </span>
              <button type="button" onClick={saveBody}
                className="rounded-full border border-zinc-700 px-3 py-1 text-xs font-medium text-zinc-300 hover:border-zinc-600 hover:text-zinc-100">
                Save
              </button>
            </div>

            <div className="fd-plan-editor min-h-[280px] flex-1 overflow-x-auto px-4 py-3">
              <style>{EDITOR_SKIN}</style>
              <MilkdownProvider key={step.id}>
                <BodyEditor defaultValue={step.body} apiRef={apiRef} />
              </MilkdownProvider>
            </div>

            <div className="border-t border-zinc-800 px-4 py-3">
              <div className="flex flex-wrap items-center gap-3">
                <h4 className="text-[15px] font-bold tracking-tight text-zinc-100">Child steps</h4>
                <span className="font-mono text-[9px] font-semibold uppercase tracking-wider text-amber-400">
                  raised while building — not in the estimate
                </span>
              </div>

              <div className="mt-2">
                {(step.children || []).map((c) => (
                  <div key={c.id} className="border-b border-zinc-800 py-2.5 pl-6">
                    <div className="flex items-center gap-2.5">
                      <span className="h-px w-2.5 shrink-0 bg-zinc-700" />
                      <span className={`w-[74px] shrink-0 rounded-[5px] border py-0.5 text-center font-mono text-[8px] font-semibold tracking-wider ${CHILD_KIND[c.kind] || CHILD_KIND.TODO}`}>
                        {c.kind || "TODO"}
                      </span>
                      <StatusSelect value={c.status} onChange={(v) => patchStep(c.id, { status: v })} />
                      <span className="min-w-0 flex-1 truncate text-[13px] text-zinc-100">{c.title}</span>
                      <HourField value={c.estimate_h} tone="text-amber-300"
                        onCommit={(n) => patchStep(c.id, { estimate_h: n })} />
                      <button type="button" aria-label={`Delete ${c.title}`}
                        onClick={() => del(`/api/tickets/steps/${c.id}`).then(onChanged)}
                        className="shrink-0 px-1 font-mono text-[11px] text-zinc-600 hover:text-rose-400">✕</button>
                    </div>
                    {c.origin && (
                      <div className="pl-[104px] pt-1 font-mono text-[9px] font-semibold uppercase tracking-wider text-zinc-600">
                        {c.origin}
                      </div>
                    )}
                  </div>
                ))}
                {!(step.children || []).length && (
                  <div className="py-2 pl-6 font-mono text-[10px] uppercase tracking-wider text-zinc-700">
                    nothing unplanned yet
                  </div>
                )}
              </div>

              <form onSubmit={addChild} className="mt-3 flex flex-wrap items-center gap-2 pl-6">
                <select value={newChild.kind} onChange={(e) => setNewChild({ ...newChild, kind: e.target.value })}
                  aria-label="Child step kind"
                  className="h-8 rounded-full border border-zinc-800 bg-zinc-900 px-2.5 font-mono text-[10px] font-semibold tracking-wider text-zinc-300 focus:outline-none">
                  {KINDS.map((k) => <option key={k} value={k}>{k}</option>)}
                </select>
                <input value={newChild.title} onChange={(e) => setNewChild({ ...newChild, title: e.target.value })}
                  placeholder="What came up?" aria-label="Child step title"
                  className="h-8 min-w-[220px] flex-1 rounded-full border border-zinc-800 bg-zinc-900/60 px-3 text-[13px] text-zinc-200 placeholder:text-zinc-600 focus:border-zinc-600 focus:outline-none" />
                <button type="submit"
                  className="h-8 rounded-full border border-zinc-700 px-3.5 text-xs font-medium text-zinc-300 hover:text-zinc-100">
                  Add child step
                </button>
              </form>

              <p className="mt-3 pl-6 text-xs leading-relaxed text-zinc-600">
                A child step never edits the milestone estimate. It is counted beside it, so the gap
                between what was estimated and what the work really cost stays visible.
              </p>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

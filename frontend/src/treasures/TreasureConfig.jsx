import React, { useCallback, useEffect, useState } from "react";
import { relTime } from "./format.js";

/**
 * Treasures configuration — the install-wide defaults injected into every
 * artifact a treasure generates from now on.
 *
 * Shaped like Odoo's website-customisation panel rather than three separate
 * pages: one page, stacked sections, each section a title + a line saying
 * what it does and where it lands + an editor. GET/PUT `/api/treasure-config`
 * is a flat object with three string keys and a server-owned `updated_at`.
 *
 * The file splits in two on purpose, the same split TreasureDetail uses for
 * its own chrome (`detail/DetailHeader.jsx`, `detail/CmsPanel.jsx`):
 *   - pure functions (`draftFromConfig`, `dirtyKeys`, `isDirty`, `buildPatch`,
 *     `loadConfig`, `saveConfig`) that hold every actual rule — what counts as
 *     dirty, what goes in a PUT, how a fetch failure becomes a message.
 *   - `ConfigView`, a plain (no-hooks) presentational component driven
 *     entirely by props, so every phase (loading / error / loaded) and every
 *     dirty/saving/error combination can be rendered directly in a test
 *     without mounting the stateful container or simulating a click.
 * `TreasureConfig` (default export) is the thin container: it owns the
 * fetch-on-mount effect and the draft/baseline state, and renders `ConfigView`.
 */

const CONFIG_PATH = "/api/treasure-config";

// The three editable keys, in display order. Also doubles as the whitelist
// for buildPatch — an unknown key 400s on the server, so nothing here may
// ever reach the wire under a key outside this list.
const FIELDS = [
  {
    key: "default_agent_notes",
    title: "Agent notes",
    helper:
      "Markdown text, injected into every generated artifact inside a collapsed " +
      '<details id="agent-notes">. It is the note an agent reads when it fetches ' +
      "a published artifact.",
  },
  {
    key: "default_header_html",
    title: "Header",
    helper: "Raw HTML placed at the top of the document body of every generated artifact.",
  },
  {
    key: "default_footer_html",
    title: "Footer",
    helper: "Raw HTML placed at the bottom of the document body of every generated artifact.",
  },
];
const KEYS = FIELDS.map((f) => f.key);

/* ---- pure helpers (exported for tests) --------------------------------- */

// Empty is a legitimate value ("inject nothing"), never a missing one — so
// every key always resolves to a string, never null/undefined.
export function draftFromConfig(config) {
  const draft = {};
  for (const k of KEYS) draft[k] = config?.[k] ?? "";
  return draft;
}

export function dirtyKeys(draft, baseline) {
  if (!draft || !baseline) return [];
  return KEYS.filter((k) => (draft[k] ?? "") !== (baseline[k] ?? ""));
}

export function isDirty(draft, baseline) {
  return dirtyKeys(draft, baseline).length > 0;
}

// Only the changed keys go over the wire, and a changed-to-empty field is
// still INCLUDED with value "" — dropping it would read as "leave it alone"
// to the server, not "clear it", and an empty string is not falsy-skippable
// here the way it might be in a generic form serializer.
export function buildPatch(draft, baseline) {
  const patch = {};
  for (const k of dirtyKeys(draft, baseline)) patch[k] = draft[k];
  return patch;
}

export async function loadConfig() {
  const r = await fetch(CONFIG_PATH);
  if (!r.ok) throw new Error(`${CONFIG_PATH}: ${r.status}`);
  return r.json();
}

export async function saveConfig(patch) {
  const r = await fetch(CONFIG_PATH, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new Error(body.detail || `${CONFIG_PATH}: ${r.status}`);
  }
  return r.json();
}

/* ---- presentational (exported, no hooks — safe to call directly) ------- */

export function ConfigView({
  phase, error, onRetry,
  draft, dirty, saving, saveError, updatedAt,
  onChange, onSave,
}) {
  if (phase === "loading") {
    return (
      <div className="grid min-h-[40vh] place-items-center px-6 text-center">
        <div>
          <div className="mx-auto h-5 w-5 animate-spin rounded-full border-2 border-zinc-700 border-r-emerald-400" />
          <p className="mt-4 font-mono text-[11px] uppercase tracking-[0.16em] text-zinc-500">
            Loading configuration
          </p>
        </div>
      </div>
    );
  }

  // Distinct from "nothing configured yet": this means the server could not
  // be asked at all, so a retry is the useful next action — not a form.
  if (phase === "error") {
    return (
      <div className="rounded-xl border border-rose-500/30 bg-rose-500/5 px-5 py-4">
        <p className="text-sm text-rose-300">Could not load the configuration ({error}).</p>
        <button
          type="button"
          className="fdx-button mt-3"
          data-variant="secondary"
          data-size="sm"
          onClick={onRetry}
        >
          Retry
        </button>
      </div>
    );
  }

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <p className="max-w-2xl text-[13px] leading-relaxed text-zinc-400">
          These are defaults applied to every artifact generated{" "}
          <strong className="text-zinc-300">from now on</strong>. An artifact already
          published picks up a changed value only the next time it is re-rendered — saving
          here does not reach back and rewrite it.
        </p>
        <div className="flex flex-col items-end gap-2">
          <button
            type="button"
            className="fdx-button"
            data-variant="primary"
            data-size="sm"
            onClick={onSave}
            disabled={!dirty || saving}
            data-state={!dirty || saving ? "disabled" : undefined}
          >
            {saving ? "Saving…" : "Save"}
          </button>
          {saveError && (
            <span className="max-w-xs text-right font-mono text-[10px] leading-tight text-rose-400">
              {saveError}
            </span>
          )}
          {updatedAt && (
            <span className="font-mono text-[10px] text-zinc-500">
              Last updated {relTime(updatedAt)}
            </span>
          )}
        </div>
      </div>

      {FIELDS.map((f) => {
        const id = `treasure-config-${f.key}`;
        return (
          <section key={f.key} className="fd-shell">
            <div className="fd-core grid gap-2 p-5">
              <label htmlFor={id} className="text-sm font-bold tracking-tight text-zinc-100">
                {f.title}
              </label>
              <p className="text-[12px] leading-relaxed text-zinc-500">{f.helper}</p>
              <textarea
                id={id}
                value={draft?.[f.key] ?? ""}
                onChange={(e) => onChange(f.key, e.target.value)}
                spellCheck={false}
                rows={f.key === "default_agent_notes" ? 6 : 8}
                className="mt-1 min-h-[8rem] w-full rounded-lg border border-[color:var(--fd-hair-2)] bg-zinc-950/40 p-3 font-mono text-[12px] leading-relaxed text-zinc-200 focus:border-[color:var(--fd-coral)]/50 focus:outline-none"
              />
            </div>
          </section>
        );
      })}
    </div>
  );
}

/* ---- container ---------------------------------------------------------- */

export default function TreasureConfig() {
  const [phase, setPhase] = useState("loading"); // "loading" | "error" | "loaded"
  const [loadError, setLoadError] = useState(null);
  const [baseline, setBaseline] = useState(null);
  const [draft, setDraft] = useState(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState(null);

  const load = useCallback(async () => {
    setPhase("loading");
    setLoadError(null);
    try {
      const config = await loadConfig();
      setBaseline(config);
      setDraft(draftFromConfig(config));
      setPhase("loaded");
    } catch (e) {
      setLoadError(String(e.message || e));
      setPhase("error");
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const onChange = (key, value) => setDraft((d) => ({ ...d, [key]: value }));

  const dirty = isDirty(draft, baseline);

  const onSave = async () => {
    if (!dirty || saving) return;
    setSaving(true);
    setSaveError(null);
    try {
      const patch = buildPatch(draft, baseline);
      const result = await saveConfig(patch);
      // The server is the record: the new baseline (and the draft shown on
      // screen) come from its response, never from what was locally typed.
      setBaseline(result);
      setDraft(draftFromConfig(result));
    } catch (e) {
      setSaveError(String(e.message || e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <ConfigView
      phase={phase}
      error={loadError}
      onRetry={load}
      draft={draft}
      dirty={dirty}
      saving={saving}
      saveError={saveError}
      updatedAt={baseline?.updated_at}
      onChange={onChange}
      onSave={onSave}
    />
  );
}

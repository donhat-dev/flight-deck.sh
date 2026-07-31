/**
 * Appearance — the install's typography config.
 *
 * Edits are a DRAFT: they apply immediately so the page you are looking at is the
 * preview, but nothing is stored until Save. That split matters because the config
 * is install-wide — an accidental click should not change what every client sees.
 *
 * "Reset to source style" removes the stored config rather than storing today's
 * values. Storing them would look identical now and silently pin the install to
 * them the moment a stylesheet changed.
 *
 * Each role picks a face, a weight and a size, and each list is what that role can
 * honestly take: Satoshi has no 600, and size is absolute for body prose but a
 * MULTIPLIER for labels and figures, which span 9px to 6rem.
 */
import React, { useEffect, useState } from "react";

import {
  FONTS, ROLES, apply, byId, clear, defaults, isSame, loadCached, normalise, save,
} from "../ui/appearance.js";

const VN_BADGE = {
  full: { text: "Vietnamese", tone: "text-emerald-400 border-emerald-500/40" },
  none: { text: "No Vietnamese", tone: "text-amber-400 border-amber-500/40" },
  platform: { text: "Platform", tone: "text-zinc-400 border-zinc-700" },
};

const fmtSize = (role, v) => (role.sizeKind === "px" ? `${v}px` : `${v}×`);

export default function AppearanceView() {
  // `saved` is what the server holds (null = source style). `draft` is what the
  // pickers show. `overriding` says whether the draft is actually being IMPOSED on
  // the page — the distinction the pickers alone cannot express.
  //
  // Without it, reset was self-defeating: clear() removed the inline tokens, then
  // setDraft(defaults()) re-ran the effect and wrote them straight back, so "reset
  // to source style" pinned the app to today's values instead of releasing it. The
  // same flag stops a fresh install from being pinned by merely opening this page.
  const [saved, setSaved] = useState(loadCached);
  const [draft, setDraft] = useState(() => loadCached() || defaults());
  const [overriding, setOverriding] = useState(() => loadCached() !== null);
  const [status, setStatus] = useState(null);

  // Preview immediately; persisting is Save's job.
  useEffect(() => {
    apply(overriding ? draft : null);
  }, [draft, overriding]);

  // Nothing to save when the page is releasing the style and the server agrees.
  const dirty = overriding ? (saved === null || !isSame(saved, draft)) : saved !== null;

  // The functional updater matters: computing the next value inside each handler
  // meant two picks in the same tick both read the same render's draft, and the
  // earlier one was silently dropped.
  const set = (roleId, patch) => {
    // Touching any picker is what turns the draft into an override.
    setOverriding(true);
    setDraft((prev) => normalise({ ...prev, [roleId]: { ...prev[roleId], ...patch } }));
  };

  const onSave = async () => {
    setStatus({ busy: true });
    const res = await save(draft);
    setSaved(res.ok ? res.choice : saved);
    setStatus(res.ok ? { ok: true, note: "Saved for the whole install." }
                     : { ok: false, error: res.error });
  };

  const onReset = async () => {
    setStatus({ busy: true });
    const res = await clear();
    setSaved(null);
    setOverriding(false);
    setDraft(defaults());
    setStatus(res.ok ? { ok: true, note: "Cleared — the stylesheets' own values apply." }
                     : { ok: false, error: res.error });
  };

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <p className="max-w-2xl text-[11px] leading-relaxed text-zinc-400">
          Changes preview here immediately but are stored only when you save. Saving
          writes the config to the server, so it belongs to the install — it survives
          a cleared browser profile and applies to every client, this dashboard and
          the Radio plane alike. <strong className="text-zinc-300">Reset to source
          style</strong> deletes the config so the stylesheets&apos; own values apply
          again, rather than pinning the install to today&apos;s numbers.
        </p>
        <div className="flex flex-col items-end gap-2">
          <div className="flex items-center gap-2">
            <button type="button" className="fdx-button" data-variant="secondary"
                    data-size="sm" onClick={onReset}>
              Reset to source style
            </button>
            <button type="button" className="fdx-button" data-variant="primary"
                    data-size="sm" onClick={onSave} disabled={!dirty || !overriding}
                    data-state={!dirty || !overriding ? "disabled" : undefined}>
              {dirty && overriding ? "Save & apply" : "Saved"}
            </button>
          </div>
          {status?.ok && (
            <span className="font-mono text-[10px] text-emerald-400">{status.note}</span>
          )}
          {status && status.ok === false && (
            <span className="max-w-xs font-mono text-[10px] leading-tight text-amber-400">
              Previewed but NOT stored — it reverts on reload. {status.error}
            </span>
          )}
          {dirty && !status?.busy && (
            <span className="font-mono text-[10px] text-zinc-500">
              {overriding ? "Unsaved preview" : "Source style — save to keep it"}
            </span>
          )}
          {!overriding && (
            <span className="font-mono text-[10px] text-zinc-500">
              Showing the stylesheets&apos; own values
            </span>
          )}
        </div>
      </div>

      {ROLES.map((role) => {
        const value = draft[role.id];
        const font = byId(value.font);
        return (
          <section key={role.id} className="fd-shell">
            <div className="fd-core grid gap-4 p-5">
              <div className="grid gap-1">
                <div className="flex flex-wrap items-baseline justify-between gap-3">
                  <h3 className="text-sm font-bold tracking-tight text-zinc-100">{role.label}</h3>
                  <code className="font-mono text-[10px] text-zinc-500">
                    {role.token} · {role.weightToken} · {role.sizeToken}
                  </code>
                </div>
                <p className="text-[11px] text-zinc-400">{role.describes}</p>
              </div>

              {/* Rendered in the exact face, weight and size being chosen — the only
                  honest preview, since names and numbers say nothing about shapes.
                  A scale role previews at a representative 20px base. */}
              <p
                className="border-y border-zinc-800/80 py-4 text-zinc-100"
                style={{
                  fontFamily: font.stack,
                  fontWeight: value.weight,
                  fontSize: role.sizeKind === "px"
                    ? `calc(${value.size}px * 1.6)`
                    : `calc(20px * ${value.size})`,
                }}
              >
                {role.sample}
              </p>

              <div className="grid gap-3">
                <div role="group" aria-label={`${role.label} font`} className="flex flex-wrap gap-2">
                  {FONTS.map((candidate) => {
                    const active = value.font === candidate.id;
                    const badge = VN_BADGE[candidate.vietnamese];
                    return (
                      <button
                        key={candidate.id}
                        type="button"
                        aria-pressed={active}
                        onClick={() => set(role.id, { font: candidate.id })}
                        title={candidate.note}
                        className={`grid gap-1 rounded-md border px-3 py-2 text-left transition-colors ${
                          active
                            ? "border-emerald-500/50 bg-emerald-500/10"
                            : "border-zinc-800 hover:border-zinc-700"
                        }`}
                      >
                        <span className="text-sm text-zinc-100"
                              style={{ fontFamily: candidate.stack }}>
                          {candidate.label}
                        </span>
                        <span className={`w-fit rounded border px-1 font-mono text-[9px] uppercase tracking-wide ${badge.tone}`}>
                          {badge.text}
                        </span>
                      </button>
                    );
                  })}
                </div>

                <div className="grid gap-3 sm:grid-cols-2">
                  <div className="grid gap-1.5">
                    <div className="flex flex-wrap items-baseline gap-2">
                      <span className="font-mono text-[10px] uppercase tracking-wider text-zinc-500">
                        Weight
                      </span>
                      <span className="text-[10px] text-zinc-500">{role.weightNote}</span>
                    </div>
                    {/* Only the weights this face really carries. */}
                    <div role="group" aria-label={`${role.label} weight`}
                         className="fdx-segmented w-fit">
                      {font.weights.map((w) => (
                        <button key={w} type="button" aria-pressed={value.weight === w}
                                onClick={() => set(role.id, { weight: w })}
                                className="fdx-segmented-seg" style={{ fontWeight: w }}>
                          {w}
                        </button>
                      ))}
                    </div>
                  </div>

                  <div className="grid gap-1.5">
                    <div className="flex flex-wrap items-baseline gap-2">
                      <span className="font-mono text-[10px] uppercase tracking-wider text-zinc-500">
                        Size
                      </span>
                      <span className="text-[10px] text-zinc-500">{role.sizeNote}</span>
                    </div>
                    <div role="group" aria-label={`${role.label} size`}
                         className="fdx-segmented w-fit">
                      {role.sizes.map((v) => (
                        <button key={v} type="button" aria-pressed={value.size === v}
                                onClick={() => set(role.id, { size: v })}
                                className="fdx-segmented-seg">
                          {fmtSize(role, v)}
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </section>
        );
      })}

      <p className="text-[10px] leading-relaxed text-zinc-500">
        Each list is what the role can honestly take. Weights are what the face
        actually carries — Satoshi ships five static cuts and has no 600, IBM Plex
        Mono offers only the three weights we import, and the variable faces offer
        their whole range. Size is absolute for body prose because that is the root of
        the scale, and a multiplier for labels and figures because those run from 9px
        to 6rem and an absolute value would flatten one end.
      </p>
    </div>
  );
}

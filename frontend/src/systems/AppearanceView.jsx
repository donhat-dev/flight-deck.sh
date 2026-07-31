/**
 * Appearance — the install's typography config.
 *
 * A config page, not a preference: every change is PUT to /api/appearance, so the
 * choice belongs to the FlightDeck install and survives a cleared browser profile.
 * When the save fails the page says so, because the change has already been applied
 * locally and would otherwise revert silently on the next load.
 *
 * Each role picks a face AND a weight, and the weight list comes from what that
 * face actually carries — Satoshi has no 600, IBM Plex Mono ships only the three
 * weights we import. Offering a weight a face lacks is a lie the browser hides by
 * rounding to the nearest one it has.
 *
 * Every sample carries a Vietnamese word, because that is where these candidates
 * differ: Outfit looks fine until "Nghiên cứu" falls back mid-word.
 */
import React, { useEffect, useState } from "react";

import {
  FONTS, ROLES, apply, byId, defaults, loadCached, normalise, save,
} from "../ui/appearance.js";

const VN_BADGE = {
  full: { text: "Vietnamese", tone: "text-emerald-400 border-emerald-500/40" },
  none: { text: "No Vietnamese", tone: "text-amber-400 border-amber-500/40" },
  platform: { text: "Platform", tone: "text-zinc-400 border-zinc-700" },
};

export default function AppearanceView() {
  const [choice, setChoice] = useState(loadCached);
  const [status, setStatus] = useState(null);

  // State is the single source of truth and one effect syncs it outward. Computing
  // the next value inside each handler meant two picks in the same tick both read
  // the same render's `choice`, and the earlier one was silently dropped.
  useEffect(() => {
    let alive = true;
    apply(choice);
    save(choice).then((res) => {
      if (alive) setStatus(res.ok ? { ok: true } : { ok: false, error: res.error });
    });
    return () => {
      alive = false;
    };
  }, [choice]);

  const pickFont = (roleId, fontId) =>
    setChoice((prev) => normalise({ ...prev, [roleId]: { ...prev[roleId], font: fontId } }));

  const pickWeight = (roleId, weight) =>
    setChoice((prev) => ({ ...prev, [roleId]: { ...prev[roleId], weight } }));

  const reset = () => setChoice(defaults());

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <p className="max-w-2xl text-[11px] leading-relaxed text-zinc-400">
          Saved on the server, so this is the install&apos;s configuration — it survives
          a cleared browser profile and applies to every client, this dashboard and the
          Radio plane alike. Each role writes two CSS tokens on{" "}
          <code className="font-mono">&lt;html&gt;</code>, so nothing has to re-render
          for a change to land.
        </p>
        <div className="flex items-center gap-3">
          {status && !status.ok && (
            <span className="max-w-xs font-mono text-[10px] leading-tight text-amber-400">
              Applied locally but NOT saved — it will revert on reload. {status.error}
            </span>
          )}
          <button type="button" className="fdx-button" data-variant="secondary" data-size="sm"
                  onClick={reset}>
            Reset to defaults
          </button>
        </div>
      </div>

      {ROLES.map((role) => {
        const value = choice[role.id];
        const font = byId(value.font);
        return (
          <section key={role.id} className="fd-shell">
            <div className="fd-core grid gap-4 p-5">
              <div className="grid gap-1">
                <div className="flex flex-wrap items-baseline justify-between gap-3">
                  <h3 className="text-sm font-bold tracking-tight text-zinc-100">{role.label}</h3>
                  <code className="font-mono text-[10px] text-zinc-500">
                    {role.token} · {role.weightToken}
                  </code>
                </div>
                <p className="text-[11px] text-zinc-400">{role.describes}</p>
              </div>

              {/* Rendered in the exact face AND weight being chosen — the only honest
                  preview, since a name and a number tell you nothing about shapes. */}
              <p
                className="border-y border-zinc-800/80 py-4 text-2xl text-zinc-100"
                style={{ fontFamily: font.stack, fontWeight: value.weight }}
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
                        onClick={() => pickFont(role.id, candidate.id)}
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

                <div className="grid gap-1.5">
                  <div className="flex flex-wrap items-baseline gap-2">
                    <span className="font-mono text-[10px] uppercase tracking-wider text-zinc-500">
                      Weight
                    </span>
                    <span className="text-[10px] text-zinc-500">{role.weightNote}</span>
                  </div>
                  {/* Only the weights this face really carries. */}
                  <div role="group" aria-label={`${role.label} weight`} className="fdx-segmented w-fit">
                    {font.weights.map((w) => (
                      <button
                        key={w}
                        type="button"
                        aria-pressed={value.weight === w}
                        onClick={() => pickWeight(role.id, w)}
                        className="fdx-segmented-seg"
                        style={{ fontWeight: w }}
                      >
                        {w}
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          </section>
        );
      })}

      <p className="text-[10px] leading-relaxed text-zinc-500">
        Weights are what each face actually carries, not a fixed list: Satoshi ships
        five static cuts and has no 600, IBM Plex Mono offers only the three weights we
        import, and Space Grotesk / JetBrains Mono / Outfit are variable so their whole
        range is real. Switching to a face that lacks your weight moves you to the
        nearest one it has.
      </p>
    </div>
  );
}

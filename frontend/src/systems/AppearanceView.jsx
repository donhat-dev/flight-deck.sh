/**
 * Appearance — pick the face for each kind of text.
 *
 * Deliberately a *trying* surface, not a settings form: each role shows its sample
 * rendered in the face you are choosing, and a Vietnamese word sits in every
 * sample because that is where these candidates actually differ. Outfit looks fine
 * until "Nghiên cứu" falls back mid-word.
 *
 * Changes apply immediately to the whole app (the tokens live on <html>), so the
 * page you are looking at is the preview.
 */
import React, { useEffect, useState } from "react";

import { FONTS, ROLES, apply, byId, defaults, load, save } from "../ui/appearance.js";

const VN_BADGE = {
  full: { text: "Vietnamese", tone: "text-emerald-400 border-emerald-500/40" },
  none: { text: "No Vietnamese", tone: "text-amber-400 border-amber-500/40" },
  platform: { text: "Platform", tone: "text-zinc-400 border-zinc-700" },
};

export default function AppearanceView() {
  const [choice, setChoice] = useState(load);

  // State is the single source of truth and an effect syncs it outward. The first
  // version computed `{...choice, [role]: font}` inside each handler, so two picks
  // in quick succession both read the SAME render's `choice` and the earlier one
  // was silently dropped — found by clicking two roles in one tick, not by reading.
  useEffect(() => {
    save(choice);
    apply(choice);
  }, [choice]);

  const pick = (roleId, fontId) =>
    setChoice((prev) => ({ ...prev, [roleId]: fontId }));

  const reset = () => setChoice(defaults());

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <p className="max-w-2xl text-[11px] leading-relaxed text-zinc-400">
          Each role sets one CSS token on <code className="font-mono">&lt;html&gt;</code>, so a
          change lands everywhere at once — this dashboard, the Radio plane and the
          design proposals. Every sample carries a Vietnamese word, because that is
          where these faces differ most.
        </p>
        <button type="button" className="fdx-button" data-variant="secondary" data-size="sm"
                onClick={reset}>
          Reset to defaults
        </button>
      </div>

      {ROLES.map((role) => {
        const current = byId(choice[role.id]) || byId(role.fallback);
        return (
          <section key={role.id} className="fd-shell">
            <div className="fd-core grid gap-4 p-5">
              <div className="grid gap-1">
                <div className="flex items-baseline justify-between gap-3">
                  <h3 className="text-sm font-bold tracking-tight text-zinc-100">{role.label}</h3>
                  <code className="font-mono text-[10px] text-zinc-500">{role.token}</code>
                </div>
                <p className="text-[11px] text-zinc-400">{role.describes}</p>
              </div>

              {/* The sample is rendered in the candidate itself — the only honest
                  preview, since a name tells you nothing about the shapes. */}
              <p
                className="border-y border-zinc-800/80 py-4 text-2xl text-zinc-100"
                style={{ fontFamily: current.stack }}
              >
                {role.sample}
              </p>

              <div role="group" aria-label={`${role.label} font`} className="flex flex-wrap gap-2">
                {FONTS.map((font) => {
                  const active = current.id === font.id;
                  const badge = VN_BADGE[font.vietnamese];
                  return (
                    <button
                      key={font.id}
                      type="button"
                      aria-pressed={active}
                      onClick={() => pick(role.id, font.id)}
                      title={font.note}
                      className={`grid gap-1 rounded-md border px-3 py-2 text-left transition-colors ${
                        active
                          ? "border-emerald-500/50 bg-emerald-500/10"
                          : "border-zinc-800 hover:border-zinc-700"
                      }`}
                    >
                      <span
                        className="text-sm text-zinc-100"
                        style={{ fontFamily: font.stack }}
                      >
                        {font.label}
                      </span>
                      <span className={`w-fit rounded border px-1 font-mono text-[9px] uppercase tracking-wide ${badge.tone}`}>
                        {badge.text}
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>
          </section>
        );
      })}

      <p className="text-[10px] leading-relaxed text-zinc-500">
        Temporary surface for trying combinations while the new token style lands.
        Satoshi ships as five static weights (300/400/500/700/900) — a rule asking
        for 600 resolves to 700. Space Grotesk covers 300–700 and JetBrains Mono
        400–800 as variable faces.
      </p>
    </div>
  );
}

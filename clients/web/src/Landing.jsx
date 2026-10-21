import React, { useEffect, useRef, useState } from "react";
import { Roundel, Wordmark } from "./brand.jsx";

/* =====================================================================
   FlightDeck Night - marketing landing (#/welcome)
   A single scrollable "cockpit at night" screen: island nav, instrument-voice
   hero with one glowing dial, and three double-bezel feature panels. Night
   only, coral is the sole accent, one easing curve, pill radius for anything
   interactive and the double-bezel for cards (design system §§2,3,5,9).
   UI-only: all content is static; the CTA opens the live deck at #/.
   ===================================================================== */

const EASE = "cubic-bezier(.32,.72,0,1)";

/* prefers-reduced-motion, reactive. Gauges snap to final value when set. */
function usePrefersReducedMotion() {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReduced(mq.matches);
    const on = () => setReduced(mq.matches);
    mq.addEventListener?.("change", on);
    return () => mq.removeEventListener?.("change", on);
  }, []);
  return reduced;
}

/* Primary CTA (design system §3): coral pill, mono uppercase tracked label,
   with the button-in-button trailing arrow in a nested dark circle. One CTA
   intent = one label everywhere ("Open the deck"). */
function Cta({ label = "Open the deck", onClick, className = "" }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={
        "group inline-flex min-h-[44px] items-center gap-3 rounded-pill bg-emerald-500 " +
        "pl-[22px] pr-[9px] font-mono text-[12px] font-semibold uppercase tracking-[0.18em] text-white " +
        "transition-[background-color,transform] duration-500 hover:bg-emerald-600 active:scale-[.98] " +
        className
      }
      style={{ transitionTimingFunction: EASE }}
    >
      {label}
      <span
        className="grid h-8 w-8 place-items-center rounded-full bg-black/25 text-[13px] leading-none text-white transition-transform duration-500 group-hover:[transform:translate(2px,-2px)_scale(1.07)]"
        style={{ transitionTimingFunction: EASE }}
        aria-hidden="true"
      >
        ↗
      </span>
    </button>
  );
}

/* Ghost link (design system §3): mono uppercase, dim, hairline underline ->
   text + coral border on hover. */
function GhostLink({ label, onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="inline-flex min-h-[44px] items-center border-b border-zinc-700 pb-0.5 font-mono text-[12px] font-semibold uppercase tracking-[0.18em] text-zinc-400 transition-colors duration-500 hover:border-emerald-400 hover:text-zinc-100"
      style={{ transitionTimingFunction: EASE }}
    >
      {label}
    </button>
  );
}

/* Polar helper: angle in degrees where 0 = straight up, positive = clockwise. */
function pt(cx, cy, r, a) {
  const rad = ((a - 90) * Math.PI) / 180;
  return [cx + r * Math.cos(rad), cy + r * Math.sin(rad)];
}
function arcPath(cx, cy, r, a0, a1) {
  const [x0, y0] = pt(cx, cy, r, a0);
  const [x1, y1] = pt(cx, cy, r, a1);
  const large = a1 - a0 > 180 ? 1 : 0;
  return `M ${x0.toFixed(2)} ${y0.toFixed(2)} A ${r} ${r} 0 ${large} 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`;
}

/* Hero dial (design system §5): faint tick arc + a coral-hot needle carrying
   the glow. The needle sweeps from rest to the reading on first view via
   IntersectionObserver; under reduced motion it lands on the value instantly.
   This is the single glowing focal element of the hero. */
function Gauge({ value = 62, label = "5-HOUR WINDOW", caption = "SESSION QUOTA" }) {
  const reduced = usePrefersReducedMotion();
  const [armed, setArmed] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    if (reduced || typeof IntersectionObserver === "undefined") {
      setArmed(true);
      return;
    }
    const el = ref.current;
    if (!el) return;
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (e.isIntersecting) {
            setArmed(true);
            io.disconnect();
          }
        }
      },
      { threshold: 0.4 }
    );
    io.observe(el);
    return () => io.disconnect();
  }, [reduced]);

  const MIN = -120;
  const MAX = 120;
  const SPAN = MAX - MIN;
  const cx = 100;
  const cy = 104;
  const f = Math.max(0, Math.min(100, value)) / 100;
  const valueAngle = MIN + f * SPAN;
  const needleAngle = armed ? valueAngle : MIN;

  const TICKS = 41;
  const ticks = Array.from({ length: TICKS }, (_, i) => {
    const frac = i / (TICKS - 1);
    const a = MIN + frac * SPAN;
    const major = i % 5 === 0;
    const rOut = 86;
    const rIn = major ? 70 : 77;
    const [x0, y0] = pt(cx, cy, rOut, a);
    const [x1, y1] = pt(cx, cy, rIn, a);
    const lit = frac <= f + 1e-6;
    return { x0, y0, x1, y1, major, lit, key: i };
  });

  return (
    <div ref={ref} className="relative mx-auto w-full max-w-[360px]">
      <svg viewBox="0 0 200 200" className="w-full" role="img"
           aria-label={`${caption} ${label}: ${Math.round(value)} percent used`}>
        {/* faint rail */}
        <path d={arcPath(cx, cy, 86, MIN, MAX)} fill="none"
              stroke="rgba(244,243,239,.10)" strokeWidth="1" />
        {/* tick arc */}
        {ticks.map((t) => (
          <line
            key={t.key}
            x1={t.x0} y1={t.y0} x2={t.x1} y2={t.y1}
            stroke={t.lit ? "#FF5133" : "rgba(244,243,239,.28)"}
            strokeWidth={t.major ? 2 : 1}
            strokeLinecap="round"
          />
        ))}
        {/* needle group - rotates about the hub, glow is here only */}
        <g
          style={{
            transformBox: "view-box",
            transformOrigin: "100px 104px",
            transform: `rotate(${needleAngle}deg)`,
            transition: `transform 1.8s ${EASE}`,
            filter: "drop-shadow(0 0 7px rgba(255,81,51,.7))",
          }}
        >
          <path
            d={`M ${cx - 3} ${cy} L ${cx + 3} ${cy} L ${cx} ${cy - 74} Z`}
            fill="#FF5133"
          />
          <circle cx={cx} cy={cy} r="6" fill="#FF5133" />
          <circle cx={cx} cy={cy} r="2.4" fill="#050505" />
        </g>
        {/* center readout (mono, tabular) */}
        <text x={cx} y={cy + 52} textAnchor="middle"
              fontFamily="'IBM Plex Mono', monospace" fontWeight="600"
              fontSize="34" fill="#F4F3EF" style={{ fontVariantNumeric: "tabular-nums" }}>
          {Math.round(value)}
          <tspan fontSize="16" fill="rgba(244,243,239,.62)" dx="2">%</tspan>
        </text>
        <text x={cx} y={cy + 70} textAnchor="middle"
              fontFamily="'IBM Plex Mono', monospace" fontWeight="600"
              fontSize="8.5" letterSpacing="2.2" fill="rgba(244,243,239,.38)">
          {label}
        </text>
      </svg>
    </div>
  );
}

/* Double-bezel feature panel (design system §§1,2): shell wraps a raised core.
   Mono uppercase kicker (coral small mark) + Outfit title + dim body. */
function FeatureCard({ index, kicker, title, body, id }) {
  return (
    <div id={id} className="fd-shell is-hoverable h-full">
      <div className="fd-core h-full p-7">
        <div className="flex items-center gap-2.5">
          <span className="inline-block h-1.5 w-1.5 rotate-45 bg-emerald-400" aria-hidden="true" />
          <span className="font-mono text-[10.5px] font-semibold uppercase tracking-[0.22em] text-emerald-400">
            {kicker}
          </span>
          <span className="ml-auto font-mono text-[10.5px] tracking-[0.14em] text-zinc-600">
            {index}
          </span>
        </div>
        <h3 className="mt-5 text-[1.35rem] font-bold leading-tight tracking-[-0.02em] text-zinc-100">
          {title}
        </h3>
        <p className="mt-3 text-[0.95rem] leading-relaxed text-zinc-400">{body}</p>
      </div>
    </div>
  );
}

export default function Landing() {
  const reduced = usePrefersReducedMotion();

  const openDeck = () => {
    window.location.hash = "#/";
  };
  const scrollTo = (id) => {
    const el = document.getElementById(id) || document.getElementById("features");
    el?.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "start" });
  };

  const navLinks = [
    { label: "Spend", target: "feat-spend" },
    { label: "Logbook", target: "feat-logbook" },
    { label: "Charts", target: "features" },
  ];

  return (
    <div className="relative min-h-[100dvh] overflow-x-hidden bg-zinc-950 text-zinc-100">
      {/* atmosphere: mesh orbs behind everything, film grain on top (both inert) */}
      <div className="fd-mesh motion-safe:animate-mesh-drift" aria-hidden="true" />
      <div className="fd-grain" aria-hidden="true" />

      {/* Island navigation (design system §2): fixed centered pill */}
      <header className="fixed inset-x-0 top-[22px] z-[60] flex justify-center px-4">
        <nav
          aria-label="Primary"
          className="flex w-max max-w-[calc(100vw-32px)] items-center gap-4 rounded-pill border border-white/10 px-3 py-2 pl-4 sm:gap-6"
          style={{
            background: "rgba(10,11,14,.55)",
            backdropFilter: "blur(22px) saturate(150%)",
            WebkitBackdropFilter: "blur(22px) saturate(150%)",
            boxShadow: "inset 0 1px 0 rgba(255,255,255,.08)",
          }}
        >
          <a
            href="#/welcome"
            onClick={(e) => { e.preventDefault(); window.scrollTo({ top: 0, behavior: reduced ? "auto" : "smooth" }); }}
            className="flex min-h-[44px] items-center gap-2.5 text-zinc-100"
            aria-label="FlightDeck home"
          >
            <Roundel className="h-[22px] w-[22px]" />
            <Wordmark className="fd-word-flat text-[15px]" />
          </a>

          <ul className="hidden items-center gap-6 min-[920px]:flex" aria-label="Sections">
            {navLinks.map((l) => (
              <li key={l.label}>
                <button
                  type="button"
                  onClick={() => scrollTo(l.target)}
                  className="min-h-[44px] font-mono text-[11px] font-semibold uppercase tracking-[0.2em] text-zinc-500 transition-colors duration-500 hover:text-zinc-100"
                  style={{ transitionTimingFunction: EASE }}
                >
                  {l.label}
                </button>
              </li>
            ))}
          </ul>

          <Cta onClick={openDeck} />
        </nav>
      </header>

      {/* content sits above the mesh */}
      <main className="relative z-10">
        {/* Hero */}
        <section className="mx-auto flex max-w-[1180px] flex-col items-center gap-12 px-5 pb-24 pt-40 min-[920px]:grid min-[920px]:grid-cols-2 min-[920px]:items-center min-[920px]:gap-10 min-[920px]:pt-48">
          <div className="text-center min-[920px]:text-left">
            {/* display wordmark with runway centerline (design system §9) */}
            <Wordmark runway className="block text-[clamp(2.6rem,6vw,4.2rem)] leading-none" />

            <h1 className="mt-7 text-[clamp(2.1rem,4.6vw,3.4rem)] font-extrabold leading-[1.04] tracking-[-0.03em] text-zinc-100">
              Your cockpit for Claude Code.
            </h1>

            <p className="mx-auto mt-5 max-w-[46ch] text-[1.05rem] leading-relaxed text-zinc-400 min-[920px]:mx-0">
              See every token priced, your 5-hour and weekly headroom, and a full logbook
              of each session at a glance.
            </p>

            <div className="mt-8 flex flex-wrap items-center justify-center gap-5 min-[920px]:justify-start">
              <Cta onClick={openDeck} />
              <GhostLink label="Read the manual" onClick={() => scrollTo("features")} />
            </div>

            <div className="mt-8 font-mono text-[11px] font-semibold uppercase tracking-[0.24em] text-zinc-600">
              LOCAL-FIRST&nbsp;·&nbsp;SQLITE LEDGER&nbsp;·&nbsp;LIVE
            </div>
          </div>

          {/* hero instrument */}
          <div className="w-full">
            <div className="fd-shell mx-auto max-w-[420px]">
              <div className="fd-core px-6 py-8">
                <Gauge value={62} label="5-HOUR WINDOW" caption="SESSION QUOTA" />
                <div className="mt-4 flex items-center justify-center gap-2 border-t border-zinc-800/80 pt-4 font-mono text-[10px] uppercase tracking-[0.2em] text-zinc-500">
                  <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 motion-safe:animate-live-pulse" aria-hidden="true" />
                  live reading
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* Feature panels */}
        <section id="features" className="mx-auto max-w-[1180px] px-5 pb-32">
          <div className="mb-8 font-mono text-[11px] font-semibold uppercase tracking-[0.24em] text-zinc-500">
            On the deck
          </div>
          <div className="grid gap-5 min-[920px]:grid-cols-3">
            <FeatureCard
              id="feat-spend"
              index="01"
              kicker="Spend"
              title="Every token, priced"
              body="Priced against API list rates with cache savings folded in, so the real cost of every run is never a guess."
            />
            <FeatureCard
              id="feat-quota"
              index="02"
              kicker="Quota"
              title="Know your 5-hour + weekly headroom"
              body="Watch the rolling 5-hour block and the weekly window fill in real time, and see how much runway is left before the wall."
            />
            <FeatureCard
              id="feat-logbook"
              index="03"
              kicker="Logbook"
              title="Replay any session"
              body="Open a session and step through it turn by turn. Every prompt, tool call, and subagent stays on the record."
            />
          </div>

          <div className="mt-14 flex flex-col items-center gap-6 text-center">
            <p className="max-w-[52ch] text-[1.05rem] leading-relaxed text-zinc-400">
              The whole deck runs on your machine. No account, no upload, just your local ledger.
            </p>
            <Cta onClick={openDeck} />
          </div>
        </section>

        <footer className="mx-auto max-w-[1180px] px-5 pb-16">
          <div className="flex flex-col items-center gap-4 border-t border-zinc-800/80 pt-8 sm:flex-row sm:justify-between">
            <div className="flex items-center gap-2.5">
              <Roundel className="h-5 w-5 text-zinc-100" />
              <Wordmark className="fd-word-flat text-sm" />
            </div>
            <div className="font-mono text-[10px] uppercase tracking-[0.22em] text-zinc-600">
              FlightDeck&nbsp;·&nbsp;Night ops
            </div>
          </div>
        </footer>
      </main>
    </div>
  );
}

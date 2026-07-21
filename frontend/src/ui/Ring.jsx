import React from "react";

/* ---- Ring instrument --------------------------------------------------- */
// Ring instrument (FlightDeck Night design system §5 "Quota"): a percentage
// gauge — hairline track + coral progress arc with glow — replacing generic
// linear bars wherever a value IS a percentage/capacity reading. Purely
// presentational (no state, no interaction), so it's a safe, isolated swap
// for any bar/number display without touching data flow. Shared by the
// sidebar QuotaBar and the Spend Efficiency gauge.
export default function Ring({ pct, size = 44, stroke = 4, color = "var(--fd-coral-hot)", trackColor = "var(--fd-hair)", showValue = false }) {
  const p = pct == null ? 0 : Math.max(0, Math.min(100, pct));
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  return (
    <div className="relative shrink-0" style={{ width: size, height: size }}>
      <svg viewBox={`0 0 ${size} ${size}`} width={size} height={size} className="-rotate-90" aria-hidden="true">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={trackColor} strokeWidth={stroke} />
        <circle
          cx={size / 2} cy={size / 2} r={r} fill="none" stroke={pct == null ? "var(--fd-faint)" : color} strokeWidth={stroke}
          strokeLinecap="round" strokeDasharray={`${(p / 100) * c} ${c}`}
          style={{
            color,
            filter: pct != null ? "var(--fd-ring-glow)" : "none",
            transition: "stroke-dasharray 1s cubic-bezier(.32,.72,0,1), stroke .3s",
          }}
        />
      </svg>
      {showValue && (
        <span className="absolute inset-0 grid place-items-center font-mono text-[10px] leading-none text-zinc-200">
          {pct == null ? "-" : Math.round(pct)}
        </span>
      )}
    </div>
  );
}

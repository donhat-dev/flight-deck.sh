import React from "react";

/**
 * The small pieces the three Memory panels share.
 *
 * Same shapes Comms and Hangar already use — an `Eyebrow`, a `fd-shell`/`fd-core`
 * panel, a hairline from `--fd-hair-2` — so this tab reads as another Systems view
 * rather than a second design. Nothing here holds state or fetches anything.
 */

export function Eyebrow({ children, className = "" }) {
  return (
    <div className={`font-mono text-[10px] uppercase leading-tight tracking-[0.17em] text-zinc-500 ${className}`}>
      {children}
    </div>
  );
}

export function Panel({ title, meta, right, children, bodyClassName = "" }) {
  return (
    <section className="fd-shell">
      <div className="fd-core">
        {(title || right) && (
          <div className="flex min-h-[42px] flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b border-[color:var(--fd-hair-2)] px-5 py-2">
            <div>
              <div className="text-xs font-bold tracking-tight text-zinc-100">{title}</div>
              {meta && <div className="mt-1 font-mono text-[10px] text-zinc-500">{meta}</div>}
            </div>
            {right}
          </div>
        )}
        <div className={bodyClassName}>{children}</div>
      </div>
    </section>
  );
}

/** One number with the sentence that says what it counts. */
export function Tile({ label, value, help, tone = "plain" }) {
  // amber = something to look at, coral (the `emerald` ramp) = the one accent.
  // See tailwind.config.js: emerald IS the coral signal here, amber stays amber.
  const colour = tone === "alert" ? "text-amber-400"
    : tone === "good" ? "text-emerald-400"
      : "text-zinc-100";
  return (
    <div className="border-t border-[color:var(--fd-hair-2)] px-5 py-4 first:border-t-0 sm:border-l sm:border-t-0 sm:first:border-l-0">
      <Eyebrow>{label}</Eyebrow>
      <div className={`mt-1.5 font-mono text-[26px] leading-none tracking-[-0.02em] ${colour}`}>
        {value}
      </div>
      <p className="mt-2 max-w-[34ch] text-[11px] leading-snug text-zinc-500">{help}</p>
    </div>
  );
}

export function TileRow({ children }) {
  return (
    <section className="fd-shell">
      <div className="fd-core grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4">{children}</div>
    </section>
  );
}

/**
 * A single-choice filter row. Not the kit's SegmentedControl: that is for two to five
 * joined choices, and this row is one per problem kind plus All.
 */
export function ChipRow({ label, items, value, onChange, className = "" }) {
  return (
    <div role="group" aria-label={label} className={`flex flex-wrap items-center gap-1.5 ${className}`}>
      {items.map((item) => {
        const on = item.value === value;
        return (
          <button
            key={item.value}
            type="button"
            aria-pressed={on}
            disabled={item.disabled}
            onClick={() => onChange(item.value)}
            className={`inline-flex min-h-[26px] items-center gap-1.5 rounded border px-2.5 text-[11px] font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${
              on
                ? "border-emerald-500/50 bg-emerald-500/15 text-emerald-300"
                : "border-[color:var(--fd-hair-2)] text-zinc-400 hover:bg-zinc-500/10 hover:text-zinc-200"
            }`}
          >
            {item.label}
            {item.count != null && (
              <span className="font-mono text-[10px] text-zinc-500">{item.count}</span>
            )}
          </button>
        );
      })}
    </div>
  );
}

/** The type of a memory, small and quiet — it groups, it does not rank. */
export function TypeTag({ children }) {
  return (
    <span className="rounded bg-zinc-500/10 px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-wide text-zinc-400">
      {children}
    </span>
  );
}

export function Loading({ what }) {
  return (
    <div className="px-5 py-10 text-center text-sm text-zinc-500">Reading {what}…</div>
  );
}

export function Failed({ error, onRetry }) {
  return (
    <div className="rounded-xl border border-rose-500/30 bg-rose-500/5 px-4 py-3 text-sm text-rose-300">
      Could not read the memory store ({String(error?.message || error)}).
      {onRetry && (
        <button type="button" onClick={onRetry} className="ml-2 underline hover:text-rose-200">
          Try again
        </button>
      )}
    </div>
  );
}

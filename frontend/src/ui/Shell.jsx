import React from "react";

/* ---- shared app shell -------------------------------------------------- */
// Every FlightDeck view renders through ONE shell: a single <Header> title
// strip on top, then a body that is either `contained` (data pages that scroll
// with the window) or `bleed` (tool pages whose body fills the remaining
// viewport). This keeps the title strip + horizontal rhythm identical across
// Spend, Logbook, Charts, Diff, Hub, Route Loom and Session detail.

// Shared top bar. Left: title (+ optional subtitle meta line beneath).
// Right: an optional `actions` slot (e.g. the Spend/Logbook range control).
// Height is a fixed h-14 strip with the same px as the contained container,
// closed off by a bottom hairline.
export function Header({ title, subtitle, actions }) {
  return (
    <div className="flex h-14 shrink-0 items-center justify-between gap-4 border-b border-zinc-800/80 px-5 md:px-8">
      <div className="min-w-0">
        <h1 className="text-lg font-semibold tracking-tight text-zinc-100">{title}</h1>
        {subtitle && <p className="mt-0.5 truncate text-xs text-zinc-500">{subtitle}</p>}
      </div>
      {actions && <div className="shrink-0">{actions}</div>}
    </div>
  );
}

// Two body modes under one Header.
//  - contained: data pages. The header + a centered, padded <main> that grows
//    with content and scrolls with the window (max-w 1440).
//  - bleed: tool pages. The header is a fixed strip; the body fills the rest of
//    the viewport (flex-1) and owns its own internal scroll/fit.
export function Shell({
  variant = "contained",
  header,
  children,
  // spacing/padding for the contained <main>; override per-view to compact
  // (e.g. Spend uses a 1rem top + tighter gaps to fit one screen). Session
  // detail keeps the default py-6/py-8 its sticky back-nav math depends on.
  contentClassName = "space-y-5 px-5 py-6 md:px-8 md:py-8",
}) {
  if (variant === "bleed") {
    return (
      <div className="flex h-[100dvh] min-h-0 flex-col">
        {header}
        <div className="min-h-0 flex-1 overflow-hidden">{children}</div>
      </div>
    );
  }
  return (
    <div className="flex flex-col">
      {header}
      <main className={`mx-auto w-full max-w-[1440px] ${contentClassName}`}>
        {children}
      </main>
    </div>
  );
}

export default Shell;

import React, { useEffect, useState } from "react";

/**
 * One level of nesting for the sidebar nav.
 *
 * App.jsx's NAV/SYS_NAV arrays are flat, each entry rendered by its own
 * `navBtn` helper. This adds exactly one level on top of that — a parent row
 * that discloses a handful of children — and nothing deeper: a sidebar this
 * narrow can show one extra indent before it turns into a menu nobody can scan.
 *
 * A leaf item (no `children`) renders as `renderLeaf(item)` and nothing else,
 * so it stays byte-for-byte what a flat NAV entry already is today.
 *
 * The row and the caret are TWO buttons, side by side, and that is the load-
 * bearing decision here. A parent like Treasures is both a section and a real
 * view, so a single button that only toggled disclosure would take the library
 * itself out of the sidebar — the group would have eaten the page it names.
 * Splitting them keeps the row a normal navigation button and gives disclosure
 * its own control. Side by side rather than nested because a button inside a
 * button is invalid HTML and browsers resolve it however they like.
 */
export default function NavGroup({ item, view, onSelect, renderLeaf }) {
  const children = item.children;
  const childKeys = (children || []).map((c) => c.k);
  const isActiveParent = view === item.k;
  const isActiveChild = childKeys.includes(view);

  // Hooks run before the leaf short-circuit below, not after it. React matches
  // hooks by call order, so an early return above a `useState` makes the order
  // depend on the props — and the day an entry gains or loses `children` the
  // whole component tree throws. The cost of getting this right is one wasted
  // state cell on a leaf.
  const [open, setOpen] = useState(isActiveParent || isActiveChild);

  // Deep-linking straight to a child (a hash route that restores `view` on
  // load) must never land on a collapsed group — re-open whenever `view`
  // becomes one of this group's children, even after the user collapsed it.
  useEffect(() => {
    if (isActiveChild) setOpen(true);
  }, [isActiveChild]);

  if (!children || children.length === 0) return renderLeaf(item);

  const panelId = `navgroup-${item.k}`;

  return (
    <div>
      <div className="flex items-center">
        {/* The row navigates, exactly as it did when this entry was a leaf, and
            opens the group on the way — going to a section and finding its
            children hidden would read as the click having half-failed. It never
            closes: that is the caret's job. */}
        <button
          type="button"
          aria-pressed={isActiveParent}
          style={{ fontWeight: "var(--fdx-weight-label)" }}
          onClick={() => { onSelect?.(item.k); setOpen(true); }}
          className={`flex flex-1 items-center gap-2.5 rounded-lg px-3 py-2 text-left text-sm transition-colors ${
            isActiveParent
              ? "bg-emerald-500/15 text-emerald-400"
              : "text-zinc-400 hover:bg-zinc-900/70 hover:text-zinc-200"
          }`}
        >
          <span className="w-4 text-center text-base leading-none opacity-80">{item.icon}</span>
          {item.label}
        </button>
        <button
          type="button"
          aria-expanded={open}
          aria-controls={panelId}
          aria-label={`${open ? "Collapse" : "Expand"} ${item.label}`}
          onClick={() => setOpen((o) => !o)}
          className="mr-1 rounded p-1.5 text-[10px] leading-none text-zinc-500 transition-colors hover:bg-zinc-900/70 hover:text-zinc-300"
        >
          <span
            aria-hidden="true"
            className={`inline-block transition-transform duration-200 ${open ? "rotate-90" : ""}`}
          >
            ▸
          </span>
        </button>
      </div>
      {/* Kept in the DOM (hidden, not unmounted) so `aria-controls` always
          resolves to a real element regardless of open state. */}
      <div id={panelId} className={`mt-1 flex flex-col gap-1 pl-6 text-[13px] ${open ? "" : "hidden"}`}>
        {children.map((child) => (
          <React.Fragment key={child.k}>{renderLeaf(child)}</React.Fragment>
        ))}
      </div>
    </div>
  );
}

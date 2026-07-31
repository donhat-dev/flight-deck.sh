import React, { useEffect, useRef, useState } from "react";

/**
 * Search is the primary control, and the filters move behind one button.
 *
 * Before, status, language, every tag in use, the search box and the system
 * actions shared one wrapping row, so "narrow the data" and "operate the system"
 * were indistinguishable. Now: a labelled search field that owns the width, a
 * Filters button carrying its own active count, and a sort control.
 *
 * Only ACTIVE filters are rendered as chips. Rendering every tag in the toolbar
 * made the toolbar grow with the library.
 */

const STATUS = [
  ["draft", "Draft"],
  ["published", "Published"],
  ["archived", "Archived"],
];
const LANGUAGE = [
  ["en", "English"],
  ["vi", "Vietnamese"],
];

function FilterGroup({ title, children }) {
  return (
    <div className="space-y-2">
      <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-zinc-500">{title}</p>
      <div className="flex flex-wrap gap-1.5">{children}</div>
    </div>
  );
}

function Option({ active, onClick, children, count }) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={`inline-flex min-h-[32px] items-center gap-1.5 rounded-lg border px-2.5 text-[13px] transition-colors ${
        active
          ? "border-[color:var(--fd-coral)] bg-[color:var(--fd-coral)]/10 text-zinc-100"
          : "border-[color:var(--fd-hair-2)] text-zinc-400 hover:text-zinc-200"
      }`}
    >
      {children}
      {count != null && <span className="font-mono text-[11px] text-zinc-500">{count}</span>}
    </button>
  );
}

export default function LibrarySearch({
  query, onQuery, status, onStatus, language, onLanguage,
  tag, onTag, tags, sort, onSort, activeCount, onClearAll,
}) {
  const [open, setOpen] = useState(false);
  const popRef = useRef(null);

  // Click-away and Escape, because a popover that only closes via its own button
  // is a trap on touch.
  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => { if (popRef.current && !popRef.current.contains(e.target)) setOpen(false); };
    const onKey = (e) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const chips = [];
  if (status) chips.push([status, STATUS.find(([k]) => k === status)?.[1] || status, () => onStatus(null)]);
  if (language) chips.push([language, LANGUAGE.find(([k]) => k === language)?.[1] || language, () => onLanguage(null)]);
  if (tag) chips.push([`#${tag}`, `#${tag}`, () => onTag(null)]);

  return (
    <div className="space-y-3">
      <div className="space-y-1.5">
        <label
          htmlFor="treasure-search"
          className="block text-[12px] font-semibold tracking-[0.02em] text-zinc-400"
        >
          Search library
        </label>
        <div className="flex flex-wrap items-center gap-2">
          <input
            id="treasure-search"
            type="search"
            value={query}
            onChange={(e) => onQuery(e.target.value)}
            placeholder="Title, slug or source path…"
            className="min-h-[44px] min-w-[220px] flex-1 rounded-lg border border-[color:var(--fd-hair-2)] bg-zinc-500/[0.03] px-3.5 text-[15px] text-zinc-100 placeholder:text-zinc-600 focus:border-[color:var(--fd-coral)]/50 focus:outline-none"
          />

          <div className="relative" ref={popRef}>
            <button
              type="button"
              aria-expanded={open}
              onClick={() => setOpen((v) => !v)}
              className="fdx-button"
              data-variant="secondary"
              data-size="sm"
            >
              <span>Filters</span>
              {activeCount > 0 && (
                <span className="ml-1 inline-flex h-5 min-w-[20px] items-center justify-center rounded-full bg-[color:var(--fd-coral)] px-1 font-mono text-[12px] text-white">
                  {activeCount}
                </span>
              )}
            </button>

            {open && (
              <div
                role="dialog"
                aria-label="Filters"
                className="absolute right-0 z-30 mt-2 w-[300px] space-y-4 rounded-xl border border-[color:var(--fd-hair)] bg-zinc-950/95 p-4 shadow-2xl backdrop-blur-xl"
              >
                <FilterGroup title="Status">
                  {STATUS.map(([k, label]) => (
                    <Option key={k} active={status === k} onClick={() => onStatus(status === k ? null : k)}>
                      {label}
                    </Option>
                  ))}
                </FilterGroup>
                <FilterGroup title="Language">
                  {LANGUAGE.map(([k, label]) => (
                    <Option key={k} active={language === k} onClick={() => onLanguage(language === k ? null : k)}>
                      {label}
                    </Option>
                  ))}
                </FilterGroup>
                {tags.length > 0 && (
                  <FilterGroup title="Tags">
                    {tags.map((t) => (
                      <Option
                        key={t.tag}
                        active={tag === t.tag}
                        count={t.count}
                        onClick={() => onTag(tag === t.tag ? null : t.tag)}
                      >
                        #{t.tag}
                      </Option>
                    ))}
                  </FilterGroup>
                )}
              </div>
            )}
          </div>

          <label className="sr-only" htmlFor="treasure-sort">Sort by</label>
          <select
            id="treasure-sort"
            value={sort}
            onChange={(e) => onSort(e.target.value)}
            className="min-h-[44px] rounded-lg border border-[color:var(--fd-hair-2)] bg-transparent px-3 text-[14px] font-semibold text-zinc-200 focus:outline-none"
          >
            <option value="updated">Updated</option>
            <option value="title">Title</option>
            <option value="size">Size</option>
          </select>
        </div>
      </div>

      {chips.length > 0 && (
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[12px] text-zinc-500">Active</span>
            {chips.map(([key, label, clear]) => (
              <button
                key={key}
                type="button"
                onClick={clear}
                className="inline-flex min-h-[32px] items-center gap-2 rounded-lg border border-[color:var(--fd-hair-2)] bg-zinc-500/[0.03] px-2.5 text-[13px] text-zinc-200 transition-colors hover:border-[color:var(--fd-coral)]/50"
              >
                {label}
                <span aria-hidden="true" className="text-zinc-500">×</span>
                <span className="sr-only">Remove filter</span>
              </button>
            ))}
          </div>
          <button
            type="button"
            onClick={onClearAll}
            className="text-[13px] font-semibold text-[color:var(--fd-coral)] hover:underline"
          >
            Clear all
          </button>
        </div>
      )}
    </div>
  );
}

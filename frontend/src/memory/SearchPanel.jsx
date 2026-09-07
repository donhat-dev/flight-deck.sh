import React, { useState } from "react";

import { Button, Notice } from "../ui/FlightComponents.jsx";
import { Eyebrow, Failed, Panel, TypeTag } from "./parts.jsx";

/**
 * The second route into the store.
 *
 * Recall runs on one cue — the summary line in the index — so a memory whose summary
 * does not mention the thing it is about is unreachable by the agent that wrote it.
 * This searches the full text instead, which is why each result shows two lines: the
 * summary the index carries, and the words that actually matched in the body. Seeing
 * the two side by side is what turns "found it" into "the summary needs rewriting".
 *
 * The "not in the summary" mark is worked out here, not read off the payload. The API
 * returns `in_index_only`, which is the opposite question — it flags a hit found ONLY
 * in the summary. What matters to a reader is the reverse case.
 */

const EXAMPLES = ["worktree", "odoo", "delete", "playwright"];

/** The matched words in bold, with the rest of the line left alone. */
function Highlight({ text, needle }) {
  if (!text) return null;
  const term = (needle || "").trim();
  if (!term) return <>{text}</>;
  const parts = text.split(new RegExp(`(${term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})`, "ig"));
  return (
    <>
      {parts.map((part, i) => (part.toLowerCase() === term.toLowerCase()
        ? <mark key={i} className="bg-emerald-500/25 font-semibold text-emerald-100">{part}</mark>
        : <React.Fragment key={i}>{part}</React.Fragment>))}
    </>
  );
}

function Result({ row, needle }) {
  const inSummary = (row.description || "").toLowerCase().includes(needle.toLowerCase());
  return (
    <article className="border-t border-[color:var(--fd-hair-2)] px-5 py-3.5 first:border-t-0">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <h3 className="font-mono text-[12px] text-zinc-100">{row.name}</h3>
        <TypeTag>{row.type || "untyped"}</TypeTag>
        {!inSummary && (
          <span className="rounded bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-medium text-amber-300">
            Not in the summary
          </span>
        )}
        <span className="ml-auto font-mono text-[10px] text-zinc-500">
          {row.hits} {row.hits === 1 ? "match" : "matches"}
        </span>
      </div>

      <dl className="mt-2 space-y-1.5">
        <div className="flex gap-3">
          <dt className="w-[68px] shrink-0 pt-px font-mono text-[9px] uppercase tracking-[0.14em] text-zinc-600">
            In index
          </dt>
          <dd className="max-w-[86ch] text-[12px] leading-snug text-zinc-400">
            {row.description
              ? <Highlight text={row.description} needle={needle} />
              : <span className="italic text-zinc-600">No summary line.</span>}
          </dd>
        </div>
        <div className="flex gap-3">
          <dt className="w-[68px] shrink-0 pt-px font-mono text-[9px] uppercase tracking-[0.14em] text-zinc-600">
            In text
          </dt>
          <dd className="max-w-[86ch] font-mono text-[11px] leading-snug text-zinc-300">
            {row.snippet
              ? <Highlight text={row.snippet} needle={needle} />
              : <span className="italic text-zinc-600">Matched in the summary only.</span>}
          </dd>
        </div>
      </dl>

      {row.neighbours?.length > 0 && (
        <div className="mt-2.5 flex gap-3">
          <span className="w-[68px] shrink-0 pt-px font-mono text-[9px] uppercase tracking-[0.14em] text-zinc-600">
            Links to
          </span>
          <ul className="min-w-0 space-y-1">
            {row.neighbours.map((n) => (
              <li key={n.name} className="text-[11px] leading-snug text-zinc-500">
                <span className="font-mono text-zinc-400">{n.name}</span>
                {n.description && <span className="text-zinc-600"> — {n.description}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </article>
  );
}

export default function SearchPanel({ query, draft, onDraft, onSearch, result, busy, error }) {
  const [id] = useState(() => `memory-search-${Math.random().toString(36).slice(2, 8)}`);
  const rows = result?.results || [];
  const hidden = rows.filter(
    (r) => !(r.description || "").toLowerCase().includes((query || "").toLowerCase()),
  ).length;

  return (
    <div className="space-y-3">
      <form
        className="fd-shell"
        onSubmit={(e) => { e.preventDefault(); onSearch(draft); }}
      >
        <div className="fd-core flex flex-wrap items-end gap-3 px-5 py-4">
          <div className="min-w-[240px] flex-1">
            <label htmlFor={id} className="block">
              <Eyebrow>Search</Eyebrow>
            </label>
            <input
              id={id}
              type="search"
              value={draft}
              onChange={(e) => onDraft(e.target.value)}
              placeholder="A word to look for"
              aria-describedby={`${id}-help`}
              className="mt-1.5 w-full border-b border-[color:var(--fd-hair-2)] bg-transparent pb-1.5 font-mono text-[15px] text-zinc-100 outline-none placeholder:text-zinc-600 focus:border-emerald-500/60"
            />
            <p id={`${id}-help`} className="mt-2 text-[11px] text-zinc-500">
              Finds words anywhere in a memory, not only in the one-line summary.
            </p>
          </div>
          <Button type="submit" variant="primary" size="sm" loading={busy}>Search</Button>
        </div>
      </form>

      {!query && (
        <Panel title="Nothing searched yet" bodyClassName="px-5 py-5">
          <p className="max-w-[70ch] text-[12px] leading-snug text-zinc-400">
            The index only carries one line per memory. Search reads the whole file, so
            it finds what that line left out.
          </p>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-zinc-600">
              Try
            </span>
            {EXAMPLES.map((word) => (
              <button
                key={word}
                type="button"
                onClick={() => { onDraft(word); onSearch(word); }}
                className="rounded border border-[color:var(--fd-hair-2)] px-2 py-0.5 font-mono text-[11px] text-zinc-400 hover:bg-zinc-500/10 hover:text-zinc-200"
              >
                {word}
              </button>
            ))}
          </div>
        </Panel>
      )}

      {error && <Failed error={error} onRetry={() => onSearch(query)} />}

      {query && !error && hidden > 0 && (
        <Notice title={`${hidden} of these ${rows.length} do not carry the word in their summary`}>
          Recall reads the summary line and nothing else, so those are reachable by full
          text alone. Rewriting the summary is usually the real fix.
        </Notice>
      )}

      {query && !error && result && (
        <Panel
          title={`Results for “${result.query}”`}
          meta={`${result.matches} ${result.matches === 1 ? "match" : "matches"} in ${result.in_memories} ${result.in_memories === 1 ? "memory" : "memories"}`}
        >
          {rows.length === 0
            ? (
              <p className="px-5 py-10 text-center text-sm text-zinc-500">
                Nothing in the store carries that word.
              </p>
            )
            : rows.map((row) => <Result key={row.name} row={row} needle={result.query} />)}
        </Panel>
      )}
    </div>
  );
}

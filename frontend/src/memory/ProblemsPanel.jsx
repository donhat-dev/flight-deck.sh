import React, { useMemo, useState } from "react";

import { relTime } from "../treasures/format.js";
import { KINDS, actionText, kindHelp, kindLabel } from "./labels.js";
import { ChipRow, Eyebrow, Failed, Loading, Panel, Tile, TileRow } from "./parts.jsx";

/**
 * What drifted in the store, as one flat table.
 *
 * One table, not one per problem kind. The reader's question is "what needs doing",
 * and the answer is a single worklist sorted heaviest first — four separate tables
 * would make them compare four sort orders to find the top of it. The kind stays a
 * column and a filter, which is the part four tables were really for.
 *
 * `Last updated` is joined in from the graph payload: the lint findings carry no
 * timestamp, and a problem on a file nobody has touched in months reads differently
 * from one on a file written this morning.
 */

const TONE = { no_source: "alert", dead_origin: "alert", broken_link: "alert" };

function KindCell({ kind }) {
  const alert = TONE[kind] === "alert";
  return (
    <span className="inline-flex items-center gap-2 whitespace-nowrap">
      <span
        aria-hidden="true"
        className={`h-1.5 w-1.5 shrink-0 rounded-full ${alert ? "bg-amber-400" : "bg-zinc-500"}`}
      />
      <span className={alert ? "text-amber-300" : "text-zinc-300"}>{kindLabel(kind)}</span>
    </span>
  );
}

export default function ProblemsPanel({ lint, graph, error, onRetry }) {
  const [kind, setKind] = useState("all");

  const updated = useMemo(() => {
    const map = new Map();
    (graph?.nodes || []).forEach((n) => map.set(n.name, n.last_updated));
    return map;
  }, [graph]);

  const findings = lint?.findings || [];
  const counts = lint?.counts || {};
  const rows = kind === "all" ? findings : findings.filter((f) => f.kind === kind);

  const touched = new Set(findings.map((f) => f.memory));
  const total = lint?.total_memories ?? 0;
  const clean = Math.max(0, total - touched.size);
  const neverRead = lint?.never_read?.length ?? 0;
  const heaviest = KINDS.find((k) => counts[k.key]);

  if (error) return <Failed error={error} onRetry={onRetry} />;
  if (!lint) return <Loading what="the store" />;

  return (
    <div className="space-y-3">
      <TileRow>
        <Tile
          label="Memories"
          value={total}
          help="Markdown files in this project's store, the index apart."
        />
        <Tile
          label="Need attention"
          value={touched.size}
          tone={touched.size ? "alert" : "good"}
          help={`${clean} of them are clean.`}
        />
        <Tile
          label="Problems"
          value={findings.length}
          help={heaviest
            ? `Heaviest: ${kindLabel(heaviest.key).toLowerCase()}, ${counts[heaviest.key]} of them.`
            : "Nothing to do."}
        />
        <Tile
          label="Never read"
          value={neverRead}
          help="Written down, and no session has opened the file since."
        />
      </TileRow>

      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <ChipRow
          label="Show one kind of problem"
          value={kind}
          onChange={setKind}
          items={[
            { value: "all", label: "All", count: findings.length },
            ...KINDS.map((k) => ({
              value: k.key,
              label: k.label,
              count: counts[k.key] || 0,
              disabled: !counts[k.key],
            })),
          ]}
        />
        <p className="font-mono text-[10px] text-zinc-500">
          {rows.length} of {findings.length} shown
        </p>
      </div>

      {kind !== "all" && (
        <p className="px-1 text-[11px] text-zinc-500">{kindHelp(kind)}</p>
      )}

      <Panel
        title="What needs doing"
        meta="Heaviest problem first, the order the check itself argues for"
        bodyClassName="overflow-x-auto"
      >
        <table className="w-full min-w-[820px] text-sm">
          <thead>
            <tr className="text-[11px] uppercase tracking-wider text-zinc-500">
              <th className="px-5 py-2.5 text-left font-medium">Problem</th>
              <th className="px-5 py-2.5 text-left font-medium">Memory</th>
              <th className="px-5 py-2.5 text-left font-medium">What was found</th>
              <th className="px-5 py-2.5 text-right font-medium">Last updated</th>
              <th className="px-5 py-2.5 text-left font-medium">What to do</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr className="border-t border-[color:var(--fd-hair-2)]">
                <td colSpan={5} className="px-5 py-10 text-center text-sm text-zinc-500">
                  {findings.length === 0
                    ? "Nothing drifted. Every memory has a source, a summary and a place in the index."
                    : "No finding of this kind."}
                </td>
              </tr>
            )}
            {rows.map((f, i) => (
              <tr
                key={`${f.kind}-${f.memory}-${i}`}
                className="border-t border-[color:var(--fd-hair-2)] align-top hover:bg-zinc-500/5"
              >
                <td className="px-5 py-3 text-[12px]"><KindCell kind={f.kind} /></td>
                <td className="px-5 py-3 font-mono text-[11px] text-zinc-200">{f.memory}</td>
                <td className="px-5 py-3 text-[12px] leading-snug text-zinc-400">
                  <span className="block max-w-[52ch]">{f.detail}</span>
                  {f.suggestion && (
                    <span className="mt-1 block max-w-[52ch] text-[11px] text-zinc-500">
                      Closest name in the store:{" "}
                      <span className="font-mono text-emerald-300">{f.suggestion}</span>
                    </span>
                  )}
                </td>
                <td className="whitespace-nowrap px-5 py-3 text-right font-mono text-[11px] text-zinc-500">
                  {relTime(updated.get(f.memory))}
                </td>
                <td className="px-5 py-3 text-[12px] leading-snug text-zinc-400">
                  <span className="block max-w-[28ch]">{actionText(f)}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>

      <div className="px-1">
        <Eyebrow>Read-only</Eyebrow>
        <p className="mt-1 max-w-[76ch] text-[11px] leading-snug text-zinc-500">
          This tab reports; it never edits the store. The harness owns those files and
          stamps their metadata on write, so the fixes above are done in the files
          themselves or by the agent that wrote them.
        </p>
      </div>
    </div>
  );
}

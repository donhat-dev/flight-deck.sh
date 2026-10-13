import React, { useMemo, useState } from "react";

import { SegmentedControl } from "../ui/FlightComponents.jsx";
import { relTime } from "../treasures/format.js";
import { layoutGraph } from "./graphLayout.js";
import { Eyebrow, Failed, Loading, Panel, Tile, TileRow } from "./parts.jsx";

/**
 * How the memories point at each other — a list first, a drawing second.
 *
 * The list is the default because it answers the question faster. Sorted fewest links
 * first, the memories nothing connects to sit at the top of the page, which is what a
 * reader came for; in a drawing they are dots you have to hunt for.
 *
 * The drawing is there for the shape the list cannot show — where the store is torn.
 * It runs no simulation: positions come from `graphLayout`, so the same store draws
 * the same picture every time and today's view can be compared with yesterday's.
 */

// Only the finding wears colour. Being pointed at is the normal case, and giving
// it a badge of its own would put three tones in one column and mark nothing.
const STATUS = {
  alone: { label: "No links", className: "bg-amber-500/10 text-amber-300" },
  in: { label: "Pointed at", className: "bg-zinc-500/10 text-zinc-400" },
  out: { label: "Points out", className: "bg-zinc-500/10 text-zinc-400" },
  both: { label: "Connected", className: "bg-zinc-500/10 text-zinc-400" },
};

const statusOf = (n) => (!n.in && !n.out ? "alone" : n.in && n.out ? "both" : n.in ? "in" : "out");

function StatusTag({ node }) {
  const s = STATUS[statusOf(node)];
  return (
    <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${s.className}`}>
      {s.label}
    </span>
  );
}

/* ---- list -------------------------------------------------------------- */

function LinksTable({ graph }) {
  const groups = useMemo(() => {
    const order = Object.keys(graph.groups || {}).sort(
      (a, b) => (graph.groups[b] || []).length - (graph.groups[a] || []).length
        || a.localeCompare(b),
    );
    // The API already sorted the nodes fewest links first; filtering keeps that.
    return order.map((type) => ({
      type,
      rows: graph.nodes.filter((n) => (n.type || "untyped") === type),
    }));
  }, [graph]);

  return (
    <Panel
      title="Every memory and its links"
      meta="Grouped by type, fewest links first, so the unconnected ones come out on top"
      bodyClassName="overflow-x-auto"
    >
      <table className="w-full min-w-[840px] text-sm">
        <thead>
          <tr className="text-[11px] uppercase tracking-wider text-zinc-500">
            <th className="px-5 py-2.5 text-left font-medium">Memory</th>
            <th className="px-5 py-2.5 text-left font-medium">Summary in the index</th>
            <th className="px-5 py-2.5 text-right font-medium">In</th>
            <th className="px-5 py-2.5 text-right font-medium">Out</th>
            <th className="px-5 py-2.5 text-right font-medium">Last updated</th>
            <th className="px-5 py-2.5 text-left font-medium">Status</th>
          </tr>
        </thead>
        {groups.map((group) => (
          <tbody key={group.type}>
            <tr className="border-t border-[color:var(--fdx-rule)] bg-zinc-500/5">
              <th colSpan={6} className="px-5 py-2 text-left">
                <span className="font-mono text-[10px] uppercase tracking-[0.17em] text-zinc-400">
                  {group.type}
                </span>
                <span className="ml-2 font-mono text-[10px] text-zinc-600">
                  {group.rows.length}
                </span>
              </th>
            </tr>
            {group.rows.map((n) => (
              <tr
                key={n.name}
                className="border-t border-[color:var(--fdx-rule)] align-top hover:bg-zinc-500/5"
              >
                <td className="px-5 py-2.5 font-mono text-[11px] text-zinc-200">{n.name}</td>
                <td className="px-5 py-2.5 text-[12px] leading-snug text-zinc-500">
                  <span className="block max-w-[64ch]">{n.description || "—"}</span>
                </td>
                <td className="px-5 py-2.5 text-right font-mono text-[12px] text-zinc-300">{n.in}</td>
                <td className="px-5 py-2.5 text-right font-mono text-[12px] text-zinc-300">{n.out}</td>
                <td className="whitespace-nowrap px-5 py-2.5 text-right font-mono text-[11px] text-zinc-500">
                  {relTime(n.last_updated)}
                </td>
                <td className="px-5 py-2.5"><StatusTag node={n} /></td>
              </tr>
            ))}
          </tbody>
        ))}
      </table>
    </Panel>
  );
}

/* ---- drawing ----------------------------------------------------------- */

const LEGEND = [
  { kind: "link", text: "A line runs from the memory that links out to the one it points at" },
  { kind: "broken", text: "A dashed line is a link with no memory at the other end" },
  { kind: "alone", text: "A memory marked ! has no link in and none out" },
];

function LegendMark({ kind }) {
  if (kind === "alone") {
    return (
      <span className="grid h-3.5 w-3.5 shrink-0 place-items-center rounded-sm bg-amber-500 font-mono text-[9px] leading-none text-zinc-950">
        !
      </span>
    );
  }
  return (
    <svg viewBox="0 0 20 6" className="h-1.5 w-5 shrink-0" aria-hidden="true">
      <path
        d="M0 3H20"
        strokeWidth="1.5"
        strokeDasharray={kind === "broken" ? "3 2.5" : undefined}
        className={kind === "broken" ? "stroke-rose-400" : "stroke-zinc-500"}
      />
    </svg>
  );
}

function LinksGraph({ graph }) {
  const view = useMemo(() => layoutGraph(graph), [graph]);
  return (
    <Panel
      title="The same links, drawn"
      meta="One column per type. Positions come from the data, so they never move between opens."
    >
      <div className="flex flex-wrap gap-x-6 gap-y-1.5 border-b border-[color:var(--fdx-rule)] px-5 py-3">
        <Eyebrow className="w-full">How to read it</Eyebrow>
        {LEGEND.map((item) => (
          <span key={item.kind} className="flex items-center gap-2 text-[11px] text-zinc-500">
            <LegendMark kind={item.kind} />
            {item.text}
          </span>
        ))}
      </div>
      <div className="overflow-x-auto px-5 py-4">
        <svg
          role="img"
          aria-label={`${view.nodes.length} memories in ${view.columns.length} columns, ${view.edges.length} links drawn`}
          viewBox={`0 0 ${view.width} ${view.height}`}
          width={view.width}
          height={view.height}
          className="max-w-none"
        >
          <g fill="none" strokeWidth="1">
            {view.edges.map((e, i) => (
              <path
                key={`${e.source}->${e.target}-${i}`}
                d={e.path}
                className={e.broken ? "stroke-rose-400" : "stroke-zinc-700"}
                strokeDasharray={e.broken ? "4 3" : undefined}
              />
            ))}
          </g>
          {view.columns.map((c) => (
            <text
              key={c.title}
              x={c.x}
              y={c.y + 11}
              className="fill-zinc-500 font-mono text-[9px] uppercase"
              style={{ letterSpacing: "0.14em" }}
            >
              {`${c.title} · ${c.count}`}
            </text>
          ))}
          {view.nodes.map((n) => (
            <g key={n.column + n.name}>
              <title>
                {n.missing
                  ? `${n.name} — no memory of this name exists`
                  : `${n.name} — ${n.in} in, ${n.out} out`}
              </title>
              <rect
                x={n.x}
                y={n.y}
                width={n.width}
                height={n.height}
                rx="2"
                className={n.missing
                  ? "fill-rose-500/10 stroke-rose-400/80"
                  : n.alone
                    ? "fill-zinc-900 stroke-amber-500/60"
                    : "fill-zinc-900 stroke-zinc-700"}
                strokeDasharray={n.missing ? "3 2" : undefined}
              />
              {(n.alone || n.missing) && (
                <text
                  x={n.x + 8}
                  y={n.y + n.height / 2 + 3.5}
                  className={n.missing ? "fill-rose-500" : "fill-amber-500"}
                  style={{ font: "700 10px var(--fdx-font-mono, monospace)" }}
                >
                  {n.missing ? "?" : "!"}
                </text>
              )}
              <text
                x={n.x + (n.alone || n.missing ? 18 : 8)}
                y={n.y + n.height / 2 + 3.5}
                className={n.missing ? "fill-zinc-200" : "fill-zinc-300"}
                style={{ font: "10px var(--fdx-font-mono, monospace)" }}
              >
                {n.name.length > 26 ? `${n.name.slice(0, 25)}…` : n.name}
              </text>
              {n.in > 0 && (
                <text
                  x={n.x + n.width - 8}
                  y={n.y + n.height / 2 + 3.5}
                  textAnchor="end"
                  className="fill-zinc-500"
                  style={{ font: "9px var(--fdx-font-mono, monospace)" }}
                >
                  {n.in} in
                </text>
              )}
            </g>
          ))}
        </svg>
      </div>
    </Panel>
  );
}

/* ---- the tab ----------------------------------------------------------- */

export default function LinksPanel({ graph, error, onRetry }) {
  const [shape, setShape] = useState("list");

  if (error) return <Failed error={error} onRetry={onRetry} />;
  if (!graph) return <Loading what="the links" />;

  const nodes = graph.nodes || [];
  const alone = nodes.filter((n) => !n.in && !n.out).length;
  const hub = nodes.reduce((best, n) => (n.in > (best?.in ?? -1) ? n : best), null);
  const types = Object.entries(graph.groups || {})
    .map(([type, names]) => `${names.length} ${type}`)
    .join(" · ");

  return (
    <div className="space-y-3">
      <TileRow>
        <Tile label="Memories" value={nodes.length} help={types || "None yet."} />
        <Tile
          label="Links"
          value={(graph.edges || []).length}
          help="Wikilinks written inside the memories."
        />
        <Tile
          label="Broken links"
          value={(graph.missing_targets || []).length}
          tone={graph.missing_targets?.length ? "alert" : "good"}
          help="The memory at the other end does not exist."
        />
        <Tile
          label="Most pointed at"
          value={hub?.in ?? 0}
          help={hub?.in ? `${hub.name} is the one hub.` : "Nothing is pointed at yet."}
        />
      </TileRow>

      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <SegmentedControl
          label="How to show the links"
          value={shape}
          onChange={setShape}
          items={[{ value: "list", label: "List" }, { value: "graph", label: "Graph" }]}
        />
        <p className="font-mono text-[10px] text-zinc-500">
          {alone} with no link either way
        </p>
      </div>

      {shape === "list" ? <LinksTable graph={graph} /> : <LinksGraph graph={graph} />}
    </div>
  );
}

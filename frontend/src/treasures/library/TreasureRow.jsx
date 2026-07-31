import React from "react";

/**
 * One treasure, in the two shapes a library needs.
 *
 * Both are real anchors to `#/treasure/<id>`, which is the point: the previous
 * `<tr role="link">` could not be opened in a new tab, copied as a link, or
 * middle-clicked. The onClick still runs so in-app navigation keeps its scroll
 * reset, but the href is what makes it a link.
 *
 * Desktop is a compact row; mobile is a card. The old build put the desktop table
 * inside a horizontal-scroll container on small screens, which is not responsive
 * — it is the same table behind a second scrollbar, and every document became a
 * very tall row.
 *
 * On the source column: the list payload carries `origin_kind`, `origin_path` and
 * `source_checksum`, but NOT the current hash of the file on disk — deciding
 * "source changed" means re-reading every file, which only the per-artifact
 * staleness endpoint does. So these rows report what the source IS, never a
 * freshness verdict they cannot compute. A source-changed column needs the list
 * endpoint to return staleness.
 */

const STATUS_TONE = {
  draft: "text-zinc-300",
  published: "text-emerald-400",
  archived: "text-zinc-500",
};

const SOURCE_LABEL = {
  doc_file: "Tracked file",
  claude_session: "Session",
  "artifact-port": "Ported",
  ui: "Pasted",
  discover: "Discovered",
};

export function relTime(ts) {
  if (!ts) return "—";
  const then = new Date(ts).getTime();
  if (Number.isNaN(then)) return "—";
  const mins = Math.max(0, Math.round((Date.now() - then) / 60000));
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days < 7) return `${days}d ago`;
  return `${Math.round(days / 7)}w ago`;
}

export function kb(bytes) {
  if (!bytes) return "—";
  return `${(bytes / 1024).toFixed(1)} KB`;
}

function sourceOf(row) {
  if (row.published_url) return { label: "claude.ai", tone: "text-zinc-500" };
  const label = SOURCE_LABEL[row.origin_kind] || row.origin_kind || "—";
  const tracked = /\.(md|html?|markdown)$/i.test(row.origin_path || "");
  return { label, tone: tracked ? "text-zinc-400" : "text-zinc-600" };
}

function StatusLabel({ status }) {
  return (
    <span className={`text-[13px] font-semibold capitalize ${STATUS_TONE[status] || "text-zinc-300"}`}>
      {status}
    </span>
  );
}

function Meta({ row }) {
  return (
    <span className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[12px] text-zinc-500">
      <span className="font-mono">{row.slug}</span>
      <span className="text-zinc-700">·</span>
      <span>{row.kind}</span>
      <span className="text-zinc-700">·</span>
      <span className="font-mono uppercase">{row.language}</span>
      <span className="text-zinc-700">·</span>
      <span className="font-mono">v{row.version}</span>
    </span>
  );
}

export function TreasureListRow({ row, onOpen }) {
  const src = sourceOf(row);
  return (
    <a
      href={`#/treasure/${encodeURIComponent(row.id)}`}
      onClick={() => onOpen?.(row.id)}
      className="grid grid-cols-[minmax(0,1fr)_150px_190px_28px] items-center gap-3 border-b border-[color:var(--fd-hair-2)] px-5 py-3.5 transition-colors last:border-b-0 hover:bg-zinc-500/5"
    >
      <span className="min-w-0 space-y-1">
        <span className="block truncate text-[15px] font-semibold text-zinc-100">{row.title}</span>
        <Meta row={row} />
      </span>
      <span><StatusLabel status={row.status} /></span>
      <span className="space-y-1">
        <span className="block text-[14px] text-zinc-200">{relTime(row.updated_at)}</span>
        <span className={`block text-[12px] ${src.tone}`}>{src.label}</span>
      </span>
      <span aria-hidden="true" className="text-right text-zinc-600">›</span>
    </a>
  );
}

export function TreasureMobileCard({ row, onOpen }) {
  const src = sourceOf(row);
  return (
    <a
      href={`#/treasure/${encodeURIComponent(row.id)}`}
      onClick={() => onOpen?.(row.id)}
      className="block space-y-2.5 border-b border-[color:var(--fd-hair-2)] px-4 py-3.5 transition-colors last:border-b-0 active:bg-zinc-500/10"
    >
      <span className="block text-[15px] font-semibold leading-snug text-zinc-100">{row.title}</span>
      <Meta row={row} />
      <span className="flex items-center justify-between gap-3">
        <StatusLabel status={row.status} />
        <span className="flex items-center gap-2 text-[13px] text-zinc-500">
          <span className={`text-[12px] ${src.tone}`}>{src.label}</span>
          <span className="text-zinc-700">·</span>
          {relTime(row.updated_at)}
        </span>
      </span>
    </a>
  );
}

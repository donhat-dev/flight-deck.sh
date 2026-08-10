/**
 * The blip-prose renderer: the markdown SUBSET, as React elements.
 *
 * Safe by construction rather than by sanitizing. The parser emits React elements and
 * text nodes only — there is no `dangerouslySetInnerHTML` anywhere in it — so markup
 * that reaches it renders as literal text, and there is no sanitizer to keep in step
 * with new injection tricks. The store refuses raw HTML on write (`prose_or_refuse`);
 * this is the same rule enforced from the reading side, and the two can fail
 * independently without either becoming an XSS.
 *
 * The subset is deliberately small: paragraphs, `- ` bullet lists, **bold**, *italic*,
 * `code`, and [links](https://…). It is what blip prose needs and nothing that fights
 * the panel's own typography — headings, images and tables belong in a Treasure, which
 * a blip can link to. Milkdown (already in the repo for Treasures) speaks the same
 * markdown, which is what keeps the WYSIWYG door open without a format migration.
 *
 * Plain text is valid markdown, so every blip written before this existed renders
 * unchanged — as the same paragraphs it always was.
 */
import React from "react";

/** One inline token per match: earliest match wins, then the scan continues after it.
 *  `code` is matched first among equals so `**` inside backticks stays literal. */
const INLINE = [
  { re: /`([^`]+)`/, render: (m, key) => <code key={key}>{m[1]}</code> },
  {
    re: /\*\*([^*]+)\*\*/,
    render: (m, key) => <strong key={key}>{inline(m[1], `${key}b`)}</strong>,
  },
  {
    re: /\*([^*]+)\*/,
    render: (m, key) => <em key={key}>{inline(m[1], `${key}i`)}</em>,
  },
  {
    // http(s) only. A javascript: URL through a markdown link is the one classic
    // injection a structural renderer does not stop by itself.
    re: /\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/,
    render: (m, key) => (
      <a key={key} href={m[2]} target="_blank" rel="noreferrer">{inline(m[1], `${key}l`)}</a>
    ),
  },
];

function inline(text, keyBase) {
  const out = [];
  let rest = text;
  let n = 0;
  while (rest) {
    let first = null;
    for (const t of INLINE) {
      const m = t.re.exec(rest);
      if (m && (first === null || m.index < first.m.index)) first = { t, m };
    }
    if (!first) {
      out.push(rest);
      break;
    }
    if (first.m.index > 0) out.push(rest.slice(0, first.m.index));
    out.push(first.t.render(first.m, `${keyBase}-${n++}`));
    rest = rest.slice(first.m.index + first.m[0].length);
  }
  return out;
}

/**
 * Blip prose. `as` names the paragraph element so callers keep their own classes —
 * the summary panel renders paragraphs as `.rdr-side-lede`, the detail panel as
 * `.rdr-lede`, and this component imposes no typography of its own.
 */
export default function Prose({ text, as: P = "p", className, ...rest }) {
  if (!text) return null;
  const blocks = String(text).split(/\n{2,}/).map((b) => b.trim()).filter(Boolean);
  return (
    <>
      {blocks.map((block, i) => {
        const lines = block.split("\n");
        if (lines.every((l) => /^[-*]\s+/.test(l))) {
          return (
            <ul key={i} className={className ? `${className} rdr-prose-list` : "rdr-prose-list"}>
              {lines.map((l, j) => (
                <li key={j}>{inline(l.replace(/^[-*]\s+/, ""), `${i}-${j}`)}</li>
              ))}
            </ul>
          );
        }
        // Single newlines inside a block are soft wraps, exactly as markdown reads them.
        return (
          <P key={i} className={className} {...rest}>
            {inline(lines.join(" "), `${i}`)}
          </P>
        );
      })}
    </>
  );
}

/** The same subset flattened to plain text, for surfaces that cannot carry markup —
 *  the blip-index table cell and anywhere a `title` attribute wants the prose. */
export function plain(text) {
  if (!text) return "";
  return String(text)
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/\*([^*]+)\*/g, "$1")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, "$1")
    .replace(/^[-*]\s+/gm, "")
    .replace(/\n{2,}/g, " — ")
    .replace(/\n/g, " ");
}

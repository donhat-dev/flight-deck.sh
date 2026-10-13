/**
 * Line diff for Edit / MultiEdit / Write.
 *
 * The old view stacked the whole old string above the whole new string, which
 * makes a two-character change look like a rewrite. A unified diff answers the
 * question the reader actually has: what moved.
 *
 * Plain LCS, guarded by size. Transcript hunks are small (a tool result is
 * already capped at 24k chars server-side); anything past the guard falls back
 * to "all removed, then all added", which is what the old view showed anyway.
 */

const MAX_CELLS = 4_000_000; // ~2000x2000 lines

function lcsTable(a, b) {
  const m = a.length, n = b.length;
  const t = new Uint32Array((m + 1) * (n + 1));
  for (let i = m - 1; i >= 0; i--) {
    for (let j = n - 1; j >= 0; j--) {
      t[i * (n + 1) + j] = a[i] === b[j]
        ? t[(i + 1) * (n + 1) + j + 1] + 1
        : Math.max(t[(i + 1) * (n + 1) + j], t[i * (n + 1) + j + 1]);
    }
  }
  return t;
}

/** @returns {Array<{type:'ctx'|'add'|'del', text:string}>} */
function rawDiff(a, b) {
  if (!a.length) return b.map((text) => ({ type: "add", text }));
  if (!b.length) return a.map((text) => ({ type: "del", text }));
  if ((a.length + 1) * (b.length + 1) > MAX_CELLS) {
    return [...a.map((text) => ({ type: "del", text })), ...b.map((text) => ({ type: "add", text }))];
  }
  const n = b.length, t = lcsTable(a, b), out = [];
  let i = 0, j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) { out.push({ type: "ctx", text: a[i] }); i++; j++; }
    else if (t[(i + 1) * (n + 1) + j] >= t[i * (n + 1) + j + 1]) { out.push({ type: "del", text: a[i] }); i++; }
    else { out.push({ type: "add", text: b[j] }); j++; }
  }
  while (i < a.length) out.push({ type: "del", text: a[i++] });
  while (j < b.length) out.push({ type: "add", text: b[j++] });
  return out;
}

// An empty string is zero lines, not one empty line: otherwise writing a new
// file reports one deletion that never happened.
const split = (s) => {
  const t = String(s == null ? "" : s).replace(/\n$/, "");
  return t === "" ? [] : t.split("\n");
};

/**
 * Unified diff rows with line numbers and folded context.
 *
 * @returns {{rows: Array<{type:string, a:?number, b:?number, text:string, count:?number}>,
 *   added: number, removed: number}}
 */
export function unifiedDiff(oldStr, newStr, { context = 3, startLine = 1 } = {}) {
  const raw = rawDiff(split(oldStr), split(newStr));
  const added = raw.filter((r) => r.type === "add").length;
  const removed = raw.filter((r) => r.type === "del").length;

  // Number the rows before folding, so the numbers survive the fold.
  let a = startLine, b = startLine;
  const numbered = raw.map((r) => {
    if (r.type === "ctx") return { ...r, a: a++, b: b++ };
    if (r.type === "del") return { ...r, a: a++, b: null };
    return { ...r, a: null, b: b++ };
  });

  // Keep `context` lines either side of a change; replace the rest with a fold.
  // With no change at all there is nothing to fold around, so show everything -
  // folding it would leave the reader with a diff that says only "42 lines".
  const unchanged = added === 0 && removed === 0;
  const keep = new Array(numbered.length).fill(unchanged);
  numbered.forEach((r, i) => {
    if (r.type === "ctx") return;
    for (let k = Math.max(0, i - context); k <= Math.min(numbered.length - 1, i + context); k++) keep[k] = true;
  });

  const rows = [];
  let run = 0;
  numbered.forEach((r, i) => {
    if (keep[i]) {
      if (run > 0) { rows.push({ type: "fold", a: null, b: null, text: "", count: run }); run = 0; }
      rows.push(r);
    } else run++;
  });
  if (run > 0) rows.push({ type: "fold", a: null, b: null, text: "", count: run });

  return { rows, added, removed };
}

/**
 * ANSI SGR -> render segments.
 *
 * Terminal output arrives already annotated: pytest colours the word PASS, npm
 * colours a warning, docker colours the checkmark. Throwing that away and
 * printing one grey <pre> discards the only structure the producer gave us, so
 * we keep the colours and drop everything else.
 *
 * Only SGR (`ESC[...m`) is interpreted. Cursor moves, erases, scroll regions
 * and OSC titles are stripped: they animate a live terminal and mean nothing in
 * a transcript.
 */

const CSI = /\u001b\[([0-9;:?]*)([A-Za-z])/g;
// OSC ... BEL | ST
const OSC = /\u001b\][^\u0007\u001b]*(?:\u0007|\u001b\\)/g;

const BASIC = ["a0", "a1", "a2", "a3", "a4", "a5", "a6", "a7"];
const BRIGHT = ["b0", "b1", "b2", "b3", "b4", "b5", "b6", "b7"];

/** xterm-256 -> a hex string. 0-15 map onto our own palette by index. */
function color256(n) {
  if (n < 8) return { key: BASIC[n] };
  if (n < 16) return { key: BRIGHT[n - 8] };
  if (n < 232) {
    const i = n - 16;
    const step = (v) => [0, 95, 135, 175, 215, 255][v];
    const r = step(Math.floor(i / 36)), g = step(Math.floor(i / 6) % 6), b = step(i % 6);
    return { hex: `rgb(${r},${g},${b})` };
  }
  const v = 8 + (n - 232) * 10;
  return { hex: `rgb(${v},${v},${v})` };
}

const EMPTY = { key: null, hex: null, bold: false, dim: false, underline: false };

function applySgr(state, params) {
  const codes = params === "" ? [0] : params.split(";").map((p) => parseInt(p, 10) || 0);
  let s = { ...state };
  for (let i = 0; i < codes.length; i++) {
    const c = codes[i];
    if (c === 0) s = { ...EMPTY };
    else if (c === 1) s.bold = true;
    else if (c === 2) s.dim = true;
    else if (c === 4) s.underline = true;
    else if (c === 22) { s.bold = false; s.dim = false; }
    else if (c === 24) s.underline = false;
    else if (c >= 30 && c <= 37) { s.key = BASIC[c - 30]; s.hex = null; }
    else if (c >= 90 && c <= 97) { s.key = BRIGHT[c - 90]; s.hex = null; }
    else if (c === 39) { s.key = null; s.hex = null; }
    else if (c === 38 || c === 48) {
      const mode = codes[i + 1];
      let picked = null;
      if (mode === 5) { picked = color256(codes[i + 2] || 0); i += 2; }
      else if (mode === 2) { picked = { hex: `rgb(${codes[i + 2] | 0},${codes[i + 3] | 0},${codes[i + 4] | 0})` }; i += 4; }
      // 48 is a background; we only paint foregrounds, so it is consumed and dropped
      if (c === 38 && picked) { s.key = picked.key || null; s.hex = picked.hex || null; }
    }
  }
  return s;
}

/**
 * @returns {Array<Array<{text: string, key: ?string, hex: ?string, bold: boolean,
 *   dim: boolean, underline: boolean}>>} one array of segments per line
 */
export function parseAnsi(input) {
  const text = String(input == null ? "" : input).replace(OSC, "").replace(/\r(?!\n)/g, "");
  const lines = [];
  let segs = [];
  let state = { ...EMPTY };
  let last = 0;

  const push = (chunk) => {
    if (!chunk) return;
    const parts = chunk.split("\n");
    parts.forEach((part, i) => {
      if (i > 0) { lines.push(segs); segs = []; }
      if (part) segs.push({ text: part, ...state });
    });
  };

  CSI.lastIndex = 0;
  let m;
  while ((m = CSI.exec(text)) !== null) {
    push(text.slice(last, m.index));
    last = CSI.lastIndex;
    if (m[2] === "m") state = applySgr(state, m[1]);
    // any other final byte is a cursor/erase command: dropped
  }
  push(text.slice(last));
  lines.push(segs);
  return lines;
}

/** True when the text carries at least one SGR colour worth rendering. */
export function hasAnsi(text) {
  return typeof text === "string" && /\u001b\[[0-9;]*m/.test(text);
}

/** Strip every escape, for search, copy and one-line summaries. */
export function stripAnsi(text) {
  return String(text == null ? "" : text).replace(OSC, "").replace(/\u001b\[[0-9;:?]*[A-Za-z]/g, "");
}

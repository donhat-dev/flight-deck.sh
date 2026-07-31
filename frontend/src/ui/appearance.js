/**
 * Appearance — which face and weight each kind of text uses.
 *
 * Three roles, because those are the three that can genuinely be set apart. The
 * system had only two (`--fdx-font-primary`, `--fdx-font-mono`), which meant a
 * column of figures and a row of menu labels were forced to share a face even
 * though they are different jobs. `--fdx-font-label` was split out for that.
 *
 * The choice is applied by writing tokens onto <html>, so it reaches the dashboard,
 * the Radio plane and every proposal entry at once — no view needs to know this
 * module exists.
 *
 * It is stored on the SERVER (`/api/appearance`), which is what makes this config
 * rather than a preference: the choice belongs to the install and survives a
 * cleared browser profile. localStorage is kept as a first-paint cache only, so the
 * page never renders in one face and then swaps while the fetch is in flight. The
 * server is authoritative — if the two disagree, the server wins and the cache is
 * corrected.
 *
 * Every candidate is self-hosted (see fonts.css). Vietnamese coverage is listed
 * per face and is not decoration: session titles come from the user's own projects
 * and are often Vietnamese, so a face that cannot set them will visibly fall back
 * mid-word.
 */

const KEY = "flightdeck.appearance.fonts";

/** Browser default — the one candidate that names no family, so the platform
 *  picks. Kept as a baseline: it is the honest comparison for "is our face
 *  actually better here?" */
const SYSTEM_SANS = "ui-sans-serif, system-ui, sans-serif";
const SYSTEM_MONO = "ui-monospace, SFMono-Regular, monospace";

export const FONTS = [
  {
    id: "satoshi",
    label: "Satoshi",
    stack: '"Satoshi", ui-sans-serif, system-ui, sans-serif',
    kind: "sans",
    vietnamese: "full",
    // Five STATIC cuts, so these five and nothing else. 600 is absent: asking for
    // it resolves to 700, which is why it must not be offered.
    weights: [300, 400, 500, 700, 900],
    note: "MJ build — 74/74 Vietnamese. Five static weights, no 600.",
  },
  {
    id: "space-grotesk",
    label: "Space Grotesk",
    stack: '"Space Grotesk", ui-sans-serif, system-ui, sans-serif',
    kind: "sans",
    vietnamese: "full",
    // Variable wght 300–700: any value in range is real, so the picker offers the
    // round steps inside it rather than pretending 800 exists.
    weights: [300, 400, 500, 600, 700],
    note: "Variable 300–700, latin + vietnamese subsets.",
  },
  {
    id: "outfit",
    label: "Outfit",
    stack: '"Outfit Variable", ui-sans-serif, system-ui, sans-serif',
    kind: "sans",
    vietnamese: "none",
    weights: [100, 200, 300, 400, 500, 600, 700, 800, 900],
    note: "Variable 100–900, but carries no Vietnamese — diacritics fall back.",
  },
  {
    id: "ibm-plex-mono",
    label: "IBM Plex Mono",
    stack: '"IBM Plex Mono", ui-monospace, monospace',
    kind: "mono",
    vietnamese: "full",
    // Only the three weights actually imported. The package ships 100–700, but an
    // un-imported weight is synthesised, which at label sizes reads as smeared.
    weights: [400, 600, 700],
    note: "400/600/700 loaded, with a vietnamese subset.",
  },
  {
    id: "jetbrains-mono",
    label: "JetBrains Mono",
    stack: '"JetBrains Mono", ui-monospace, monospace',
    kind: "mono",
    vietnamese: "full",
    weights: [400, 500, 600, 700, 800],
    note: "Variable 400–800, latin + vietnamese subsets.",
  },
  {
    id: "system-sans",
    label: "Default sans",
    stack: SYSTEM_SANS,
    kind: "sans",
    vietnamese: "platform",
    // The platform decides what it has; anything else may be synthesised.
    weights: [400, 700],
    note: "Whatever the browser picks. The baseline to compare against.",
  },
  {
    id: "system-mono",
    label: "Default mono",
    stack: SYSTEM_MONO,
    kind: "mono",
    vietnamese: "platform",
    weights: [400, 700],
    note: "Whatever the browser picks for monospace.",
  },
];

export const ROLES = [
  {
    id: "primary",
    token: "--fdx-font-primary",
    weightToken: "--fdx-weight-body",
    sizeToken: "--fdx-size-body",
    // Absolute: this is the root of the prose scale, so a px value is the honest
    // control. Headings stay relative to it.
    sizeKind: "px",
    sizes: [12, 13, 14, 15, 16, 17],
    label: "UI & prose",
    describes: "Titles, body copy, buttons — everything read as language.",
    weightNote:
      "Sets the BODY weight. Titles and buttons keep their own scale on purpose — " +
      "one number cannot own a type hierarchy.",
    sizeNote: "Body size in px. Headings scale from it.",
    sample: "FlightDeck Implement · Nghiên cứu công cụ",
    fallback: { font: "satoshi", weight: 400, size: 14 },
  },
  {
    id: "label",
    token: "--fdx-font-label",
    weightToken: "--fdx-weight-label",
    sizeToken: "--fdx-size-label",
    // A SCALE, not a size. Labels run 9/10/11px on purpose; one absolute value
    // would flatten that, so every label site multiplies its own size by this.
    sizeKind: "scale",
    sizes: [0.85, 0.9, 1, 1.1, 1.2, 1.35],
    label: "Menus & labels",
    describes: "Tabs, eyebrows, segmented controls — small caps you navigate by.",
    weightNote: "Applied at every label site, so this one number owns them all.",
    sizeNote: "A multiplier — the 9/10/11px steps keep their relationship.",
    sample: "ON AIR · SPEND · QUOTA HEADROOM",
    fallback: { font: "ibm-plex-mono", weight: 700, size: 1 },
  },
  {
    id: "mono",
    token: "--fdx-font-mono",
    weightToken: "--fdx-weight-figure",
    sizeToken: "--fdx-size-figure",
    // A SCALE for the same reason, harder: figures run from a 10px meta line to a
    // 6rem display numeral. An absolute size would destroy one of the two.
    sizeKind: "scale",
    sizes: [0.9, 0.95, 1, 1.05, 1.1],
    label: "Figures",
    describes: "Every number, with tabular figures so columns line up.",
    weightNote: "Applied to figures via [data-num].",
    sizeNote: "A multiplier — mono faces often run small beside the sans.",
    sample: "$82.53 · 474 turns · 96.8%",
    fallback: { font: "ibm-plex-mono", weight: 400, size: 1 },
  },
];

const byId = (id) => FONTS.find((f) => f.id === id);

export function defaults() {
  return Object.fromEntries(ROLES.map((r) => [r.id, { ...r.fallback }]));
}

/** The CSS value for a size, which differs by role: px is a length, a scale is a
 *  bare multiplier the stylesheets feed into calc(). */
export function sizeValue(role, size) {
  return role.sizeKind === "px" ? `${size}px` : String(size);
}

/** Coerce one role to something renderable: a face that exists, a weight that face
 *  really carries, and a size from the role's own list. An unavailable weight is
 *  silently rounded by the browser — the exact "looks slightly wrong, no error"
 *  failure this catalogue exists to prevent. */
function coerce(role, value) {
  const font = byId(value?.font) || byId(role.fallback.font);
  const wantedWeight = Number(value?.weight) || role.fallback.weight;
  // Exact if the face has it, otherwise the NEAREST it does have. Falling back to
  // the role default would throw away the intent: switching Satoshi 900 to IBM Plex
  // Mono should land on 700, not on 400 — the user asked for heavy.
  const weight = font.weights.includes(wantedWeight)
    ? wantedWeight
    : font.weights.reduce((best, w) =>
        Math.abs(w - wantedWeight) < Math.abs(best - wantedWeight) ? w : best);
  const wantedSize = Number(value?.size) || role.fallback.size;
  const size = role.sizes.includes(wantedSize)
    ? wantedSize
    : role.sizes.reduce((best, v) =>
        Math.abs(v - wantedSize) < Math.abs(best - wantedSize) ? v : best);
  return { font: font.id, weight, size };
}

export function normalise(raw) {
  return Object.fromEntries(ROLES.map((r) => [r.id, coerce(r, raw?.[r.id])]));
}

export function isSame(a, b) {
  return JSON.stringify(normalise(a)) === JSON.stringify(normalise(b));
}

export function loadCached() {
  try {
    const saved = JSON.parse(localStorage.getItem(KEY) || "null");
    if (!saved) return null;
    // The pre-weight shape stored a bare font id per role. Migrate rather than
    // discard, so an existing choice is not silently reset.
    if (typeof Object.values(saved)[0] === "string") {
      return normalise(Object.fromEntries(
        ROLES.map((r) => [r.id, { font: saved[r.id], ...r.fallback }])));
    }
    return normalise(saved);
  } catch {
    return null;
  }
}

function cache(choice) {
  try {
    if (choice) localStorage.setItem(KEY, JSON.stringify(choice));
    else localStorage.removeItem(KEY);
  } catch {
    /* a blocked store must not stop the choice from applying */
  }
}

/**
 * Write the choice onto <html>, or REMOVE the overrides when given null.
 *
 * Removing is what "reset to source style" means: with no inline properties the
 * stylesheets' own values apply again. Writing the current design's values instead
 * would look identical today and silently pin the app to them the moment a
 * stylesheet changed.
 */
export function apply(choice) {
  if (typeof document === "undefined") return choice;
  const root = document.documentElement;
  for (const r of ROLES) {
    if (!choice) {
      root.style.removeProperty(r.token);
      root.style.removeProperty(r.weightToken);
      root.style.removeProperty(r.sizeToken);
      continue;
    }
    const value = choice[r.id];
    root.style.setProperty(r.token, byId(value.font).stack);
    root.style.setProperty(r.weightToken, String(value.weight));
    root.style.setProperty(r.sizeToken, sizeValue(r, value.size));
  }
  return choice;
}

/** Persist to the server, and only cache what the server accepted — caching the
 *  request instead would let a rejected value look saved until the next reload. */
export async function save(choice) {
  const body = normalise(choice);
  apply(body);
  try {
    const res = await fetch("/api/appearance", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`PUT /api/appearance: ${res.status}`);
    const saved = normalise((await res.json()).appearance);
    cache(saved);
    return { ok: true, choice: saved };
  } catch (e) {
    return { ok: false, error: String(e), choice: body };
  }
}

/** Drop the stored config so the stylesheets' own values apply again. */
export async function clear() {
  apply(null);
  cache(null);
  try {
    const res = await fetch("/api/appearance", { method: "DELETE" });
    if (!res.ok) throw new Error(`DELETE /api/appearance: ${res.status}`);
    return { ok: true };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

/**
 * Apply the cache immediately, then reconcile with the server.
 *
 * Called by every entry before React mounts. The cache makes the first paint
 * correct; the fetch makes it *true*, because the server is where the config lives.
 * With nothing stored anywhere, no property is written at all and the source style
 * stands.
 */
export function init() {
  const cached = loadCached();
  apply(cached);
  if (typeof fetch === "undefined") return cached;
  fetch("/api/appearance")
    .then((r) => (r.ok ? r.json() : null))
    .then((body) => {
      const server = body?.appearance ? normalise(body.appearance) : null;
      if (JSON.stringify(server) === JSON.stringify(cached)) return;
      apply(server);
      cache(server);
    })
    .catch(() => {
      /* offline or starting up — whatever was cached is already applied */
    });
  return cached;
}

export { byId };

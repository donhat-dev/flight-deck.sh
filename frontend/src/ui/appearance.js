/**
 * Appearance — which face each kind of text uses.
 *
 * Three roles, because those are the three that can genuinely be set apart. The
 * system had only two (`--fdx-font-primary`, `--fdx-font-mono`), which meant a
 * column of figures and a row of menu labels were forced to share a face even
 * though they are different jobs. `--fdx-font-label` was split out for that.
 *
 * The choice is applied by writing the tokens onto <html>, so it reaches the
 * dashboard, the Radio plane and every proposal entry at once — no view needs to
 * know this module exists. It is stored in localStorage and applied BEFORE React
 * mounts, so the page never paints in one face and then swaps.
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
    note: "MJ build — 74/74 Vietnamese. Five static weights, no 600.",
  },
  {
    id: "space-grotesk",
    label: "Space Grotesk",
    stack: '"Space Grotesk", ui-sans-serif, system-ui, sans-serif',
    kind: "sans",
    vietnamese: "full",
    note: "Variable 300–700, latin + vietnamese subsets.",
  },
  {
    id: "outfit",
    label: "Outfit",
    stack: '"Outfit Variable", ui-sans-serif, system-ui, sans-serif',
    kind: "sans",
    vietnamese: "none",
    note: "Variable, but carries no Vietnamese — diacritics fall back.",
  },
  {
    id: "ibm-plex-mono",
    label: "IBM Plex Mono",
    stack: '"IBM Plex Mono", ui-monospace, monospace',
    kind: "mono",
    vietnamese: "full",
    note: "400/600/700 loaded, with a vietnamese subset.",
  },
  {
    id: "jetbrains-mono",
    label: "JetBrains Mono",
    stack: '"JetBrains Mono", ui-monospace, monospace',
    kind: "mono",
    vietnamese: "full",
    note: "Variable 400–800, latin + vietnamese subsets.",
  },
  {
    id: "system-sans",
    label: "Default sans",
    stack: SYSTEM_SANS,
    kind: "sans",
    vietnamese: "platform",
    note: "Whatever the browser picks. The baseline to compare against.",
  },
  {
    id: "system-mono",
    label: "Default mono",
    stack: SYSTEM_MONO,
    kind: "mono",
    vietnamese: "platform",
    note: "Whatever the browser picks for monospace.",
  },
];

export const ROLES = [
  {
    id: "primary",
    token: "--fdx-font-primary",
    label: "UI & prose",
    describes: "Titles, body copy, buttons — everything that is read as language.",
    sample: "FlightDeck Implement · Nghiên cứu công cụ",
    fallback: "satoshi",
  },
  {
    id: "label",
    token: "--fdx-font-label",
    label: "Menus & labels",
    describes: "Tabs, eyebrows, segmented controls — small caps you navigate by.",
    sample: "ON AIR · SPEND · QUOTA HEADROOM",
    fallback: "ibm-plex-mono",
  },
  {
    id: "mono",
    token: "--fdx-font-mono",
    label: "Figures",
    describes: "Every number, with tabular figures so columns line up.",
    sample: "$82.53 · 474 turns · 96.8%",
    fallback: "ibm-plex-mono",
  },
];

const byId = (id) => FONTS.find((f) => f.id === id);

export function defaults() {
  return Object.fromEntries(ROLES.map((r) => [r.id, r.fallback]));
}

export function load() {
  const base = defaults();
  try {
    const saved = JSON.parse(localStorage.getItem(KEY) || "{}");
    for (const r of ROLES) {
      // An unknown id is dropped rather than trusted: a stale value from a font
      // that no longer ships would otherwise resolve to nothing at all.
      if (byId(saved[r.id])) base[r.id] = saved[r.id];
    }
  } catch {
    /* a blocked or corrupt store just means defaults */
  }
  return base;
}

export function save(choice) {
  try {
    localStorage.setItem(KEY, JSON.stringify(choice));
  } catch {
    /* a full store must not stop the UI from applying the choice */
  }
}

/** Write the choice onto <html>. Everything else reads the tokens, so no view
 *  needs to re-render for the change to land. */
export function apply(choice = load()) {
  if (typeof document === "undefined") return choice;
  const root = document.documentElement;
  for (const r of ROLES) {
    const font = byId(choice[r.id]) || byId(r.fallback);
    root.style.setProperty(r.token, font.stack);
  }
  return choice;
}

/** Called by every entry before React mounts, so the first paint is already in
 *  the chosen faces. */
export function init() {
  return apply(load());
}

export { byId };

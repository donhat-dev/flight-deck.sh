/** @type {import('tailwindcss').Config} */
// FlightDeck Night retheme. Rather than rewrite every utility class across the
// app, the two palette families the app actually uses are remapped at the
// source: `emerald` -> the coral SIGNAL ramp (the single brand accent), and
// `zinc` -> the FlightDeck neutral ramp (void ground -> bone-white text). Every
// existing `bg-zinc-900` / `text-emerald-400` therefore adopts the new system
// with zero markup churn. Semantic families (amber=warn, rose=critical,
// sky=environment, violet=thinking) are intentionally left untouched — per the
// design system, instrument state colors live separately from the accent.
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      fontFamily: {
        // display / UI / headings
        sans: ['"Outfit Variable"', "ui-sans-serif", "system-ui", "sans-serif"],
        // instrument labels + all numeric values (tabular)
        mono: ['"IBM Plex Mono"', "ui-monospace", "SFMono-Regular", "monospace"],
      },
      colors: {
        // neutral ground -> text. Bound to CSS channel vars so the app can
        // theme-switch Night (default) <-> Day via [data-theme]; alpha
        // modifiers (bg-zinc-900/40) work through <alpha-value>. Night: 950 =
        // void .. 100 = bone-white; Day inverts (see index.css [data-theme=day]).
        zinc: {
          50:  "rgb(var(--z-50) / <alpha-value>)",
          100: "rgb(var(--z-100) / <alpha-value>)",
          200: "rgb(var(--z-200) / <alpha-value>)",
          300: "rgb(var(--z-300) / <alpha-value>)",
          400: "rgb(var(--z-400) / <alpha-value>)",
          500: "rgb(var(--z-500) / <alpha-value>)",
          600: "rgb(var(--z-600) / <alpha-value>)",
          700: "rgb(var(--z-700) / <alpha-value>)",
          800: "rgb(var(--z-800) / <alpha-value>)",
          900: "rgb(var(--z-900) / <alpha-value>)",
          950: "rgb(var(--z-950) / <alpha-value>)",
        },
        // the single accent -> coral SIGNAL, also var-bound (Day deepens it for
        // legible coral text on the light ground).
        emerald: {
          200: "rgb(var(--e-200) / <alpha-value>)",
          300: "rgb(var(--e-300) / <alpha-value>)",
          400: "rgb(var(--e-400) / <alpha-value>)",
          500: "rgb(var(--e-500) / <alpha-value>)",
          600: "rgb(var(--e-600) / <alpha-value>)",
          700: "rgb(var(--e-700) / <alpha-value>)",
        },
        // FlightDeck token aliases (for new components that opt in)
        fd: {
          void: "#050505", raise: "#0B0C10", "raise-2": "#101218",
          text: "#F4F3EF", dim: "#9C9B96", faint: "#605F5C",
          coral: "#D93A18", "coral-hot": "#FF5133", "coral-deep": "#B92E0F",
          sky: "#4E93CC", "sky-deep": "#2E6FB0", bone: "#EFEDE6", ink: "#17191E",
        },
      },
      borderRadius: { pill: "999px" },
      keyframes: {
        "fade-up": {
          "0%": { opacity: "0", transform: "translateY(10px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        "live-pulse": {
          "0%,100%": { opacity: "1" },
          "50%": { opacity: "0.35" },
        },
        "mesh-drift": {
          "0%": { transform: "translate(-1.2%, -0.8%)" },
          "100%": { transform: "translate(1.2%, 0.8%)" },
        },
      },
      animation: {
        // one easing curve for the whole system: cubic-bezier(.32,.72,0,1)
        "fade-up": "fade-up 0.55s cubic-bezier(.32,.72,0,1) both",
        "live-pulse": "live-pulse 2s ease-in-out infinite",
        "mesh-drift": "mesh-drift 46s ease-in-out infinite alternate",
      },
    },
  },
  plugins: [],
};

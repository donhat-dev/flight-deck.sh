# FlightDeck Day — design system (daylight counterpart)

The **light** side of the FlightDeck brand. Where **FlightDeck Night** is a
cockpit at night (dark ground, instruments glow), **FlightDeck Day** is the
same deck seen in daylight: a printed flight chart on a warm desk. The signal
stays coral; the ground inverts to warm bone; the glow goes away, because
**daylight does not glow**.

## Scope — where Day is used

Day covers light surfaces: the landing page, docs, exports, print, any embed on
a light host — and, as of the in-app theme work, an **opt-in dashboard theme**.
**Night stays the default** (the data-dense dashboard is built for a dark
instrument panel); Day is a deliberate toggle beside the range control, never
auto-applied and never flipped without user action, and the choice persists per
user (localStorage).

## Brand law (unchanged)

> **"Night ops inverts the ground, never the signal."**

Coral is coral on any ground. In Day it sits on warm bone instead of void;
the ground swap is the *only* structural change. Sky remains an environment color
(panels/charts), never a button or link.

## Metaphor discipline

Every Day decision should survive: **"a flight chart in daylight, ink on warm
paper."** Warm, calm, legible, matte. No glow, no neon, no pure-white glare.

## How Day differs from Night

| Aspect | Night (app) | Day (marketing) |
|---|---|---|
| Page ground | OLED void `#050505` | warm bone `#E8E3D8` (matte paper, **not white**) |
| Primary ink | bone-white `#F4F3EF` | soft charcoal `#2E2C28` (**not pure black**) |
| Signal accent | coral (same) | coral (same) |
| Environment | sky `#4E93CC` | sky `#3E7CB1` (deepened for light-bg legibility) |
| Elevation | hairline + inset top-highlight + glow | **soft ink drop shadow**, subtle top highlight, no glow |
| Atmosphere | dark mesh orbs + fine grain | **warm daylight wash** (amber/coral/sky, low alpha) + paper grain |
| Glow | reserved for 1–2 accent marks | **none** — daylight has no glow |
| Contrast | very high (17:1 body) | **moderate** (~10:1 body) — comfortable, still AA |
| Mood | cockpit at night | chart on a desk in daylight |

Everything else is **shared** with Night: type families, radii, spacing,
the single easing curve, the double-bezel geometry, the wordmark/roundel forms.

## Design tokens

Ship as CSS custom properties (prefix `--fdd-` so Day and Night can coexist in
the same stylesheet, e.g. a landing that links back into the Night app).
Components consume tokens, never raw hex.

```css
:root {
  /* ground — warm, matte, deliberately NOT white (low glare) */
  --fdd-ground:  #E8E3D8;                 /* page ground (warm bone paper) */
  --fdd-raise:   #F2EEE5;                 /* raised card / core (lighter paper) */
  --fdd-raise-2: #DED8CB;                 /* recessed wells / inputs */
  --fdd-glass:   rgba(46,44,40,0.035);    /* bezel shell fill (warm ink tint) */
  --fdd-hair:    rgba(46,44,40,0.14);     /* primary hairline */
  --fdd-hair-2:  rgba(46,44,40,0.08);     /* soft hairline / dividers */

  /* ink — soft charcoal, NOT pure black (moderate contrast) */
  --fdd-ink:     #2E2C28;                 /* primary text  (~10.5:1 on ground) */
  --fdd-dim:     rgba(46,44,40,0.66);     /* secondary text (~5:1) */
  --fdd-faint:   rgba(46,44,40,0.42);     /* metadata ONLY, never body */

  /* signal — the one accent, unchanged family */
  --fdd-coral:      #D93A18;              /* interactive fill (white text on it) */
  --fdd-coral-deep: #B02C0C;              /* hover fill + coral TEXT on light (legible) */
  --fdd-coral-mark: #FF5133;              /* small graphic marks only (dots, ticks) */

  /* environment — panels / charts only, never buttons/links */
  --fdd-sky:      #3E7CB1;
  --fdd-sky-deep: #2E6395;

  /* elevation — soft ink drop shadows (no glow in daylight) */
  --fdd-shadow-sm: 0 1px 2px rgba(60,52,40,0.12);
  --fdd-shadow:    0 8px 20px -12px rgba(60,52,40,0.26);
  --fdd-shadow-lg: 0 22px 48px -22px rgba(60,52,40,0.32);
  --fdd-core-hi:   inset 0 1px 0 rgba(255,255,255,0.55);   /* paper top highlight */

  /* shape — SHARED with Night */
  --fdd-r-shell: 2rem;
  --fdd-r-core:  calc(2rem - 6px);
  --fdd-r-chip:  1.25rem;
  --fdd-r-pill:  999px;

  /* motion — SHARED single curve */
  --fdd-ease: cubic-bezier(.32,.72,0,1);

  /* NO --fdd-glow: daylight does not glow. Emphasis = weight + coral, not light. */
}
```

**Typography** — identical to Night: Outfit (display/UI, 400–900), IBM Plex
Mono (labels, all numeric readouts, `tabular-nums`). Display Outfit 800,
`letter-spacing:-.025em`; mono labels uppercase, `.18em–.24em`, 10–11px.

## Atmosphere (page chrome)

- **Daylight wash**: 2–3 fixed radial-gradients at low alpha — warm amber top,
  a soft coral, a cool sky — reading as sun through a window. Much lower
  intensity than Night's mesh (alpha ≤ .10). Optional 46s drift, reduced-motion
  gated, transform only.
- **Paper grain**: the same feTurbulence overlay as Night but at `opacity:.02`
  with `mix-blend-mode: multiply` so it reads as paper tooth, not TV noise.
- No `backdrop-filter` on scrolling content. Cards are solid paper, not glass.

## Component rules (Day variants)

### 1. Double-bezel card
Same geometry as Night, re-lit for paper: `.shell` = `--fdd-glass` fill + 1px
`--fdd-hair-2` + `--fdd-shadow-sm`; `.core` = `--fdd-raise` + `--fdd-core-hi`
top highlight + `--fdd-shadow` for lift. Concentric radii via calc. The card
reads as a raised paper tile catching soft overhead light — **no inset dark
well, no glow**.

### 2. Buttons
- **Primary (CTA)**: coral pill, white mono-uppercase label, the button-in-
  button trailing `↗` in a nested `rgba(0,0,0,.18)` circle; hover
  `--fdd-coral-deep`; active `scale(.98)`. Same "Open the deck" pattern.
- **Ghost link**: mono uppercase, `--fdd-dim`, 1px bottom hairline → ink +
  `--fdd-coral-deep` border on hover.
- Coral **text** on light must use `--fdd-coral-deep` (`#B02C0C`), not the
  fill coral, to stay ≥ 4.5:1.

### 3. Wordmark on light
"FlightDeck" in Outfit 800, ink color; the `i` dot is a coral delta triangle
in `--fdd-coral` **with no glow filter**. At display sizes the stem carries the
dashed runway centerline in `rgba(46,44,40,.55)` (dark dashes on the light
stem). Roundel: sky-over-bone with an ink horizon, coral wings-level bar + dot,
ink ring — the Day inverse of the Night roundel.

### 4. Instruments (gauges & readings)
- **Dial**: tick arc in `rgba(46,44,40,.28)`; needle in `--fdd-coral` (solid,
  **no glow** — a painted needle in daylight). Same rest→value sweep on first
  view via IntersectionObserver.
- **Reading block**: mono 600 value in ink + mono 400 label in `--fdd-faint`.
- Thresholds (ok/warn/critical) use their own semantic hues, separate from the
  coral accent, tuned darker for the light ground.

### 5. Charts
Series order: coral primary, sky secondary, ink tertiary; grid `--fdd-hair-2`;
axis labels mono `--fdd-faint`; tooltip = small paper chip (`--fdd-raise` +
hairline + `--fdd-shadow-sm`). Tabular-nums everywhere.

## Accessibility (WCAG 2.2 AA — and the moderate-contrast rationale)

The ground is warm bone and the ink is soft charcoal **on purpose**: pure black
on pure white (21:1) is glare-harsh for a marketing read. Day targets a
**comfortable ~10:1** for body — well above the 4.5:1 minimum, below the harsh
ceiling. Verified pairs (keep):

- `--fdd-ink` on `--fdd-ground` ≈ **10.5:1** (body, comfortable)
- `--fdd-dim` on `--fdd-ground` ≈ **5:1** (secondary, passes AA)
- white on `--fdd-coral` ≈ **4.6:1** (CTA label)
- `--fdd-coral-deep` on `--fdd-ground` ≈ **5.2:1** (coral text/links)
- `--fdd-faint` (≈ 3:1) is **decorative/metadata only**, never body or actions.

Keyboard-first: visible focus ring `outline: 2px solid var(--fdd-coral-deep);
outline-offset: 3px` on every interactive element. Touch targets ≥ 44px.
`prefers-reduced-motion` collapses wash drift + gauge sweep to final state.

## Rules: Do
- Consume `--fdd-*` tokens; no raw hex in components.
- Keep the ground warm and matte; keep ink soft — moderate contrast is a feature.
- Coral is the only accent; sky only in panels/charts/roundel.
- Soft ink drop shadows for lift; solid paper surfaces.

## Rules: Don't
- **No glow anywhere** (that's Night's language).
- No pure `#FFFFFF` ground and no pure `#000000` ink.
- No glass/`backdrop-filter` cards; no dark inset wells.
- No sky-colored buttons/links; no new easing/radii/spacing outside the shared scale.
- Keep Night the default in-app; Day is opt-in (never auto-applied, never a mid-session flip without a user toggle).

## QA checklist (before shipping a Day surface)
- [ ] Ground is warm bone (not white); ink is soft charcoal (not black).
- [ ] Zero glow; lift comes from soft ink drop shadows only.
- [ ] Coral is the sole accent; coral **text** uses `--fdd-coral-deep`.
- [ ] Body ≥ 4.5:1 and ≤ ~11:1 (comfortable band); faint tier not used for essential text.
- [ ] Numbers mono + tabular-nums; labels mono uppercase tracked.
- [ ] Focus ring, ≥44px targets, reduced-motion collapse all present.
- [ ] Zero em/en-dashes in visible copy; middle dot `·` ≤ 1 per line.

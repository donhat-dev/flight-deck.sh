# Design System: FlightDeck Interface Contract

Version: 1.0  
Scope: FlightDeck product surfaces and the 18-component Interface Kit  
Reference implementation: `frontend/src/index.css`, `frontend/src/ui/FlightComponents.jsx`  
Behavior and accessibility contract: `docs/flightdeck-component-system.md`

This file is the visual source of truth for humans, coding agents, and screen-generation tools. New screens must feel native to FlightDeck before they feel novel. If a token value changes in code, update this file in the same change.

**Single source, as of 2026-09-07. Flat since the same day** — the four-rung offset ladder was written, trialled for an afternoon and reverted. The Night Ops tokens are the baseline, depth is gone, §5 is now the flat state contract and §4 carries the radius split. The ladder is preserved in `showcases/flightdeck-depth-unification.html` and `OUTDATED_CHOICES.md`, not in the code. The parallel `design-system-flightdeck-night` skill is retired and moved to `orphans/`; its Night Ops direction (Outfit, `#050505`, double-bezel, glow, pill radii) is recorded in `OUTDATED_CHOICES.md` for reading older artifacts and the `pencil-new.pen` canvas history. Nothing else describes FlightDeck's visual language. Where this file and the code disagree, **the code wins and this file gets corrected** — `frontend/src/ui/compositionLint.test.jsx` is the executable half of this contract, and its rule ids are cited below.

## 1. Visual Theme and Atmosphere

FlightDeck is a warm, tactile operational instrument: the precision of an aircraft checklist combined with the clarity of a printed technical manual. It is dark without becoming cyberpunk, expressive without becoming decorative, and dense without feeling cramped.

- **Density: 8/10 — Cockpit Dense.** Operational data is compact, aligned, and scan-first. Space separates decisions, not every individual fact.
- **Variance: 6/10 — Offset Asymmetric.** Heroes and editorial sections use uneven splits, offset alignment, and controlled negative space. Product data remains grid-disciplined.
- **Motion: 5/10 — Fluid and Restrained.** Motion confirms state, sequence, or live activity. Static controls do not move merely to attract attention.
- **Material language:** warm paper, charcoal ink, coral signal, hairline rules, pill-shaped actions on square panels, flat surfaces. Nothing in the product lifts off the page.
- **Lighting direction:** none. There is no physical shadow in the product. Separation comes from a hairline, a change of ground value, or type weight, in that order.
- **Surface philosophy:** joined planes and rules, never floating cards. Elevation is reserved for a true overlay, which has to separate from what it covers; it is not available to controls, panels or cards.

The result should resemble a working control deck rendered by an editorial design studio, never a generic SaaS dashboard.

## 2. Color Palette and Functional Roles

Coral is the only product accent. **Pink is a card tint — a background, never a shadow and never text** (`compositionLint` C6a fires on either). **Orange is both a face and a depth**, because Day is an orange key with a dark offset and Night is the reverse; treating orange as depth-only was the previous contract and is no longer true. Neither is a decorative text colour, a navigation colour, or an arbitrary highlight.

### Night palette

- **Void Canvas** (`#080807`) — primary page background; never use pure black.
- **Instrument Surface** (`#11110F`) — cards, joined panels, input ground.
- **Raised Instrument Surface** (`#191815`) — nested controls, selected data regions, disabled control faces.
- **Warm Paper** (`#F4EDE0`) — primary text and inverse surfaces.
- **Muted Paper** (`#AAA195`) — descriptions, metadata, inactive labels.
- **Inverse Ink** (`#201D18`) — text placed on Warm Paper.
- **Hairline Rule** (`rgba(244, 237, 224, 0.22)`) — internal separators.
- **Strong Rule** (`rgba(244, 237, 224, 0.42)`) — component boundaries and grid frames.
- **Coral Signal** (`#E84D2A`) — live signal, selected state, and high-level emphasis.
- **Action Face** (`#CF3F21`) — primary button surface with AA-compliant light text.
- **Action Hover** (`#BD351B`) — primary control hover face.
- **Action Pressed** (`#A92D16`) — primary control pressed face.
- **Ink Frame** (`#211812`) — the structural border separating a control face from its depth.
- **Card Tint** (`#F47F96`, token `--fdx-card-tint`) — selected and featured card backgrounds. Background only.
- **Orange Depth** (`#FF9A35`) — secondary, loading, and error control offset.
- **Muted Depth** (`#4C4139`) — disabled control offset.
- **Critical** (`#F16D74`) — error status and inline validation.
- **Warning** (`#E6B85C`) — delayed or cautionary state.
- **Positive** (`#62C59B`) — live, connected, and completed state.
- **Focus Signal** (`#FF7355`) — keyboard focus outline only.
- **Environment Blue** (`#4E93CC`, deeper `#3E7CB1` / `#2E6FB0`) — panels, chart series and the roundel horizon **only**. Never a button, a link, a badge or a navigation accent, and never mixed into the neutral ramp. Listed here because the code has used it since before this file existed; it is an environment colour, not a second accent.

### Day palette

- **Paper Canvas** (`#F1EADB`) — primary page background.
- **Light Instrument Surface** (`#F8F3E8`) — cards and fields.
- **Raised Paper** (`#E7DCC8`) — nested and disabled surfaces.
- **Dark Ink** (`#201D18`) — primary text and inverse surface.
- **Muted Ink** (`#6D655A`) — descriptions and metadata.
- **Day Coral Signal** (`#D94625`) — active product accent.
- **Day Action Face** (`#C73A1E`) — primary button face.
- **Day Card Tint** (`#E96884`) — selected and featured card backgrounds. Background only.
- **Day Orange Depth** (`#ED881F`) — loading, secondary, and error offset depth.
- **Day Positive** (`#1F7052`) — live and success.
- **Day Warning** (`#865D0C`) — caution.
- **Day Critical** (`#A82E3D`) — errors.
- **Day Focus** (`#B42F12`) — keyboard focus.
- **Day Environment Blue** (`#2E6395`) — same scope as Night.

### Color rules

1. Use Coral Signal for one primary decision or one active state in a local region.
2. Never use the Card Tint as a shadow, body text, badge, chart series, or navigation accent. Never use Orange as body text or a chart series; as a control face or a control depth it is correct.
3. Status colors always include a label, value, icon shape, or position cue.
4. Do not use blue-purple gradients, neon edges, bloom, or outer glows.
5. Do not mix cool blue-gray neutrals into the warm paper and ink family. The Environment Blue is not a neutral and is scoped by its own rule above.
6. Avoid decorative gradients. Ambient color may use a restrained radial wash below 12% opacity.

## 3. Typography Architecture

- **Display and UI:** `Satoshi`, then `Helvetica Neue`, then `Outfit Variable`, sans-serif.
- **Operational mono:** `IBM Plex Mono`, then `ui-monospace`, monospace.
- **Inter is banned.** Generic serif fonts are banned. Serif is not part of the FlightDeck product language.
- **Display headings:** weight 700, line-height `0.93–0.98`, letter-spacing `-0.035em` to `-0.055em`.
- **Section headings:** `clamp(2.7rem, 5.5vw, 6.4rem)` with a maximum width near 18 characters.
- **Body:** minimum `1rem` where space allows, line-height `1.55–1.65`, maximum width 65 characters.
- **Compact control labels:** `0.74rem–0.88rem`, weight 600–700.
- **Metadata:** IBM Plex Mono, `0.58rem–0.68rem`, weight 600, letter-spacing `0.07em–0.12em`.
- **Numbers, ports, timestamps, percentages, and identifiers:** always use the mono face with tabular figures.
- Use sentence case for headings and actions. Uppercase is reserved for compact operational metadata.
- Apply `text-wrap: balance` to large headings and `text-wrap: pretty` to descriptive copy.

Typography must create hierarchy before color or decoration is introduced.

## 4. Spatial Tokens and Shape

Use only the established spacing scale:

- `0.25rem` — micro separation
- `0.5rem` — label-to-control or tightly related detail
- `0.75rem` — compact control rhythm
- `1rem` — standard internal gap
- `1.5rem` — card padding and related component groups
- `2rem` — large internal separation
- `3rem` — section-heading rhythm
- `4rem` — compact section separation
- `6rem` — major section separation
- `8rem` — editorial section separation

Shape rules:

Radius says what a thing **is**, not how important it is. Two families, and an element belongs to exactly one:

- **Actions are pills** (`--fdx-radius-control`, `999px`): button, icon button, input field, select, toggle and its track, segmented control, tab, filter pill, badge, pagination control. If a pointer press changes state, it is a pill.
- **Everything else is `5px`** (`--fdx-radius-block`): panel, card, table, table row, group header, popover, sheet, rail, chip, widget slot, code block. If it receives content, it is a block.
- Floating overlays may use `10px` (`--fdx-radius-md`).
- Status dots stay circular, because their geometry is the state.
- Product blocks are joined into rule grids. A rounded floating card is exceptional and needs a reason.

The pair is the point: a pill beside a `5px` panel reads as an action against a surface, with no depth spent. A `5px` button and a pill-shaped table both break the distinction and are defects.

## 5. Control State Contract

Controls are flat. A control separates from its ground by **fill, a one-pixel rule, and its pill shape** — never by a shadow. Two layers only:

1. **Face** — the state-bearing surface.
2. **Rule** — a one-pixel hairline giving the control an edge against its ground.

### 5.1 How state is expressed

The ladder that offset shadows used to carry is carried by ground value instead. Each step is one surface up the ground scale from §2, so no new token is needed.

| State | Face | Rule | Extra |
|---|---|---|---|
| Rest | Instrument Surface | Hairline | none |
| Hover | Raised Instrument Surface | Strong Rule | none |
| Active, pressed | Raised Instrument Surface | Strong Rule | the press is confirmed by colour, not by travel |
| Selected, current | Raised Instrument Surface | Strong Rule | a two-pixel coral **inset** bar on the leading edge |
| Focus-visible | unchanged | unchanged | `outline: 2px solid Coral Signal; outline-offset: 2px` |
| Disabled | Instrument Surface at 40% | Hairline | `pointer-events: none`, label stays legible |
| Loading | unchanged | unchanged | label swaps to a mono `WORKING` with a pulsing dot; repeat activation refused |

A primary action carries the Action Face fill. That is what makes it primary — one per region, and it needs no height to say so.

### 5.2 Inset is not depth

`inset` shadows stay legal and are the only shadow form a control may carry: the two-pixel coral leading bar on a selected row, the four-pixel coral rule under an open tab, the one-pixel warm highlight on a light surface. They describe an edge inside the element and cast nothing. `compositionLint` allows `inset` and fires on every outset form.

### 5.3 The one exception

A true overlay may carry a soft shadow, because it separates from live content underneath rather than from its own ground. Today that is `.fdx-console` alone, and the exemption is declared where the code is, with a `composition-lint-allow` marker and a reason. A panel, card or popover that merely wants to look important does not qualify.

### 5.4 Invariants

No outset shadow anywhere except the declared overlay. No blurred shadow on any ground plane. No control travel on press. Disabled and loading keep the exact footprint of rest, so nothing reflows when state changes.

## 6. Component Inventory and Styling

The public contract contains exactly 19 components. New primitives require an implementation, a product example, responsive rules, keyboard behavior, tests, and documentation before entering this inventory.

### Actions

#### Button

- Pill radius, one-pixel rule, no shadow (§4, §5).
- Primary: Action Face, warm-white label. One per region, and the fill is what says so.
- Secondary: Instrument Surface, Warm Paper rule.
- Inverse: Warm Paper face, Inverse Ink label.
- Error: deep critical face, warm-white label.
- Loading keeps the footprint and shows a compact activity indicator without accepting repeat activation.
- Active state changes fill and rule. It does not move.
- Labels are verb-first and describe the result.

#### IconButton

- Uses the same flat grammar in a pill-shaped 44-pixel target.
- Requires an accessible label and title.
- One icon only. The 1.7–2px stroke rule scopes to the line icons in `ui/icons.jsx`; a PixelMark is a fill and is exempt.

#### PixelMark

- Navigation and mode marks are 8x8 filled grids drawn at 16px, so one cell is exactly two device pixels and the edges stay crisp (`shapeRendering: crispEdges`).
- Authored as an ASCII grid and compiled to a single `<path>` at module load, so a mistyped grid throws on import instead of drawing a wrong-but-plausible shape. Reference: `frontend/src/ui/PixelMark.jsx`.
- No icon font and no icon dependency. A mark that cannot be read at 16px does not belong in the set.
- The mark sits in a `5px` chip carrying its own fill and rule, no shadow. A chip is a mark, not an action; the nav row around it is the action and takes the pill. The chip is what survives a rail collapsing to 76px, because the label does not.

### Input

#### Field

- Visible mono label above the input.
- Hint or inline error below; placeholders never replace labels.
- Minimum height 44px, square edge, one-pixel rule.
- Focus uses a 2.25px Focus Signal outline with a two-pixel offset.

#### SelectField

- Retains native select behavior and platform selection UI.
- Uses a semantic chevron, not a decorative icon package.
- Error, disabled, focus, and hint behavior match Field.

#### TextAreaField

- Minimum visual height approximately seven rem.
- Vertical resizing only.
- Long text scrolls internally and never creates page overflow.

#### CheckField

- Retains a native checkbox in the accessibility tree.
- Custom mark uses a one-pixel rule at `5px` radius and a coral fill when checked. A checkbox is a mark, not an action, so it does not take the pill.
- Use for independent selection or explicit acknowledgement.

#### Toggle

- Uses `role="switch"` and an exposed checked state.
- Use for immediate preference changes, never deferred consent.
- Knob position and surface color communicate state together.

### Navigation

#### Tabs

- Joined tab row with one selected inverse surface and a four-pixel coral inset rule.
- One active tab panel with stable minimum height.
- Arrow keys move between tabs; Home and End jump to boundaries.
- On mobile, the trigger row may scroll internally. The page must not scroll horizontally.

#### SegmentedControl

- Two to five joined choices with no gaps.
- Selected item uses Coral Signal and a readable label.
- Arrow, Home, and End keys update selection.

#### HorizontalAccordion

- Desktop uses narrow vertical triggers and one broad open panel.
- Below 760px it becomes a vertical disclosure list.
- Exactly one panel remains open.

### Feedback

#### StatusBadge

- Compact mono label plus a dot.
- Pulse only for genuinely live data.
- Never rely on color alone.

#### ProgressBar

- Uses a native progress element.
- Label and formatted value sit above the track; operational detail sits below.
- Coral communicates ordinary progress, Warning communicates backlog, Positive communicates completion.
- Do not use determinate progress for unknown-duration loading.

#### Notice

- Uses a structural mark, direct title, recovery copy, and an optional action.
- Critical notices retain stale or existing data whenever possible.
- Avoid modal dialogs for recoverable inline problems.

#### EmptyState

- Explains why the view is empty and gives one relevant next action.
- Loading keeps the same footprint through matching skeleton geometry.
- No celebratory illustration for routine absence.

### Data and surface

#### SurfaceCard

- Static cards do not react.
- Interactive cards are one focus target and cannot contain nested buttons or links.
- Related cards join into a gapless rule grid.
- Use elevation only when the surface is actionable or materially above its context.

#### MetricStrip

- Definition-list semantics with label, mono value, and optional detail.
- Six columns on large desktop, three on tablet, two on mobile.
- Values never disappear because detail is long.

#### DataList

- Definition-list semantics for label-value facts.
- Four columns on desktop, two on tablet, one on mobile.
- Use a semantic table instead when rows share sortable columns.

#### TokenMarquee

- Decorative duplicate is hidden from assistive technology.
- Stops and becomes a complete static row under reduced motion.
- Carries system vocabulary, not promotional copy.

## 7. Layout Principles

- Maximum content width: `90rem` with centered containment.
- Use CSS Grid for major layouts. Do not use percentage arithmetic in Flexbox.
- Hero: asymmetric split, approximately 1.25fr / 0.75fr, left-aligned copy and an offset instrument preview.
- Inline waveform imagery may interrupt a display headline at type height. It must never overlap text.
- Feature and component surfaces use a 12-column gapless bento grid.
- Avoid three equal floating cards. Use asymmetric spans such as 7/5, followed by joined 4/4/4 only when the cards are part of one instrument grid rather than generic marketing tiles.
- Editorial sections may use large negative space. Operational controls remain compact.
- Do not use `height: 100vh`; use `min-height: 100dvh` when a full viewport is required.
- Do not place absolutely positioned content over meaningful text or controls.

## 8. Responsive Contract

Supported audit widths: 320, 390, 768, 1024, and 1440 CSS pixels.

- **Below 1100px:** hero becomes one column; metric strip becomes three columns; state matrices and data lists become two columns.
- **Below 760px:** all major content grids collapse to one column; catalog becomes two columns; horizontal accordion becomes vertical; notice actions move below their copy.
- **Below 440px:** catalog and DataList become one column; button groups stack; all primary buttons may become full width.
- Minimum touch target: 44 by 44 CSS pixels.
- Body copy must remain at least 14px; primary reading copy should remain 16px.
- No horizontal page overflow at any supported width.
- At 200% zoom, primary content and actions remain available without two-dimensional scrolling.

## 9. Motion and Interaction

- Fast state change: `160ms`.
- Normal component transition: `220ms`.
- Editorial reveal: up to `620ms`.
- Primary easing: `cubic-bezier(.16, 1, .3, 1)`.
- Animate only `transform`, `opacity`, color, and box-shadow.
- Never animate `top`, `left`, width, or height for routine interaction.
- Hero content may enter with a short staggered rise.
- Live indicators may pulse; loading skeletons may shimmer; the token marquee may loop.
- Static controls do not receive perpetual idle animation.
- Under `prefers-reduced-motion: reduce`, stop loops, scroll-linked effects, and decorative transitions while preserving all content and actions.

If spring motion is required in a new isolated interaction, begin with stiffness 100 and damping 20, then tune by observation. Do not introduce a second global easing language.

## 10. Accessibility and State Invariants

- Every interactive element has a visible focus indicator with at least 3:1 adjacent contrast.
- Every control has an accessible name.
- Loading controls expose `aria-busy=true` and reject repeat activation.
- Error fields expose `aria-invalid=true` and link visible copy through `aria-describedby`.
- Toggle exposes `role=switch` and `aria-checked`.
- Tabs expose tablist, tab, selected state, linked panel, and roving keyboard focus.
- ProgressBar exposes current value, maximum, and an accessible label.
- Status is understandable in grayscale and without motion.
- Disabled controls retain legible labels and their physical footprint.
- Empty, loading, error, and disabled states keep stable geometry to prevent layout jumps.

## 11. Content and Voice

FlightDeck copy is concise, direct, and operational.

- Actions begin with a verb: “Create mission,” “Reconnect,” “Open session log.”
- Errors name the problem and the recovery path.
- Helper text explains format, consequence, or scope.
- Use organic operational data such as `43 / 64`, `68%`, `22:41`, and port `8010`.
- Do not use exclamation marks for routine success.
- Do not use “Oops,” “Click here,” “Learn more,” or vague “Submit” labels.
- Ban AI copy clichés: “Elevate,” “Seamless,” “Unleash,” “Next-Gen,” “Game-changer.”
- Do not use fake people, generic companies, or placeholder Latin text.

## 12. Agent Implementation Rules

1. Consume semantic `--fdx-*` tokens. Do not introduce a raw color, shadow, radius, spacing value, or easing when an existing semantic token fits.
2. Extend an existing component through props before adding a local style fork.
3. Keep markup semantic: use `nav`, `main`, `section`, `aside`, `dl`, `progress`, and native form controls where appropriate.
4. Do not add a dependency until the existing stack has been checked for the capability.
5. New components require:
   - public API and state model;
   - default, hover, focus, active, disabled, loading, and error treatment where applicable;
   - keyboard and touch behavior;
   - narrow-screen behavior;
   - one realistic product example;
   - automated semantic tests;
   - rendered verification without console errors or horizontal overflow.
6. Preserve the three-layer control contract. Face, frame, and depth are independent.
7. Prefer joined rule grids, negative space, and hierarchy over nested cards.
8. Keep changes reviewable and targeted. Do not rewrite the product shell to add a primitive.

## 13. Anti-Patterns — Never Do

- No pure black `#000000`.
- No Inter.
- No serif fonts in the product interface.
- No purple or blue neon aesthetic.
- No outer glow, bloom, glassy neon edge, or blurred control shadow.
- No outset shadow on anything except the one declared overlay.
- No control that travels on press.
- No pill on a panel, table, card or chip.
- No `5px` radius on a button, field, toggle or tab.
- No blurred shadow on a ground plane.
- No pink or orange depth colors reused as arbitrary accents.
- No gradient text on large headings.
- No custom mouse cursor.
- No emoji as interface iconography.
- No overlapping text, imagery, or controls.
- No centered hero composition.
- No generic three-card marketing row.
- No card inside card inside card.
- No floating label inputs.
- No placeholder standing in for a visible label.
- No spinner replacing an entire page when existing data can remain visible.
- No modal for a simple inline edit or recoverable notice.
- No motion without meaning.
- No horizontal page scroll on mobile.
- No generic names, round fake metrics, filler text, or AI marketing clichés.

## 14. Completion Checklist

A FlightDeck screen is complete only when:

- it uses the Night or Day palette without introducing another accent;
- typography follows the display/body/mono roles;
- actionable controls are flat pills with a face and a rule, and blocks are `5px`;
- layout is contained, asymmetric where editorial, and grid-disciplined where operational;
- all states are explicit and stable;
- keyboard, touch, reduced-motion, and focus behavior pass;
- there is no horizontal overflow at supported widths;
- browser console has no warnings or errors caused by the screen;
- product copy names real actions, states, and recovery paths;
- the implementation and this contract remain synchronized.

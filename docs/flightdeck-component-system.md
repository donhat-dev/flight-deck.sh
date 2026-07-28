# FlightDeck Interface Kit

## Context and goals

Design intent: FlightDeck must feel like a precise operational instrument rendered with the clarity of a printed technical manual.

The kit is for developers and technical teams working across FlightDeck’s data-heavy product surfaces. It adapts the useful language observed on UI SFX—warm paper, ink, coral signal, joined grids, compact metadata, and tactile offset shadows—without copying its brand, content, or illustration system.

The implementation must optimize for consistency, accessibility, and fast delivery. Product teams should prefer system consistency over local visual exceptions.

The current density baseline is:

| Element | Observed count | System response |
| --- | ---: | --- |
| Buttons | 148 | Actions must share one size, focus, and state model. |
| Cards | 141 | Related cards must join into grids instead of floating independently. |
| Links | 31 | Links must use descriptive labels and visible hover/focus treatment. |
| Inputs | 21 | Inputs must keep labels and validation messages attached. |
| Navigation | 6 | Destinations must remain stable across viewport changes. |
| Lists | 1 | Long lists should virtualize and preserve keyboard position. |

### Component inventory

The public kit contains 18 documented contracts:

| Category | Components |
| --- | --- |
| Actions | `Button`, `IconButton` |
| Input | `Field`, `SelectField`, `TextAreaField`, `CheckField`, `Toggle` |
| Navigation | `Tabs`, `SegmentedControl`, `HorizontalAccordion` |
| Feedback | `StatusBadge`, `ProgressBar`, `Notice`, `EmptyState` |
| Data and surface | `SurfaceCard`, `MetricStrip`, `DataList`, `TokenMarquee` |

New primitives must enter this inventory only after their semantics, states, responsive behavior, and product example are documented. Compound patterns should compose these contracts before introducing another base control.

## Design tokens and foundations

Product components must consume semantic `--fdx-*` tokens. Product components must not introduce raw color, radius, spacing, shadow, or motion values.

### Typography

| Token | Value | Use |
| --- | --- | --- |
| `--fdx-font-primary` | `Satoshi, Helvetica Neue, Outfit Variable, sans-serif` | UI, headings, actions |
| `--fdx-font-mono` | `IBM Plex Mono, ui-monospace, monospace` | Numeric values, status, compact metadata |
| Base text | `16px / 24px / 400` | Body content |
| Metadata | `0.62rem–0.72rem` | Uppercase operational labels |
| Display | `clamp()` with `0.93–0.98` line height | Editorial headings |

Display headings must remain at two or three lines on supported desktop widths. Body copy should stay between 45 and 70 characters per line.

### Color

| Semantic role | Night | Day |
| --- | --- | --- |
| Canvas | `--fdx-canvas` | `--fdx-canvas` |
| Surface | `--fdx-surface` | `--fdx-surface` |
| Raised surface | `--fdx-surface-raised` | `--fdx-surface-raised` |
| Primary text | `--fdx-text` | `--fdx-text` |
| Muted text | `--fdx-text-muted` | `--fdx-text-muted` |
| Primary action | `--fdx-signal` | `--fdx-signal` |
| Action face | `--fdx-action-face` | `--fdx-action-face` |
| Control frame | `--fdx-depth-ink` | `--fdx-depth-ink` |
| Primary depth | `--fdx-depth-pink` | `--fdx-depth-pink` |
| Alert/loading depth | `--fdx-depth-orange` | `--fdx-depth-orange` |
| Error | `--fdx-critical` | `--fdx-critical` |
| Warning | `--fdx-warning` | `--fdx-warning` |
| Success/live | `--fdx-positive` | `--fdx-positive` |
| Focus | `--fdx-focus` | `--fdx-focus` |

Coral must communicate the primary action or live system signal. Coral must not decorate passive content. Button depth uses a separate pink or orange token and must never reuse the face color. Ink frames the physical edge between those layers. Status color must always be paired with a text label or icon shape.

### Spacing, shape, and depth

Spacing must use `--fdx-space-1`, `2`, `3`, `4`, `6`, `8`, `12`, `16`, `24`, or `32`. Controls must use `--fdx-radius-xs` or `--fdx-radius-sm`. Floating overlays may use `--fdx-radius-md`.

Related cards must share borders in a gapless grid. Primary actions use a three-layer anatomy: `--fdx-action-face`, a `--fdx-control-border-width` ink frame, and an offset depth color through `--fdx-shadow-control` or `--fdx-shadow-control-alert`. `--fdx-control-depth-x` and `--fdx-control-depth-y` define the shared light direction. Ordinary panels must not use control shadows. Touch targets must be at least 44 by 44 CSS pixels.

### Motion

State transitions must use `--fdx-motion-fast` or `--fdx-motion-normal`. Editorial transitions may use `--fdx-motion-slow`. Motion must use `--fdx-ease`.

Motion must stop when `prefers-reduced-motion: reduce` is active. Content and actions must remain available when animation is removed.

## Component-level rules

### Button

Anatomy: optional leading icon, action label, optional trailing icon, a two-pixel structural frame, and a four-pixel offset depth layer. Face, frame, and depth must use independent semantic tokens.

Variants: `primary`, `secondary`, `quiet` through `IconButton`, `inverse`, and `error`.

| State | Required behavior |
| --- | --- |
| Default | The label must describe the result, such as “Create mission.” |
| Hover | The surface must change without relying on movement alone. |
| Focus-visible | A two-pixel semantic focus outline must appear outside the control. |
| Active | The control must move into its offset depth, leaving a one-pixel residual shadow and keeping its label visible. |
| Disabled | Native `disabled` must prevent activation; the muted face, frame, and depth remain visible instead of collapsing into a flat block. |
| Loading | `aria-busy=true` and a spinner must appear; the control must reject repeat activation while retaining orange physical depth. |
| Error | The error face must use the critical family, swap its depth to orange, and retain an action label such as “Retry.” |

Keyboard: `Enter` and `Space` must activate the button. Pointer and touch: the full 44-pixel surface must be interactive. Long labels must wrap only when the control is full width; compact toolbars should move the action to an overflow menu.

### IconButton

Anatomy: one icon and an accessible name.

Every IconButton must provide `label`, which becomes both `aria-label` and `title`. It must follow all Button states. IconButton must not contain an unlabeled decorative glyph. Toolbars should group related IconButtons with `role=group` and an accessible group label.

### Field

Anatomy: visible label, control, optional hint or error message, optional loading indicator.

| State | Required behavior |
| --- | --- |
| Default | The visible label must be associated with the input. |
| Hover | Border contrast must increase. |
| Focus-visible | The semantic focus outline must appear around the input. |
| Active | Selection and caret must remain visible. |
| Disabled | Native `disabled` must prevent input and preserve the visible label. |
| Loading | `aria-busy=true` and a non-blocking spinner must appear inside the control. |
| Error | `aria-invalid=true` and `aria-describedby` must connect the input to an inline error with `role=alert`. |

Placeholder text must not replace a label. Long values must scroll within single-line inputs. Multi-line content should use a separately specified TextArea with an explicit maximum height.

### SelectField

Anatomy: visible label, native select, semantic chevron, and optional hint or error.

`SelectField` must receive an `options` array with stable `value` and `label` fields. Native select behavior must remain intact so touch devices and assistive technology can use platform selection UI. Error copy must connect through `aria-describedby`; `aria-invalid=true` must appear whenever `error` is present.

The control must keep a 44-pixel minimum target, provide visible hover and focus states, and retain its selected value when validation fails. Disabled options may be marked individually; disabling the field must use the native `disabled` attribute.

### TextAreaField

Anatomy: visible label, resizable multiline control, and optional hint or error.

The default height must show several lines without dominating its card. Vertical resizing is allowed; horizontal resizing is not. Product code should set a meaningful maximum length where the backing API has a limit. Error and hint relationships follow `Field`.

TextArea content must not be replaced by placeholder copy. Long input must scroll inside the field and must not expand the page horizontally.

### Toggle

Anatomy: physical switch track, state indicator, label, and optional description.

Toggle uses a button with `role=switch` and exposes its value through `aria-checked`. It is for immediate preference changes, not legal consent or multi-item selection. Space and Enter must activate it through native button behavior. The label remains visible in both states; color and knob position jointly communicate state.

Disabled toggles must retain their current value, reject activation, and remain readable. Product code controls the value through `checked` and `onChange`.

### CheckField

Anatomy: native checkbox, custom check mark, visible label, and optional description.

CheckField is for independent selections and explicit acknowledgements. The native input stays in the accessibility tree and supplies checked, disabled, keyboard, and form semantics. The custom mark must follow native state through sibling selectors and show a visible focus outline.

Groups of checkboxes require a visible group label through `fieldset` and `legend` at the product layer. Do not substitute Toggle when the action is committed later by a separate button.

### SegmentedControl

Anatomy: labeled group and two to five mutually exclusive buttons.

The selected option must expose `aria-pressed=true`. Arrow Left and Arrow Right must move and select the adjacent item. Home and End must move and select the first or last item. Hover, focus-visible, active, disabled, loading, and error presentations must follow Button rules; loading and error should apply to the whole group, not one arbitrary option.

On narrow screens, labels must stay on one line or the group must switch to a native select. It must not create horizontal page overflow.

### Tabs

Anatomy: labeled tab list, two or more tab triggers, optional compact metadata, and one active tab panel.

Tabs must expose `role=tablist`, `role=tab`, `aria-selected`, `aria-controls`, `role=tabpanel`, and `aria-labelledby`. Exactly one enabled tab participates in sequential keyboard focus. Arrow Left and Arrow Right move and activate adjacent enabled tabs; Home and End jump to the boundaries.

Disabled tabs must be skipped during directional movement. On narrow screens, the trigger row may scroll horizontally while the page itself remains fixed. Tab panels must preserve enough minimum height to avoid layout jumps between related views.

### StatusBadge

Anatomy: state dot and short text label.

Default must use the neutral token. Hover and active must not imply clickability when the badge is passive. Focus-visible applies only when the badge is rendered inside a control. Disabled must use the parent control state. Loading must use a labeled “Loading” or “Pending” badge. Error must use the critical tone and a specific label.

Pulse motion should be reserved for genuinely live data. Status meaning must remain legible without animation or color.

### ProgressBar

Anatomy: label, formatted value, native progress element, and optional detail.

`ProgressBar` must use a native `<progress>` element with a meaningful accessible label. `value` and `max` carry machine-readable state; `valueLabel` may describe non-percent measures such as “43 / 64.” Signal, warning, and positive tones are allowed, but the value and detail must communicate status without color.

Progress indicates measurable completion, not indefinite loading. Use a skeleton or labeled loading state when the total is unknown. Multiple progress bars in one card must share alignment and spacing.

### SurfaceCard

Anatomy: optional eyebrow, state badge, title, body, and optional footer.

Variants: static surface and whole-card interactive surface.

| State | Required behavior |
| --- | --- |
| Default | Content hierarchy must remain eyebrow, title, body, footer. |
| Hover | Interactive cards must invert or raise surface contrast; static cards must not react. |
| Focus-visible | The whole interactive surface must show one focus target. |
| Active | An inset signal must confirm activation. |
| Disabled | The card must reject activation and expose native disabled semantics. |
| Loading | Skeletons must preserve the final content footprint and `aria-busy=true`. |
| Error | A critical inset rule and explicit message must identify the failure. |

Interactive cards must not contain nested buttons or links. Long content must clamp only when the full value is available through a detail view or disclosure. Card grids must use dense placement and must not leave empty visual cells.

### MetricStrip

Anatomy: label, value, optional detail.

The definition list must preserve label-value semantics. Hover and active must not appear on passive metrics. Focus-visible applies only when a metric is promoted to a link or button. Disabled, loading, and error states must replace the value with a stable-width label instead of collapsing the cell.

Desktop may use six columns, tablet should use three, and mobile must use two. Values must use tabular numerals. Overflowing detail may truncate; the metric label and value must remain visible.

### DataList

Anatomy: accessible definition-list label and repeated label, value, optional detail, and optional status.

DataList is for compact system facts, runtime configuration, and other scan-first key-value data. It must render `<dl>`, `<dt>`, and `<dd>` semantics rather than a table when records have no column relationships. Values use the mono font and may truncate on one line; the detail remains available directly below.

Desktop may use four columns, tablet two, and mobile one. Status uses `StatusBadge` and must not replace the value. Use a semantic table instead when rows must be sorted, compared across multiple shared columns, or navigated as a dataset.

### Notice

Anatomy: semantic mark, title, explanation, optional action.

Info, warning, critical, and success notices must use distinct semantic tokens plus text. Critical notices must use `role=alert`; non-critical notices should use `role=status`. Hover, focus-visible, and active states apply only to the notice action. Disabled and loading actions must follow Button rules. Error copy must describe both the problem and recovery.

On mobile, the action must move below the explanation. Long content must wrap without pushing the action outside the viewport.

### EmptyState

Anatomy: restrained signal graphic, title, explanation, optional primary action.

Default must explain why the area is empty and offer a relevant next action. Loading must retain the same footprint and use `aria-busy=true`. Error must use the critical signal and recovery copy. Hover, focus-visible, active, and disabled states apply to the contained action.

Empty states must not celebrate routine absence. Empty-state art must not overpower the recovery action.

### HorizontalAccordion

Anatomy: labeled triggers and one expanded content panel.

Exactly one item should remain expanded. Each trigger must expose `aria-expanded` and `aria-controls`. Arrow keys, Home, and End must move and open panels. Hover must increase trigger contrast. Focus-visible must surround the trigger. Active must use the coral signal. Disabled must prevent expansion. Loading must preserve the expanded panel width. Error must keep the failed panel open with recovery copy.

Below 760 pixels, the component must become a vertical disclosure list. Long content must wrap within the open panel and must not create horizontal overflow.

### TokenMarquee

Anatomy: one accessible token list and one visually duplicated loop.

The duplicate row must be `aria-hidden=true`. Hover and active must not imply interaction. Focus-visible is not applicable to passive content. Disabled is not applicable. Loading should render a static token row. Error should remove the decorative strip rather than announce irrelevant failure.

The marquee must stop under reduced motion and reveal a complete static row.

## Accessibility requirements and testable acceptance criteria

- Every interactive control must be reachable in a logical order using only `Tab`.
- Every focusable control must show a two-pixel focus outline with at least 3:1 contrast against adjacent colors.
- Every button and field must have an accessible name in the browser accessibility tree.
- Tabs must expose one selected tab, one linked panel, and directional keyboard movement that skips disabled triggers.
- Toggles must expose `role=switch`; check fields must retain a native checkbox in the accessibility tree.
- Progress bars must expose their accessible label, current value, and maximum.
- Every touch target must measure at least 44 by 44 CSS pixels at 320 CSS pixels viewport width.
- Text below 24 CSS pixels must meet WCAG 2.2 AA contrast: 4.5:1 for normal text and 3:1 for large or bold text.
- Status, success, warning, and error meaning must remain understandable in a grayscale screenshot.
- Disabled controls must not receive pointer activation.
- Loading controls must expose `aria-busy=true` and must not accept duplicate submissions.
- Error fields must expose `aria-invalid=true` and must reference visible error copy through `aria-describedby`.
- The HorizontalAccordion must operate with Arrow keys, Home, End, Enter, and Space.
- At 320, 768, 1024, and 1440 CSS pixels, the page must have no horizontal scrollbar.
- At 200% browser zoom, primary content and actions must remain visible without two-dimensional scrolling.
- With reduced motion enabled, all content and interactions must remain available and no continuous animation may run.

## Content and tone standards

Copy must be concise, confident, and implementation-focused.

Labels must describe destinations or outcomes:

| Avoid | Use |
| --- | --- |
| “Submit” | “Create mission” |
| “Click here” | “Open session log” |
| “Error” | “Connection interrupted” |
| “Try again” without context | “Reconnect” |
| “No data” | “No missions match this filter” |

Buttons must use verb-first labels. Error messages must name the problem and recovery. Helper text should explain format or consequence, not repeat the visible label.

## Anti-patterns and prohibited implementations

- Product code must not use raw color values when a semantic token exists.
- Product code must not add one-off spacing or typography values.
- Focus indicators must not be removed or hidden behind overflow.
- Interactive cards must not contain nested interactive elements.
- Placeholder text must not replace a visible label.
- Status must not rely on color alone.
- Decorative coral must not compete with the primary action.
- A control face and its offset depth must not use the same color token.
- Product grids must not leave an empty corner or dead cell.
- Long headings must not collapse into four or more desktop lines.
- Mobile layouts must not preserve desktop horizontal accordions or wide toolbars.
- Loading states must not replace the entire page when existing data can remain visible.

Migration should begin with frequently repeated actions, fields, and cards. Teams should replace local styles with primitives one workflow at a time. Existing semantic status colors should remain until their replacement passes contrast and grayscale checks.

Edge cases:

- Unknown or empty values must render an em dash with an accessible label when ambiguity is possible.
- Unbounded lists should virtualize after 100 visible items.
- Long identifiers should use middle truncation when the suffix carries meaning.
- Network refresh errors should keep stale data visible and identify its age.
- Read-only data must not be styled as an enabled editable control.

## QA checklist

- [ ] All component styling uses semantic `--fdx-*` tokens.
- [ ] Default, hover, focus-visible, active, disabled, loading, and error states are present.
- [ ] Button labels describe outcomes.
- [ ] Field labels and messages have valid programmatic relationships.
- [ ] SelectField and TextAreaField retain native form semantics and linked error copy.
- [ ] Toggle, CheckField, and Tabs pass keyboard-only operation.
- [ ] ProgressBar and DataList expose native progress and definition-list semantics.
- [ ] Keyboard-only operation passes for every interactive component.
- [ ] Touch targets meet the 44 by 44 CSS pixel minimum.
- [ ] Text and focus contrast pass WCAG 2.2 AA.
- [ ] Color is never the only state cue.
- [ ] Reduced motion removes continuous and scroll-linked motion.
- [ ] The page has no horizontal overflow at supported breakpoints.
- [ ] Long content, empty content, stale data, and network errors have explicit handling.
- [ ] Dense grids have no empty cells.
- [ ] Existing workflows continue to behave after primitive migration.

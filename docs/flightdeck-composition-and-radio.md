# FlightDeck control plane: composition layer + FlightDeckRadio

**Status:** plan, nothing implemented.
**Inputs:** `DESIGN.md` (token contract), `docs/flightdeck-component-system.md` (component
contract), `frontend/src/ui/ComponentLab.jsx` (the component-level proposal that
just landed), and a design critique of ComponentLab against the UI SFX reference.

---

## 1. Diagnosis: the gap is above the components, not inside them

The critique reads as a token and component problem — flat surfaces, no hard
shadow, no whitespace, everything equally weighted. Measured against the code,
the tokens and components are not where the gap sits.

| Claim in the critique | What the code actually shows |
|---|---|
| "FlightDeck is flatter than the reference; borders divide regions but create no front-to-back order" | The depth contract EXISTS and is implemented. `index.css` defines `--fdx-shadow-control{,-hover,-pressed,-alert,-muted}` and `--fdx-depth-{pink,orange,muted,ink}`; `.fdx-button` applies `box-shadow: var(--fdx-button-shadow)` with a face/frame/depth split, per `DESIGN.md` §5. |
| "Lacks whitespace; density is uniform" | The editorial spacing tokens exist and are almost unused. Across `index.css` + the lab: mid-scale (`space-3/4/6`) is used **87** times, editorial (`space-16/24/32`) **10** times. The layout lives in a 0.75–1.5rem band. |
| "Action colour sits too close to error" | `DESIGN.md` §2 already separates them: Action Face `#CF3F21`, Critical `#F16D74`, Warning `#E6B85C`, and it already states Pink/Orange are "depth materials, not additional accents". The spec is right; only enforcement is missing. |
| "READY repeats on every panel and stops carrying information" | **No longer true.** `grep -r READY` over the frontend returns zero hits, and the panel eyebrows are already distinct: Action / Input / Choice / Status / Surface / Navigation / Boolean control / Expanded input. This item was written against a different build and needs no work. |

So: **142 `--fdx-*` tokens already exist and the component semantics are strong.**
What is missing is a layer nobody has written yet — a *composition* contract that
says how regions relate to each other. ComponentLab renders every specimen inside
the same `fdx-showcase-panel`, which is correct for a catalog and wrong for a
product surface.

The conclusion the critique reaches is the right one, for a reason it does not
name: FlightDeck did not fail to adopt the reference's materials. It adopted them
and then applied them *uniformly*, which cancels them out. Depth that every
element has is not depth. Spacing that never varies is not rhythm.

**Therefore this plan changed no tokens and rewrote no components** — and held to
that through stages 2–8. It stopped holding when the token style itself was
decided; see §2b.

---

## 2b. The unified token style (decided after stage 8)

The composition work deliberately touched no tokens. That constraint ended when
the material language was settled, sourced from `uisfx/apps/showcase` — the same
reference the original critique used. Six rules:

| Rule | Implementation |
|---|---|
| **One edge treatment per control**, chosen by whether it has a face | A face → an offset, no border. No face → a border, no offset (`data-variant="ghost"`). An offset under nothing reads as a shadow cast by a hole, which is why the two never combine |
| **Blocks have no border either, by default** | Border is applied per designation, never as a blanket. On each screen exactly one region — the anchor — is designated |
| **Day: near-black face, orange offset** | Straight from the reference screenshot: its primary button is a dark key with an orange offset on warm paper |
| **Night: orange face** | Our own extrapolation, because uisfx ships **no dark mode** so there is no reference to follow. A near-black key on a near-black canvas read as "an orange shadow with no button attached"; the offset became a burnt orange, dark enough to read as the body's shadow and light enough to survive the canvas |
| **One orange in the system** | `--fdx-orange` resolves to the primary action colour. Introducing a second (a brighter amber) is what let Night's buttons drift off-palette |
| **Controls carry an icon, not only a label** | `ui/icons.jsx` — stroke icons in `currentColor`, so they inherit the label colour and need no per-theme variant. In a bank of same-sized keys the word is the only differentiator, and words read slower than shapes |
| **Pink is a card background only** | `--fdx-pink` → `--fdx-card-tint`; never a shadow, never text |
| **Corners are square** | `--fdx-radius-control: 0`, `--fdx-radius-block: 0` |
| **One lift, and it belongs to the device** | `--fdx-shadow-block` is applied to the plane, not to its regions |
| **The shell is inset from the screen** | body is the desk, the plane is the deck inset by `space-4`, regions are areas of that face |

Two things this forced, both worth keeping:

1. **The radius was a wrong turn, corrected by looking at the rendered site.**
   Reading the showcase CSS suggested a generous 0.62–1.35rem scale, so "soft
   neo-brutalism" looked like it meant soft corners. The reference screenshot says
   otherwise: its primary button, code block, cards and link buttons are all
   **sharp**. Those rounded values belong to the *floating* chrome — the topbar and
   the volume pill — which is the one thing that hovers. What makes the style read
   as soft is the **warm paper palette**, not the corner. Reading a stylesheet told
   me which values exist; only the screenshot told me which ones apply where.
2. **Regions became areas, not tiles.** Giving every block a border, a radius and a
   lift produced a bento grid. A radio is *one face* with areas grooved into it, so
   support regions are now flush and separated by a hairline; the anchor keeps the
   single designated border and the single offset; and the only thing that lifts is
   the device itself, because it is the only thing sitting on something.
3. **C6a inverted, and C3 learned to tell ground from figure.** Pink and orange
   used to be depth-only materials banned from fills. Now the control face and its
   depth trade places by theme, so orange is legitimately a fill, and pink is the
   one material with a single home — the rule became "the card tint is never a
   shadow and never text". And because the block recipe carries its own softened
   offset, C3 would have flagged every panel on every screen; it now skips layers
   declared through a `*-shadow-block` token. **The distinction is read off the
   token name, not guessed from magnitude** — the same principle as the composition
   markers: intent has to be stated.

   That change bit immediately, and the test suite is why it was caught: both
   anchors write `var(--fdx-shadow-block), var(--fdx-shadow-print)`, and testing
   the *whole declaration* for the block token made the lint blind to the print
   offset beside it — silently un-guarding the one region that matters most. The
   exclusion is per **layer**, with a regression test named after the mistake.

---

## 2. The composition contract

Six rules. Each binds to tokens that already exist, so nothing here needs a new
primitive.

### C1 — One anchor per screen

Exactly one region carries the screen. It occupies 50–60% of the first viewport,
holds the largest type on the page, and is the only region allowed to answer
"what is happening right now". Every other region is support and must be visibly
lighter — smaller heading, no depth, less padding.

A screen with two anchors has none.

### C2 — Density rhythm is mandatory, not aesthetic

Every screen contains at least one **dense** region and at least one **sparse**
region:

- dense — `space-2`/`space-3` internal rhythm, ≥6 data points, mono metadata
- sparse — `space-24` or `space-32` separation, ≤2 elements, deliberately empty

Uniform spacing is the defect being fixed; do not raise all spacing together.
Raising everything preserves the flatness at a larger scale.

### C3 — Depth is rationed

Offset depth marks *interaction and primacy*, never decoration:

- allowed: the anchor region, the primary action, the active/selected item
- forbidden: support panels, specimen frames, static cards, anything a user
  cannot act on
- disabled keeps mass but drops contrast (`--fdx-shadow-control-muted`)
- loading must differ from disabled by *material*, not only by dimming — the two
  currently read alike

A screen should contain roughly 2–4 depth-bearing elements. More than that and
C3 has failed.

### C4 — Asymmetry by default

The equal N-column grid is banned as a layout default. The standard split is
40/60: a narrow text column against a wide live region. Symmetry is permitted
only where the content is genuinely a matrix (a comparison table, a state grid).

### C5 — Metadata must earn its slot

The eyebrow/status slot carries a *distinct fact per region* — `4 STATES`,
`VALIDATED`, `KEYBOARD`, `3 SIGNALS` — or it is omitted. A word repeated on every
panel conveys nothing. (Already satisfied in ComponentLab; keep it a rule so it
cannot regress.)

### C6 — Colour roles are enforced, not just documented

Bind the existing spec so drift is detectable:

| Role | Token | Never used for |
|---|---|---|
| action, selected, active | Coral Signal / Action Face | error |
| error, failed, invalid | Critical | primary action |
| delayed, caution | Warning | loading |
| loading | Warning at reduced saturation, or Muted Depth | error |
| offset depth only | Pink Depth, Orange Depth | text, badges, charts, navigation |

The table restates `DESIGN.md` §2 in checkable form, because "Pink is not an
accent" written as prose did not stop the drift the critique observed.

### Where this lives — stage 2, implemented

`frontend/src/ui/compositionLint.js` + `compositionLint.test.jsx` (35 tests, part
of `npm test`). It reads the stylesheets by glob, so a sheet added later —
`radio.css` — is covered without touching the lint.

**What turned out to be decidable, and what did not.** Enforcing the contract
meant admitting that only part of it is visible in source:

| Rule | Status | Why |
|---|---|---|
| C1 at most one anchor | enforced | via a `composition: anchor` marker |
| C1 a screen has an anchor | enforced when a sheet declares `composition: screen` | opt-in: choosing which region is the anchor is a design decision, not a lint's |
| C2 density rhythm | **not checkable** | "at least one sparse region" is a layout fact; a spacing histogram cannot tell a deliberate void from a gap |
| C3a depth only on interactive / frame / anchor | enforced | the rule with the most teeth |
| C3b depth budget | enforced on screens | counts base classes **at rest**, excluding frames |
| C3c depth in a list is conditional | enforced on JSX | a depth class inside `.map()` must arrive via an expression |
| C4 asymmetry | **not checkable** | the matrix exemption is a content judgement; an equal grid is right whenever the content is a matrix |
| C5 distinct metadata | **not checkable here** | needs the rendered string per region |
| C6a depth material as fill or text | enforced | |
| C6b role token vs the selector's role | enforced, 3 directions | error↔action, loading↔error, caution↔error — the unambiguous ones |

The undecidable three stay in review. Writing a weak proxy for them and calling
it enforcement would be worse than leaving them explicit.

**Two markers, both greppable.** `composition: anchor` declares the anchor;
`composition-lint-allow: <rule> — <reason>` exempts one site and the **reason is
mandatory** — an exemption nobody had to justify is a silent rule deletion. A
ratchet test caps the total exemption count at its current value (3), so the
cheapest way past the lint cannot be to keep adding markers.

**Three findings from building it**, none of which the prose diagnosis had:

1. **Depth hides behind token aliases.** `.fdx-button` reaches its offset through
   two hops (`--fdx-button-shadow` → `--fdx-shadow-control-alert` →
   `4px 4px 0 …`). A grep for offset shadows finds 4 sites in `index.css`; the
   lint, resolving `var()`, finds **9**. Any check that does not resolve custom
   properties reports the kit as flatter than it is.
2. **`.fdx-showcase-panel` is the diagnosed defect, now marked as debt.** Every
   catalog specimen carries the same `--fdx-shadow-print` offset — §1's
   "adopted them and then applied them uniformly", found independently by the
   rule rather than by reading. It holds an exemption whose reason names it as
   debt to remove at stage 3, not as an approval.
3. **`home-concept-v2.css` measures as flat.** One depth-bearing element
   (`.fd2-btn`), no anchor, and spacing that never leaves the 0.35–1.5rem band.
   It passes the lint — nothing there is *wrong* — but it has not made the
   composition decisions the contract asks for. That is the gap Radio exists to
   close, restated as a measurement.

Deliberately *not* enforced by material: `--fdx-shadow-print`
(`4px 4px 0 var(--fdx-signal)`) and `--fdx-shadow-control-*`
(`4px 4px 0 var(--fdx-depth-pink)`) are the same geometry in different
materials, and exempting the print family would have made C3a toothless —
uniform print depth is exactly how the flatness happened.

---

## 3. FlightDeckRadio — the parallel control plane

### Why a second plane rather than a redesign

The existing dashboard is an **inspection** surface: you go to a view and read
panels. It is correct for "what happened" and it is the thing that must not
break — Treasures, Spend, Logbook and Diff all live there.

The composition rules above cannot be demonstrated inside it without touching
every view at once. A parallel plane lets the rules be proven on real data first,
and adopted per-view afterwards. This follows the pattern the repo already uses:
one Vite entry per proposal (`component-lab.html`, `home-concept.html`,
`home-concept-v2.html`), reviewable without mounting over the product.

### The art-direction thesis

Radio is not only a monitoring surface. It is a deliberate break from the
industry assumption that **a control plane must arrive in a predictable frame** —
the equal card grid, one metric per card, uniform panel chrome, a toolbar on top.
That frame is a convention, not a requirement, and adopting it is what turned
ComponentLab into design documentation rather than a product surface.

The break has to be bounded, or "unconventional" becomes an excuse for a console
nobody can operate under pressure. So the line is drawn between *what a reader
can predict* and *what the layout looks like*:

| Must stay predictable — operability | Free to break — art direction |
|---|---|
| A given fact always lives in the same slot | The frame: no equal grid, no card-per-metric, no uniform panel chrome |
| Colour meaning (C6) never varies by screen | Region proportion, and deliberate emptiness |
| Focus order, keyboard reach, visible focus | Type at display scale, print-like layering |
| Destructive actions stay guarded and legible | Asymmetry, density contrast, off-centre anchors |
| Numbers keep the mono face and tabular figures | How a number is framed and how large it reads |

The falsifiable limit: someone who knows FlightDeck must still find any specific
number within about two seconds. Art direction that costs more than that has
failed, however good it looks. Judge Radio against this, not against how far it
sits from the convention.

### What "Radio" means here

A **monitoring** surface rather than an inspection one: something left on, which
speaks up when a run matters. The metaphor is load-bearing, not decorative —
radio hardware supplies exactly the composition FlightDeck lacks:

| Radio idiom | Composition rule it satisfies |
|---|---|
| One "on air" region | C1 anchor |
| A dense tuning band of channels against an open now-playing area | C2 density rhythm |
| Physical stacked panels, offset knobs | C3 rationed depth |
| Narrow channel list against a wide display | C4 asymmetry |
| Signal meters reading in units | C5 distinct metadata |
| One lit indicator at a time | C6 one coral per region |

### Proposed regions

1. **On air** — anchor, ~55%. The session currently running: elapsed, current
   tool, burn rate, quota headroom, the last few actions. The only region with
   display type and depth.
2. **Channels** — dense band, ~25%. One row per active session; tuning to a
   channel opens it. Mono metadata, `space-2` rhythm, no depth except the
   selected row.
3. **Signal** — sparse. Three meters at most: quota, cache hit, cost rate.
   `space-24` around them. Deliberately empty.
4. **Log** — full-width footer strip. The day's notable events: completed,
   failed, waiting. Wide and short, echoing the reference's footer statistics.

Every number comes from endpoints that already exist (`/api/summary`,
`/api/sessions`, `/api/quota`, `/api/usage-windows`, `/api/stream` for live
updates). No backend work, and the SSE feed already carries the "something
changed" ping the plane needs.

### The plane shell — what makes two views one plane

Radio and the re-composed Spend first shipped as two separate pages. Two pages in
the same visual language are two proposals, not a control plane. `plane/Plane.jsx`
is the shell that makes them one, and the split of responsibility is the design:

| Owned by the plane | Owned by the view |
|---|---|
| Identity and instruments: wordmark, burn-rate dial, ON AIR lamp, palette switch | Its four (or five) regions |
| The tabs, and the hash route behind them (`#/now`, `#/spend`) — a tab is linkable and survives a reload | Its own anchor |
| Data both tabs need: quota + the rolling window, fetched once | Data only it needs: sessions for ON AIR, daily/by-model for SPEND |
| View-scoped controls beside the tabs | — |
| **No anchor** | **Exactly one anchor** |

Three things that fall out of the contract rather than from taste:

1. **Each tab keeps its own stylesheet.** Not tidiness — the lint checks anchors
   per sheet, so merging `radio.css` and `spend-composed.css` would report C1
   immediately. The file layout is what makes "two anchors" impossible to add
   quietly.
2. **The tab bar is flat: only the selected tab stands proud.** One depth element,
   gated on its ARIA state.

   Three richer treatments were tried and reverted, in this order: a drawn cuboid
   in oblique 3/4 projection with a chamfered corner; rectangular moulded caps with
   a slot rail, fluted plastic and a chamfer hairline; and soft neo-brutalist keys
   built from the kit's control tokens. Each was more illustrated than the last,
   and each read as *navigation asking for more attention than navigation earns* on
   a page whose anchor is supposed to carry it.

   Worth keeping from that detour, since none of it depended on the look:

   - The neo-brutalist version cost two of the screen's four depth slots. Measured
     on the page — 4 rendered hard-offset elements, 5 with a signal selected — not
     argued about. That measurement is the reason the treatment was expensive, and
     it is the same method §6 now uses to check any screen.
   - C3c fired on it correctly (every key in a bank stands proud, which the rule
     reads as depth per list row), which is how the exemption mechanism grew a JSX
     half: markers scoped to the `.map(` line, reason still mandatory, negative
     tests both ways. That mechanism is kept and tested; it currently has no user,
     and the ratchet ceiling went back to 3 when the override was removed.
   - Comments are now stripped before the JSX scan, so a `.map(` inside a comment
     no longer counts as a list.

3. **The range control appears on SPEND and is absent on ON AIR.** A time range
   means nothing beside "what is running now", and a permanent control that is
   dead on one tab is worse than one that comes and goes.

**A render-time guard for the invariant the lint cannot reach.** Proving each
sheet declares one anchor is not the same as proving the running page shows one: a
shell that mounted both views would satisfy every sheet and still put two anchors
on screen. `plane/Plane.test.jsx` renders the real shell and counts anchor-marked
elements, reading the anchor class list *from the stylesheets' own markers* so a
third tab is covered without editing the test. Verified by breaking it: rendering
both views made it report `expected 2 to be 1`.

The standalone `spend-concept.html` was retired here rather than at stage 8 — the
moment the SPEND tab existed, a second copy of the same view was a duplicate to
maintain, and the live-vs-v2 comparison it existed for works just as well at
`/radio.html#/spend`.

### What makes it a control plane, not a dashboard

It must act, not only display: tune to a session (open it), mute (stop watching),
and jump to the artifact or diff a run produced. Read-only would make it a fourth
way to look at the same numbers.

---

## 4. Staged plan

Each stage is independently reviewable and leaves the product working.

| # | Stage | Deliverable | Verifies |
|---|---|---|---|
| 1 | Write the composition contract | §2 as a `docs/` section | The rules exist before anything is built against them |
| 2 | ✅ Composition lint | `compositionLint.js` — C1, C3a/b/c, C6a/b enforced; C2/C4/C5 declared undecidable | The rules are enforceable, not aspirational. Verified by a live negative test: violations injected into a real stylesheet were caught through the same path CI uses |
| 3 | ✅ FlightDeckRadio shell, **Day palette only** | `radio.html` + `radio-entry.jsx` + `Radio.jsx` + `radio.css`. Declares `composition: screen`, so the lint requires it to name its anchor | The composition reads as intended before real data complicates it |
| 4 | ✅ Wire live data | `/api/sessions`, `/api/summary`, `/api/quota`, `/api/usage-windows` + the SSE ping. No backend work | It is a control plane on real state — verified rendering live sessions |
| 5 | ◐ Actions | Tune (repoints the anchor), mute (local watch list, persisted), open transcript. **Jump-to-artifact not built** | It controls rather than displays |
| 6 | ✅ Re-compose **Spend** | `spend-concept.html` + `SpendComposed.jsx` + `spend-composed.css`, live on the same endpoints. Swapping it into `view === "usage"` is left as a separate decision | The rules survive contact with a view that has real requirements |
| 7 | ✅ Night palette port | `ui/PaletteToggle.jsx` on both planes. Every colour already came from a token both themes define, so flipping `data-theme` **was** the port | Verified in both palettes with the composition unchanged: the art direction was never in the paper colour |
| 8 | ⏸ Radio takes `/` | Retire the `home-concept*` entries | One home, not three — **needs a decision, not a step**: it replaces the daily entry point |

Stage 6 is the point of the exercise. Stages 3–5 exist to earn the right to do it,
and stages 7–8 only make sense once it has been earned.

### What stages 3–5 actually produced

Reachable from the dashboard sidebar under **Planes → Radio** (an anchor, not a
`setView` button, because Radio is its own Vite entry). Registered in
`vite.config.js`; the backend already serves `dist` with `html=True`, so
`/radio.html` needs no route.

Four regions, mapped in `radio.css`: **On air** (anchor, ~55vh, display numeral,
the page's only depth, carrying the print offset), **Channels** (dense band,
hairlines and no card chrome, depth on the selected row only), **Signal** (three
meters inside `space-24`), **Log** (wide, short). Tokens are the existing
`--fdx-*` set in its Day palette — no third namespace after `--fd2-*`.

**Three things this stage established beyond the shell:**

1. **The lint caught its author.** C3c flagged `radio-channel` as depth rendered
   per row. It was a false positive — the depth belongs to
   `[data-selected="true"]`, so only one row has it — and the fix was to
   distinguish *unconditional* depth from *attribute-gated* depth rather than to
   suppress the rule. C3c now flags only classes whose depth has no gate, which
   is the pattern that is actually wrong. Two of the lint's own bugs surfaced the
   same way: exemptions attached to the declaration instead of the rule, and a
   single-line `.map()` slipped past a line-based scanner.
2. **A colour-role error of ours, caught by reading the render.** The log first
   showed a *running* session in Warning. Running is the most normal state on the
   page; C6 assigns active to Coral Signal, and Warning means caution. Now:
   running → Signal, idle → muted, and Warning/Critical stay **unused** because
   `/api/sessions` cannot tell us a run was delayed or failed. Colouring an idle
   run as a problem would be an invented fact.
3. **Radio put a known pricing gap where it cannot be missed.** Session cost
   reads `$0.00` for nearly every channel, because `flightdeck/pricing.py` knows
   `claude-opus-4` but **not `claude-opus-5`**, which is what these sessions run
   on. Spend already discloses the same gap honestly — "5.6B unpriced tokens" in
   its header, and no Opus 5 row in the model breakdown — so this is a known
   omission rather than a hidden one. What Radio changes is the reading: in Spend
   the gap is one caveat in a header; on Radio the gap fills the cost column of
   nearly every channel. Burn rate and window spend stay correct because `/api/usage-windows`
   prices separately. Not fixed here — the real per-token prices are not ours to
   invent on a cost dashboard. Adding the price row belongs to stage 6.

**Not done, and why:** "jump to the artifact or diff a run produced" needs a
session→artifact relation that no endpoint exposes. Tune, mute and open-transcript
are real; that fourth action would need the relation built first.

---

## 5. Decisions taken

1. **Radio is the monitoring surface described above**, and its art direction
   deliberately leaves the predictable control-plane frame — see the thesis in
   §3, including the operability limits that keep the break usable.
2. **Stage-6 target is Spend** — see §6.
3. **Radio eventually replaces the home view.** It is parallel only while it is
   being proven, not as a permanent second plane. Consequence:
   `docs/home-layout-local-workbench.md` and the `home-concept*.html` entries are
   superseded **as layouts**, but their content inventory stays the requirement
   input — what a home must show did not change, only how it is composed. Those
   entries should be retired when Radio takes `/`, rather than leaving three
   competing homes in the repo.
4. **Day palette first, Night after.** Not merely a sequencing choice: the
   reference material *is* warm paper and ink, so Day is where this art direction
   is actually provable. Night is the port, not the original.

---

## 6. Stage 6 target: Spend

Spend currently renders **12 panel-level regions** — API-equivalent value,
avoided value, four efficiency meters, cost trend, three operational signals,
model breakdown, cost concentration — at close to equal weight. It is the
strongest test of the contract precisely because it has the most to lose from
uniformity.

Proposed re-composition, with the rule each move answers:

| Region | Weight | Rule |
|---|---|---|
| **Burn** — the trend chart, the current rate, and quota headroom read as ONE region | anchor, 50–60% | C1. It is the only region that answers "is this normal, and how much room is left" — the question the view exists for |
| **Model breakdown** | dense support | C2 dense pole. Legitimately a matrix, so C4's symmetry exemption applies |
| **Efficiency** — at most three meters | sparse support | C2 sparse pole, `space-24` around it. Today it is four numbers at panel weight |
| **Signals** | dense list, short | C2. Ranked, no card chrome per item |
| **Concentration + totals** | footer strip, wide and short | C4, echoing the reference's footer statistics |

Two figures must not become the anchor despite being the largest numbers on the
page: API-equivalent value is explicitly "not the amount charged", and avoided
value is a comparison. Anchoring on either would make the view's most prominent
claim a number nobody acts on — the exact failure C1 exists to prevent.

Depth budget for the whole view: the anchor, the range control, and the selected
signal. Three, inside C3's 2–4 — and now **measured** rather than asserted: the
lint reports `spend-composed.css` at exactly three depth-bearing elements at rest
(`.sc-burn`, `.sc-range-seg`, `.sc-signal`), with `radio.css` at two.

### What building it changed

Delivered as its own entry (`/spend-concept.html`) on the repo's proposal
pattern, live on the same five endpoints the dashboard uses. **Adopting it into
`view === "usage"` is one swap in `App.jsx`, deliberately not done here** — it
replaces the daily tool, so the flip is a decision rather than a step.

Four things the work forced, three of them corrections:

1. **A blind spot in the lint, of exactly the kind the lint exists to close.**
   Both anchors take their offset from `var(--fdx-shadow-print)`, declared in
   `index.css`. The var map was per-file, so the lint could not resolve it and
   reported **both anchors as flat** — it had never checked the one region it most
   needed to. Fixed with `collectVars()` + an `inheritedVars` option; a screen can
   still override a token locally. Same failure as the two-hop alias, one level
   up, and found only by asking the lint to print what it saw.
2. **The range control was four raised buttons.** Built from the kit's
   `.fdx-button`, all four segments carried offset depth — uniform depth, which is
   no depth. Now a segmented control where only the selected segment stands proud:
   what C3 allows, and the right physical idiom. `aria-pressed` was added to the
   lint's interaction vocabulary, since an ARIA state *is* an interaction state.
3. **A fake scale, removed.** `Avg context / turn` had a meter bar computed
   against an invented 200K denominator, so at 372K it sat pinned at 100% and read
   as "at the limit". The figure has no natural ceiling — it counts cache reads —
   so that meter now ships with **no bar**. A bar implies a bounded scale; where
   none exists, the number stands alone.
4. **The demotion holds up in practice.** API-equivalent value ($27.3K) and
   avoided value ($127.3K) are by far the largest numbers on the page and they sit
   in the footer, while the anchor carries $29/hour. Reading the page top to
   bottom now answers "is this normal, and how much room is left" before it offers
   any reference figure — which was the whole claim of §6.

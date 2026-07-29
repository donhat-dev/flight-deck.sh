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

**Therefore this plan changes no tokens and rewrites no components.**

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

### Where this lives

A new `docs/` section, plus a lint that is cheap because the tokens are CSS
variables: a test can assert that `--fdx-depth-*` never appears in a `color:` or
`background:` on text, and that no file uses more than N depth-bearing selectors.
Same shape as the Playfair-confinement test already guarding the Treasures
tokens.

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
| 2 | Composition lint | Tests for C3 (depth budget) and C6 (colour roles) | The rules are enforceable, not aspirational |
| 3 | FlightDeckRadio static shell, **Day palette only** | `radio.html` + entry + component, representative local data | The composition reads as intended before real data complicates it |
| 4 | Wire live data | Existing endpoints + SSE | It is a control plane on real state |
| 5 | Actions | Tune / mute / jump-to-artifact | It controls rather than displays |
| 6 | Adopt back into **Spend** | Re-compose Spend per §6, judged against Radio | The rules survive contact with a view that has real requirements |
| 7 | Night palette port | Radio + Spend in Night | The art direction was in the composition, not in the paper colour |
| 8 | Radio takes `/` | Retire the `home-concept*` entries | One home, not three |

Stage 6 is the point of the exercise. Stages 3–5 exist to earn the right to do it,
and stages 7–8 only make sense once it has been earned.

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
signal. Three, inside C3's 2–4.

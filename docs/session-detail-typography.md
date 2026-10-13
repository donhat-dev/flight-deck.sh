# Typography for the session transcript: four candidates

Scope: the type system for one surface only, the FlightDeck session detail view.
Decided: **D-prime**, built in `frontend/src/session/session.css`.
Mocks: Pencil screens `S4 Typography proposals` and `S5 D-prime after review`.

## The surface

A single scrolling column, 880px wide, on a near-black ground (`#050505`), read
on a desktop browser for long stretches. One column carries four registers that
used to live in four different places:

1. **Prompt** - what the human asked. Short, arrives in bursts.
2. **Prose** - the agent's answer. Paragraphs, lists, tables, inline code.
3. **Thinking** - reasoning. Long, skimmed more often than read.
4. **Machine** - commands, terminal output, diffs, JSON. Mono, often 200+ lines.

Faces are fixed by the design system: **Outfit** variable (UI/prose) and
**IBM Plex Mono** 400/600 (machine). Dark ground, `#F4F3EF` primary text,
`rgba(244,243,239,.62)` secondary.

The reading pattern is not "read every word". It is: land at the end of the
session, scroll up through thousands of lines, stop where something looks
relevant. So the type system is a **navigation aid** first and a reading surface
second.

## Constraint from the platform

Row virtualization measures each row. Median row height is 47px, p90 115px. A
setting with a taller line box makes fewer rows fit a screen, which is fine, but
it also raises the cost of every mis-estimated row. Line heights that resolve to
whole pixels at the default browser zoom keep the measurement stable.

## The four candidates

Values are `size px / line-height multiplier / weight`.

### A - Editorial

Thesis: the transcript is an article; size carries the hierarchy and the machine
voice stays quiet.

| Register | Value |
|---|---|
| Prompt | 17 / 1.45 / 500 |
| Prose | 16 / 1.7 / 400 |
| Thinking | 14 / 1.62 / 400 italic |
| Mono | 12 / 1.6 / 400 |
| Label | 9 / 1.4 / 600, caps, .18em |
| Measure at 880px | **126 characters** (measured) |

Cost: a mono block at 12px next to 16px prose reads as a footnote; long tool
output becomes hard to scan. Vertical space per turn is the highest of the four.

### B - Console

Thesis: the transcript is a log; the machine voice is the default and prose
adapts to it.

| Register | Value |
|---|---|
| Prompt | 14 / 1.4 / 600 |
| Prose | 13.5 / 1.55 / 400 |
| Thinking | 12 / 1.5 / 400 italic |
| Mono | 11.5 / 1.5 / 400 |
| Label | 9 / 1.3 / 600, caps, .16em |
| Measure at 880px | **148 characters** (measured) |

Cost: 148 characters is far past any comfortable line length, and 12px
italic on a dark ground is at the edge of legibility.

### C - One size

Thesis: one body size for every register; weight, colour and left rules do the
separating, so the vertical rhythm never breaks.

| Register | Value |
|---|---|
| Prompt | 15 / 1.6 / 600 |
| Prose | 15 / 1.6 / 400 |
| Thinking | 15 / 1.6 / 400 italic, dim |
| Mono | 13 / 1.6 / 400 |
| Label | 9 / 1.3 / 600, caps, .18em |
| Measure at 880px | **136 characters** (measured) |

Cost: thinking at full body size may pull more attention than it deserves, since
it is the register most often skipped.

### D - Baseline 4px

Thesis: mono is optically matched to the UI face (Outfit 15 and Plex Mono 13
have nearly the same x-height), and every line box is a multiple of 4px, so
prose and code share one rhythm.

| Register | Value | Line box |
|---|---|---|
| Prompt | 16 / 1.5 / 500 | 24px |
| Prose | 15 / 1.6 / 400 | 24px |
| Thinking | 13 / 1.538 / 400 italic | 20px |
| Mono | 13 / 1.538 / 400 | 20px |
| Label | 9 / 1.33 / 600, caps, .18em | 12px |
| Measure at 880px | **136 characters** (measured) |

Cost: thinking and mono share a setting, so they are told apart only by face,
colour and slant.

## What the choice actually turns on

1. **Which register is the default reader?** Someone re-reading a decision wants
   A or D. Someone auditing what ran wants B.
2. **How loud should thinking be?** It is the largest register by volume and the
   least often read. C makes it as loud as prose; A and D demote it.
3. **Does code need to feel the same size as prose?** Only D pairs them
   optically; the others let mono look smaller, which is the conventional
   choice but makes long output tiring.
4. **Line length.** Every candidate is far outside the comfortable band at
   880px - see the measurements below. This turned out to be the finding that
   matters most, and none of the four addressed it.

## Questions for review

- Which candidate best serves scroll-and-scan navigation, as distinct from
  linear reading?
- Is italic the right demotion for thinking on a dark ground, or should it be
  upright at lower contrast?
- Is a single 4px baseline across proportional and mono type worth the
  constraint it puts on future components?
- Are any of these settings below accessible minimums for body text at the
  stated contrast, and would any need a user-facing size control?

## Measured, after review

The character counts above were estimates and all four were wrong by roughly
2x. These are measured in Chromium against the actual shipped font files
(`@fontsource-variable/outfit`, `@fontsource/ibm-plex-mono`), by walking a
rendered paragraph character by character and counting how many land on each
line. Script: `scratchpad/typo3.mjs`.

| Setting | Chars per line at 880px |
|---|---|
| Outfit 13.5 | 148 |
| Outfit 15 | 135 (vi) · 136 (en) |
| Outfit 16 | 125 (vi) · 127 (en) |
| Outfit 17 | 116 |
| Plex Mono 12 | 122 columns |
| Plex Mono 13 | 109 columns |

Narrowing the column, same measurement:

| Column | Outfit 15 | Outfit 16 |
|---|---|---|
| 880px | 135 | 125 |
| 760px | 113 | 106 |
| 680px | 106 | 96 |
| 620px | 95 | 87 |

**Font metrics** (ratio of em, measured at 1000px):

| | x-height | cap-height |
|---|---|---|
| Outfit | 0.485 | 0.704 |
| IBM Plex Mono | 0.532 | 0.704 |

So the x-height claim in candidate D was close but not right: matching Outfit
15px needs Plex Mono at `15 x 0.485 / 0.532 = 13.7px`, not 13px. At 13px the
mono x-height is 5% short. The two faces already share a cap-height ratio,
which is why they set together well at all.

## D-prime, the setting to build

Line heights are stated in px, not multipliers, because `13 x 1.538 = 19.99`
is not 20 and the 4px grid claim was false as written.

| Register | Size | Line box | Weight | Colour |
|---|---|---|---|---|
| Prompt | 16px | 24px | 500 | text |
| Prose | 15px | 24px | 400 | text |
| Thinking | 14px | 20px | 400 **upright** | dim |
| Mono | 13.5px | 20px | 400 | text / dim |
| Label | 9px | 12px | 600 caps .18em | faint |

Two changes that came out of the review:

- **Thinking is upright, not italic.** A long run of italic on a near-black
  ground costs more legibility than the demotion is worth. Size, colour and the
  hairline rule already say "this is secondary".
- **Mono is 13.5px**, the optical match for 15px Outfit, instead of the
  conventional "code is smaller" 12px.

## The column, which was the real problem

At 880px every candidate sits between 125 and 148 characters. Long-form
guidance (45-75) assumes 18-21px reading sizes and does not transfer directly,
but technical UIs still settle near 90-100. The fix is not a smaller font, it
is an asymmetric column:

- **Prose, prompt, thinking: max-width 660px** -> 103 characters at 15px.
- **Tool cards, diffs, terminal, tables: full 880px** -> 109 mono columns at
  13.5px, which is close to the 100-120 that terminal output is written for.

That split also matches how the content is read: prose is read in lines, tool
output is scanned in columns.

## Open, not decided

A density control (comfortable / compact) is warranted for sustained reading,
and it is the honest answer to "is 13.5px too small for you" rather than
picking one number for everyone. WCAG sets no minimum font size; the contrast
pairs here measure 18.4:1 (primary) and 7.1:1 (secondary), both clear of AA.

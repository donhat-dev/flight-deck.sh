# Weekly Quota Calibration: Ledger Tokens per 1%

**Date:** 2026-09-23 · **Data:** 413 `/usage` readings found in transcripts (2026-08-24 → 2026-09-23, six weekly windows) plus the live report; ledger billed rows (`dup_of IS NULL`, Claude models).

> **Superseded on 2026-09-23 by a controlled test (section "Controlled test").** The output-only model below fit history well, but the test rejects it: the quota tracks API-equivalent cost, cache tokens included. The history fit could not tell the two apart because cache reads and output rise together.

## Controlled test (2026-09-23, Opus 5.5, `scripts/cost-audit/quota/experiment/`)

Nothing else used Claude during the runs. Meter read from the claude.ai usage page (whole percents).

| Phase | Tokens used (from the runs' transcripts) | API-equivalent | Session | Weekly |
|---|---|---:|---:|---:|
| Baseline | | | 20% | 35% |
| A: 8 runs, no tools, long output (15:23–15:28) | output 328k, cache write 254k, cache read 138k | about $8.6 | 28% (+8) | 36% |
| B: 6 runs, read about 120k tokens then 30 tiny reads (15:30–15:32) | output 18.6k, cache write 961k, cache read 32.7M | about $14.6 | 39% (+11) | 37% |

- **Output-only is rejected.** It predicts B at +0.6 to +1.1 session points; B gave +11.
- **Cost fits.** $23.2 gave +19 session points, about $1.2 per session point, or about **$9.8 per point of the week**. The single-coefficient cost fit on this week's `/usage` readings gives the same $9.80. Solving A and B separately, cache dollars weigh about 0.6–1.0 of output dollars (whole-percent readings leave a wide range), so cache is priced near its list rate.
- Session to week stays about 8:1 (+19 session, +2 week).
- Two turns of this session ($1.12 and $0.73) did not move the meter. That fits the model given whole-percent readings, but sits at its edge.

**Why the history fit misled.** Cache reads and output grow together in agent work, so a per-model output coefficient absorbs each model's typical cache cost. That predicts well while the working pattern stays the same, and fails when it changes, as phase B did. The drift of cost per 1% across weeks ($21 in late August, $10 this week) is still unexplained. It could be a limit change, per-model weights that differ from list prices, or chat usage the ledger cannot see.

**Still open:** cache read and cache write (1h) were not separated; B was about half of each. Next test: one context read once, then 100 tiny reads per run, so cache reads dominate.

## Test 2 (2026-09-23, Opus 5 and Fable 5.1, predictions fixed before running)

Meters as Session / Weekly / Fable: before 40/37/35, after D 45/37/35, after C 67/40/40.

| Phase | Tokens used | List price | Predicted H1 (list) / H0 (as Opus 5.5) | Observed |
|---|---|---:|---|---|
| D: Opus 5, cache-read-heavy | cache read 19.0M, cache write 1h 276k, output 9k | $12.48 | session +10.2 / +5.1 | **+5**, week 0 |
| C: Fable 5.1, output-heavy | output 164k, cache write 1h 170k, cache read 42k | $11.61 | session +9.5 / +3.8, Fable +4.5 | **+22**, week +3, Fable **+5** |

- **Neither hypothesis holds for every model.** Each model has its own quota weight, and it is not its list-price gap.
- **Opus 5 counts at about half its list price** (0.4–0.6 given whole-percent readings), about the same as Opus 5.5 for the same tokens. History agrees: in the weeks that were Opus 5 only (ending 08-25, 09-08, 09-22), a 0.5 weight predicts the week within 2.6–3.5 points, where list prices miss by 17–44. This explains most of the drift from $21 to $10 per 1%: late August was all Opus 5.
- **The Fable bucket tracks Fable's list price** ($11.61 for +5, about $2.3 per point, in line with $2.4–2.75 from earlier weeks).
- **In the all-models bucket Fable counts at about 2.3–2.5 times its list price**: about $0.53 per session point against $1.22 for Opus 5.5. Per token that is about 6 times Opus 5.5, against a published gap of 2.5 times.
- **Unresolved:** history implies a Fable weight of only 1.3–1.5 (the weeks ending 09-15 and 09-29). The likely reason is that the weight differs by token type: test C was mostly output, while day-to-day Fable use is mostly cache. A Fable cache-heavy run (phase D's shape on Fable 5.1) would settle it.

**Test 3 (phase E, Fable 5.1, one run, mostly cache read):** $7.79 at list price (cache read 18.8M, cache write 1h 105k, output 19k). Meters 67/40/40 → 82/41/43: session **+15**, week +1, Fable +3. Weight 2.35 (2.2–2.5), so Fable's cache counts at the same ~2.3× its list price as its output. The Fable bucket again matched list price. The gap with history (1.3–1.5) is therefore not about token type; the next section traces it to the weekly scale and the limit change on 2026-09-14.

## Limit changes over the calibration weeks (reviewed 2026-09-24)

Anthropic changed limits during these weeks. Weekly windows here end Tuesday 03:00 UTC.

| When (UTC) | Change | Source | Weeks affected |
|---|---|---|---|
| until 2026-09-14 06:59 | Claude Code weekly limits +50% over base (temporary, since May 13) | Anthropic help center, ClaudeDevs | 08-25, 09-01, 09-08, 09-15 (last reading 09-14 06:43, before the switch) |
| from 2026-09-14 07:00 | Weekly +25% over base, permanent: 1.2 times less than during the promotion | ClaudeDevs | 09-22, 09-29 |
| 2026-09-01 | Fable 5.1 launch; 5-hour and weekly meters reset for all users | Reported, quoting ClaudeDevs | None: the ledger has no Claude usage from the 09-08 week start until 09-07 01:20 |
| 2026-09-22 | Opus 5.5 launch; Claude Code 5-hour limits +20% (no end date stated); one saved reset, usable until 10-22 | ClaudeDevs | 09-29 |

What this changes:

- **The weekly scale is about $11 per point, not $9.8.** Across all five test phases the meters moved +62 session and +6 weekly points, a ratio of about 10 (8.7–12.6), not 8. At $1.22 per session point that is $10.6–12.6 per weekly point. This week's reading before the tests gives $11.2 under the working weights.
- **Weeks before 2026-09-14 07:00 need a factor of 1.2** (1.5 / 1.25). With it and $11 per point, the week ending 09-15 implies a Fable 5.1 weight of 2.2; the two weeks after give 2.6 and 2.35, all near the tests' 2.3. The earlier 1.3–1.5 came from $9.8 without the factor. Opus 5 comes out at 0.6 in both full Opus 5 weeks (08-25, 09-22), close to test D's 0.4–0.6. The Fable bucket also stepped down about 1.4 times at 09-14 ($3.5–3.7 → $2.0–2.75 per point), although the help center says the promotion did not change per-model limits.
- **Fable 5 and Fable 5.1 need separate weights.** The week ending 09-01 used only Fable 5: $7.45 of list price per Fable-bucket point, against $3.5–3.7 for Fable 5.1 in the promotion week. In the all-models bucket Fable 5 comes out near 1.1. This fits reports that Fable 5.1 uses limits about twice as fast. FlightDeck prices Fable 5 cache reads at 0.1× input; if that price is wrong, so is the ratio.
- **Not explained: the session-to-week ratio did not fall.** Promotion weeks gave about 9.5 session points per weekly point (consecutive readings, 16 windows). The two changes predict about 6.6 now; the tests measured about 10. Either a change did not reach these meters, or it applies only in some cases. A full 5-hour window used only in Claude Code settles it: about 15% of the week if both changes apply, about 10% if neither does.

Working formula: week % ≈ ($Opus 5.5 + 0.5 × $Opus 5 + 2.3 × $Fable 5.1 + 1.1 × $Fable 5 + $other) / (11 × f), where $ is list-price API-equivalent and f = 1.2 before 2026-09-14 07:00 UTC, otherwise 1.

Refit on all 198 readings with f applied (`refit`, 2026-09-24): held-out week error falls from 9.7 to 5.8 points on average (week 09-15: 21.9 → 6.9, week 09-22: 13.7 → 4.9). At $11 per point the fitted weights are Opus 5 0.58, Fable 5.1 2.0, Fable 5 0.75; the week ending 09-01 supplies 144 of the readings, so the Fable 5 weight leans on one week. History cannot fit Opus 5.5 (one reading above 5%), so $11 comes from the tests.

## History fit (superseded)

The weekly "all models" percentage follows **output tokens weighted by model**, not API-equivalent cost.

| Model family | % of week per 1M output tokens | 1% of the week ≈ |
|---|---:|---:|
| Opus 5 / 4.8 | 10.7 | 93k output tokens |
| Opus 5.5 | 4.1 (provisional) | 245k output tokens |
| Fable 5 / 5.1 | 35.2 | 28k output tokens |
| Sonnet / Haiku | 0.5 | ~2M output tokens (little data) |

The separate **Fable weekly bucket**: 1% ≈ 13–17k Fable output tokens, stable across four weeks.

## How it was checked

- **This week first.** The model-weighted sum predicts this week's readings within 2 points (27.3% predicted against 29%, 9.0% against 9%).
- **Then earlier weeks, one week held out at a time.** Fit on five weeks, predict the sixth. Mean absolute error per held-out week: 0.5, 1.5, 2.7, 5.7, 1.1, 2.3 points.
- **Alternatives lose.** One coefficient on API-equivalent cost drifts from $21 per 1% (late August) to $10 (this week), and its held-out error reaches 24 points. Cost by model family does no better (up to 26). Adding input, cache-write or cache-read tokens to the output model gets zero weight.

## Limits

- **Opus 5.5 appears only this week**, over readings from 9% to 29%, so its coefficient is weakly determined. Re-fit after the 2026-09-29 reset.
- **Cache reads get zero weight, but that is not proof they are free.** In agent work they rise together with output, so the fit cannot separate the two.
- **The quota also counts claude.ai and other devices**, which the ledger does not see. In the week ending 2026-08-25 the Fable bucket moved while the ledger had no Fable rows. The coefficients absorb a steady share of that; a week with heavy chat use will read low.
- **`/usage` reports whole percents**, so readings under 5% were dropped.

## Reproduce

```bash
cd scripts/cost-audit/quota
python3 quota_hist.py                      # transcripts -> readings.json
# needs the ledger URL from flight-deck.sh/.env
PYTHONPATH=../../../backend ../../../.venv/bin/python calib.py      # single-measure coefficients per week
PYTHONPATH=../../../backend ../../../.venv/bin/python calib4.py     # model comparison, held-out weeks
PYTHONPATH=../../../backend ../../../.venv/bin/python thisweek.py   # this week's readings vs the fit
```

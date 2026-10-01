# Quota experiment: Opus 5.5 output vs context

Tests two things the ledger fit could not separate:

- **A (output-heavy):** 8 parallel runs, no tools, about 25k output tokens each, about 200k in total.
- **B (context-heavy):** 6 parallel runs. Each reads about 120k tokens of files, then does 30 tiny reads. Every request re-reads the whole context (cache read) and writes only about 50 tokens. That is about 25M cache-read tokens and under 20k output in total.

## Before you start

- Nothing else may use Claude in this window: no other Claude Code session (including this one), no claude.ai chat, no Odoo "send to session". FlightDeck's task labeling uses Codex, so it is fine.
- Finish before the 5h window resets, or start right after it resets. A phase must not straddle a reset.

## Steps

Read the meter from the claude.ai usage page (or `/usage`), writing down both **Session** and **Weekly**. After each phase, wait 3 minutes, read again, and read once more at +10 minutes in case the meter lags.

| Step | Command | Session % | Weekly % | Time |
|---|---|---:|---:|---|
| 0. Baseline | (read) | | | |
| 1. Phase A | `./phase_a.sh` (about 10 min) | | | |
| 1b. +10 min | (read) | | | |
| 2. Phase B | `./phase_b.sh` (about 5–10 min) | | | |
| 2b. +10 min | (read) | | | |

Then run `python3 analyze.py a` and `python3 analyze.py b`. Each prints the tokens the phase actually used and a prediction table.

## Reading the result

The meter shows whole percents, so each reading is ±0.5 and each difference is ±1 session point.

- **A:** session +7 means the current Opus 5.5 coefficient (4.1 per 1M output tokens) holds. About +11 means the higher value from the prospective check (about 7). The table gives the exact numbers for the tokens actually used.
- **B:** 0–1 session points means cache reads count for little or nothing. At about 0.005 per 1M tokens (the upper bound the fit allows), expect about +1 more point. At the API price ratio (0.04), about +8. B detects only the large case. Smaller weights need hundreds of millions of cache-read tokens.
- Any Weekly change should be about one-eighth of the Session change. If it is not, the 12.4% ratio needs checking.

## Test 2 (set 2026-09-23 15:55): other models, predictions fixed before running

Tests two claims at once. **H1:** the quota is API-equivalent cost at each model's list price, so our method and Anthropic's published price gaps both hold. **H0:** the same tokens are weighted as if every model cost like Opus 5.5.

Scale from test 1 (phases A+B, and this week's `/usage` readings): $1.22 per session point, $9.8 per weekly point. From Fable 5.1 weeks: $2.6 per Fable-bucket point (range $2.4–3.7).

| Phase | Run | Planned tokens | H1 session / week / Fable | H0 session | Cache read free |
|---|---|---|---|---:|---:|
| D (first) | `./phase_d.sh`: Opus 5, 2 runs, 6 big reads + 60 tiny reads | cache read 20M, cache write 1h 320k, output 10k | **+11** / +1.4 / 0 | +5.5 | +2.8 |
| C (second) | `./phase_c.sh`: Fable 5.1, 4 runs, like phase A | output 164k, cache write 1h 127k | **+8.8** / +1.1 / **+4.1** | +3.5 | +8.8 |

- D tests the cache-read price gap (Opus 5 is 2.5 times Opus 5.5), and whether cache reads count at all.
- C tests the output price gap (Fable 5.1 is 2.5 times Opus 5.5), with a second meter in the Fable bucket.
- Pass for H1: each phase within ±2 session points of the H1 value after re-pricing the tokens actually used (`python3 analyze2.py d`, `python3 analyze2.py c`), and outside the H0 value.
- Read Session, Weekly **and Weekly Fable**: before D, 3 minutes after D (then start C), 3 minutes after C, and again 10 minutes after C.

## Test 3 (set 2026-09-23 16:07): Fable 5.1 cache weight, one run

Question: in the all-models bucket, is Fable's cache charged at the same 2.3× its list price as its output (test C), or near the 1.3–1.5 that history implies?

`./phase_e.sh`: 1 run, 3 big reads, then 200 tiny reads. Planned: cache read 19M, cache write 1h 95k, output 14k. List price $7.35, of which cache read $4.75.

| If Fable's cache weight is | Session | Weekly | Fable bucket |
|---|---:|---:|---:|
| 2.3 (same as output) | **+13.9** | +1.7 | +2.8–3.2 |
| 1.4 (history) | **+8.4** | +1.1 | +2.8–3.2 |
| 1.0 (list price) | **+6.0** | +0.8 | +2.8–3.2 |
| cache read not counted | +4.9 | | |

Re-price with the tokens actually used: `python3 analyze2.py e`, then scale the rows above by (actual list price / 7.35). Read the three meters before, 3 minutes after, and 10 minutes after. The run is sequential (about 10–15 minutes); finish before the 17:10 reset.

# fd thinking

```text
Feature: Thinking trends from fd
Goal: An agent answers "how did thinking tokens and thinking time move over the last N days" with one fd command.
Now: fd ingests only billed token counts; thinking lives in the transcripts (usage.output_tokens_details.thinking_tokens, thinkingDurationMs since CLI 2.1.289) and nowhere in the ledger.
Direction: Ledger v2 stores thinking tokens, thinking time, the time to the end of thinking and effort per response; `fd thinking` answers from the ledger, or from the transcripts with --raw.
Constraints: Spend numbers unchanged; ledger rows never deleted; one JSON document on stdout with --json.
Done when: `fd thinking --days 7` gives per-day figures and a trend; --raw prints the slow-read and oldest-transcript warnings; a skill leads an agent from the question to a sourced answer.
Not now: UI, the Odoo module, per-session or per-agent breakdowns.
Refs: core/internal/{ledger,ingest,metrics}, core/cmd/fd/thinking.go, .agents/skills/fd
```

## Decisions

- Ledger first, `--raw` second, because the ledger keeps responses whose transcripts Claude Code has deleted and answers without reading 2 GB again.
- Schema v2 sets every read cursor back to 0, because ingest never re-reads bytes it passed; the next run (20 s once, on this machine) fills the new columns on existing rows and adds no row. The v1 file was copied to `~/.flightdeck/fd/ledger.db.v1-2026-10-06` first.
- Three thinking figures, kept apart: `thinking_tokens` (usage), `thinking_seconds` (the CLI's `thinkingDurationMs`, only 2.1.289 on), `wait_seconds` (last line before the response to its last thinking block), because only the third covers the days before 2026-10-06 and it also holds the first-token wait (about 2× thinking on 2026-10-06).
- A response keeps the largest value any of its lines gave, as `output_tokens` already does.
- Medians per day and a least-squares slope in % of the mean per day; a day counts with 5+ values, a slope needs 3+ days, because thin days and two-point lines read as trends that are not there.
- `--raw` warns on stderr and repeats the warnings in the JSON, so a person sees them and an agent can quote them.
- How far `--raw` reaches: complete from today − cleanupPeriodDays (read from `~/.claude/settings.json`, else 30), because Claude Code deletes by inactivity; the oldest line on disk (2026-07-14) only shows how far incomplete days go.
- Subagent transcripts carry no `output_tokens_details`, so token figures are the sessions' own; said in the output notes rather than hidden.
- A `sidechain` column and `--agent` / `--by agent`, because the baseline run showed subagents (3,037 of 7,578 responses this week) moving the daily figures; it went into schema v2 before v2 left this machine, so the local ledger was rebuilt from the v1 copy.
- `--from`/`--to`/`--split-at` taking a date or a time with its zone, because the first before/after test (split at the AGENTS.md rewrite, 2026-10-01 09:27 UTC) had to drop the whole changeover day and its 428 responses; bounds are turned into the transcripts' own UTC stamp form so they compare as text.
- `-h`/`--help` prints the command's flags with exit 0, because the test agent's first move was `fd thinking --help` and it got a refusal.
- The skill is a skills-directory plugin `fd` with child `fd:thinking`, like `nakivo-jira`, so later fd questions (spend, sessions) become siblings.

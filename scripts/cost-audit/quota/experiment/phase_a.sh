#!/usr/bin/env bash
# Phase A: output-heavy, no tools, no files. 8 parallel runs of ~25k output tokens.
set -euo pipefail
cd /tmp/fd-quota-exp
export CLAUDE_CODE_MAX_OUTPUT_TOKENS=32000
PROMPT='Do not use any tools and do not write anything else. Print exactly 2000 lines. Line k is "k. the quick brown fox jumps over the lazy dog" for k = 1 to 2000, in order, with no gaps and no abbreviation.'
date '+phase A start %T'
for i in 1 2 3 4 5 6 7 8; do
  claude -p "$PROMPT" --model claude-opus-5-5 --effort low --tools "" --strict-mcp-config \
    --output-format json > "out/a_$i.json" 2> "out/a_$i.err" &
done
wait
date '+phase A end %T'

#!/usr/bin/env bash
# Phase C: Fable 5.1, output-heavy, no tools. Same shape as phase A, 4 runs.
set -euo pipefail
cd /tmp/fd-quota-exp
export CLAUDE_CODE_MAX_OUTPUT_TOKENS=32000
PROMPT='Run C. Do not use any tools and do not write anything else. Print exactly 2000 lines. Line k is "k. the quick brown fox jumps over the lazy dog" for k = 1 to 2000, in order, with no gaps and no abbreviation.'
date '+phase C start %T'
for i in 1 2 3 4; do
  claude -p "$PROMPT" --model claude-fable-5-1 --effort low --tools "" --strict-mcp-config \
    --output-format json > "out/c_$i.json" 2> "out/c_$i.err" &
done
wait
date '+phase C end %T'

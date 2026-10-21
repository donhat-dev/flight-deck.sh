#!/usr/bin/env bash
# Phase D: Opus 5, cache-read-heavy. Read ~120k tokens once, then 60 tiny reads,
# so cache reads are most of the cost.
set -euo pipefail
cd /tmp/fd-quota-exp
PROMPT='Run D. Follow these steps exactly, one tool call per message, and write no text between tool calls.
1. Read big1.txt, big2.txt, big3.txt, big4.txt, big5.txt, big6.txt (one Read each, full file).
2. Then Read tiny.txt 60 times in a row, one Read per message.
3. Then reply with the single word: done'
date '+phase D start %T'
for i in 1 2; do
  claude -p "$PROMPT" --model claude-opus-5 --effort low --tools Read --allowedTools Read \
    --strict-mcp-config --output-format json > "out/d_$i.json" 2> "out/d_$i.err" &
done
wait
date '+phase D end %T'

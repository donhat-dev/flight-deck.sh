#!/usr/bin/env bash
# Phase B: context-heavy, near-zero output. Read ~120k tokens, then 30 tiny reads,
# so each later request re-reads the whole context (cache read) and writes ~50 tokens.
set -euo pipefail
cd /tmp/fd-quota-exp
PROMPT='Follow these steps exactly, one tool call per message, and write no text between tool calls.
1. Read big1.txt, big2.txt, big3.txt, big4.txt, big5.txt, big6.txt (one Read each, full file).
2. Then Read tiny.txt 30 times in a row, one Read per message.
3. Then reply with the single word: done'
date '+phase B start %T'
for i in 1 2 3 4 5 6; do
  date '+phase B iteration $i start %T'
  claude -p "$PROMPT" --model claude-opus-5-5 --effort low --tools Read --allowedTools Read \
    --strict-mcp-config --output-format json > "out/b_$i.json" 2> "out/b_$i.err" &
  date '+phase B iteration $i end %T'
done
wait
date '+phase B end %T'

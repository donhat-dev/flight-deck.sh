#!/usr/bin/env bash
# Phase E: Fable 5.1, cache-read-heavy, one run. Read ~60k tokens once, then 200
# tiny reads, so cache reads are about two thirds of the list-price cost.
set -euo pipefail
cd /tmp/fd-quota-exp
PROMPT='Run E. Follow these steps exactly, one tool call per message, and write no text between tool calls.
1. Read big1.txt, big2.txt, big3.txt (one Read each, full file).
2. Then Read tiny.txt 200 times in a row, one Read per message.
3. Then reply with the single word: done'
date '+phase E start %T'
claude -p "$PROMPT" --model claude-fable-5-1 --effort low --tools Read --allowedTools Read \
  --strict-mcp-config --output-format json > out/e_1.json 2> out/e_1.err
date '+phase E end %T'

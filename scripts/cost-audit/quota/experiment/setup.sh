#!/usr/bin/env bash
# Build the throwaway work dir for the quota experiment. Outside the workspace,
# so no project CLAUDE.md is loaded into the runs.
set -euo pipefail
W=/tmp/fd-quota-exp
mkdir -p "$W/out"
cd "$W"
# Six ~20k-token files for phase B (1,000 lines of ~55 chars each, under Read's limits).
for n in 1 2 3 4 5 6; do
  python3 - "$n" <<'PY'
import random, sys
n = int(sys.argv[1]); random.seed(n)
words = "amber basalt cobalt delta ember fjord granite harbor indigo juniper kelvin lumen".split()
with open(f"big{n}.txt", "w") as f:
    for i in range(1000):
        f.write(f"{n}-{i:04d} " + " ".join(random.choice(words) for _ in range(7)) + "\n")
PY
done
echo "ok" > tiny.txt
ls -la "$W"

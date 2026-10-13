"""How often each memory was actually pulled into a context window.

Recall in this harness is a plain Read of the file, so the transcripts hold a complete
record of which memories were ever used. Age is the proxy everyone reaches for; this is
the real signal, and it turns out to be far more discriminating: most of a store can be
old-but-load-bearing or new-but-never-touched, and only this tells them apart.

Two honest limits, stated here so callers do not over-read the number. Transcripts are
duplicated by session resume, so a count is an upper bound on distinct reads. And a
memory also reaches the model through its one-line description in the index, which is
injected without any Read at all — so a count of zero means "the body never entered a
context window", not "this memory was never used".
"""
import json
from pathlib import Path


def read_counts(transcript_dir: Path, memory_dir: Path) -> dict[str, int]:
    if not Path(transcript_dir).is_dir():
        return {}
    known = {p.name for p in Path(memory_dir).glob("*.md")}
    memory_prefix = str(Path(memory_dir))
    counts: dict[str, int] = {}

    for transcript in Path(transcript_dir).glob("*.jsonl"):
        with transcript.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if memory_prefix not in line:
                    continue
                try:
                    turn = json.loads(line)
                except ValueError:
                    continue
                for block in (turn.get("message") or {}).get("content") or []:
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    if block.get("name") not in ("Read", "Grep"):
                        continue
                    target = str((block.get("input") or {}).get("file_path")
                                 or (block.get("input") or {}).get("path") or "")
                    if not target.startswith(memory_prefix):
                        continue
                    name = Path(target).name
                    # A Grep aimed at the directory reads across every file in it.
                    hits = [name] if name in known else sorted(known)
                    for n in hits:
                        counts[n] = counts.get(n, 0) + 1
    return counts

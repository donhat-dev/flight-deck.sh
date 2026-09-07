"""Eight checks over the memory store, ordered by how much each one costs the reader.

The order is the argument. A memory with no checkable source is worse than a memory
with a broken link: the link is a navigation defect, the missing source means the claim
cannot be promoted to evidence at all. And a memory absent from the index is worse than
one that is merely unlinked, because the index is the only thing the harness injects —
a file missing from it is permanently invisible, not just poorly connected.

Provenance is checked by type, not uniformly. A `reference` memory describes a live
system and rots against it, so it owes a source. A `feedback` memory records something
a person said; the transcript IS the source and there is no outside truth to check it
against. Flagging those would produce noise on a third of the store and teach the
reader to ignore the report.
"""
import difflib
from pathlib import Path

from flightdeck.memory import store, usage

#: Heaviest first. `findings` is sorted by this, and it is the whole editorial claim.
KIND_ORDER = ["no_source", "dead_origin", "weak_description", "not_in_index",
              "broken_link", "not_linked", "missing_file", "future_target"]

#: Only this type is checked for a source. See the module docstring.
SOURCE_REQUIRED_TYPES = {"reference"}

MIN_DESCRIPTION_CHARS = 20
NEAR_DUPLICATE_RATIO = 0.85
CLOSE_NAME_RATIO = 0.75


def _finding(kind, memory, detail, suggestion=None, action=None):
    return {"kind": kind, "memory": memory, "detail": detail,
            "suggestion": suggestion, "action": action}


def run(memory_dir, transcript_dir=None, cwd=None):
    memory_dir = Path(memory_dir)
    memories = store.load_all(memory_dir)
    index = store.load_index(memory_dir)
    names = {m.name for m in memories}
    files = {m.filename for m in memories}
    indexed_files = {filename for _t, filename, _h in index}
    findings = []

    # Only a link that RESOLVES connects two memories. A broken link and an alias
    # pointing at something that does not exist yet are both reported in their own
    # right, but neither is a connection — counting them here would let a file with
    # nothing but a dead pointer escape the isolation check, and would put this table
    # at odds with the graph, which draws edges from resolvable links only.
    linked_from = {m.name: set() for m in memories}
    resolvable = {m.name: [t for t in m.links if t in names] for m in memories}
    for m in memories:
        for target in resolvable[m.name]:
            linked_from[target].add(m.name)

    for m in memories:
        if m.type in SOURCE_REQUIRED_TYPES and not m.source:
            findings.append(_finding(
                "no_source", m.name,
                "A reference memory with no source cannot be checked against the "
                "system it describes.", action="add_source"))

        if m.description == "" or len(m.description) < MIN_DESCRIPTION_CHARS:
            findings.append(_finding(
                "weak_description", m.name,
                f"The summary line is {len(m.description)} characters. It is the only "
                "cue the recall step has."))

        if m.filename not in indexed_files:
            findings.append(_finding(
                "not_in_index", m.name,
                "On disk but not in MEMORY.md, so it is never loaded.",
                action="add_to_index"))

        for target in m.links:
            if target in names:
                continue
            close = difflib.get_close_matches(target, sorted(names), n=1,
                                              cutoff=CLOSE_NAME_RATIO)
            findings.append(_finding(
                "broken_link", m.name, f"Links to [[{target}]], which does not exist.",
                suggestion=close[0] if close else None,
                action="rename_link" if close else None))

        for target in m.alias_links:
            findings.append(_finding(
                "future_target", m.name,
                f'Points at "{target}", which does not exist yet. Not a broken '
                "link — the target does not follow the naming rule at all."))

        if not resolvable[m.name] and not linked_from[m.name]:
            findings.append(_finding(
                "not_linked", m.name, "No links in, and none out that resolve.",
                action="suggest_links"))

    for _title, filename, _hook in index:
        if filename not in files:
            findings.append(_finding(
                "missing_file", filename,
                "The index points at a file that is not on disk.",
                action="remove_index_line"))

    findings.extend(_weak_by_similarity(memories))
    findings.extend(_dead_origins(memories, transcript_dir))

    findings.sort(key=lambda f: (KIND_ORDER.index(f["kind"]), f["memory"]))
    counts = {}
    for f in findings:
        counts[f["kind"]] = counts.get(f["kind"], 0) + 1
    return {"counts": counts, "findings": findings, "total_memories": len(memories)}


def _weak_by_similarity(memories):
    """Two memories whose summaries read alike share one cue and neither wins it."""
    out = []
    for i, a in enumerate(memories):
        for b in memories[i + 1:]:
            if not a.description or not b.description:
                continue
            ratio = difflib.SequenceMatcher(None, a.description.lower(),
                                            b.description.lower()).ratio()
            if ratio >= NEAR_DUPLICATE_RATIO:
                out.append(_finding(
                    "weak_description", a.name,
                    f"Its summary reads almost the same as {b.name}, so a query "
                    "matching one matches both."))
    return out


def _dead_origins(memories, transcript_dir):
    if transcript_dir is None:
        return []
    alive = {p.stem for p in Path(transcript_dir).glob("*.jsonl")}
    return [_finding("dead_origin", m.name,
                     "The session that produced it is no longer on disk, so the claim "
                     "has no record behind it. Demote it, do not delete it.",
                     action="demote_tier")
            for m in memories if m.origin_session and m.origin_session not in alive]


def with_usage(memory_dir, transcript_dir, cwd=None):
    """`run` plus a `used` count per memory — the retention signal age only approximates."""
    result = run(memory_dir, transcript_dir=transcript_dir, cwd=cwd)
    counts = usage.read_counts(Path(transcript_dir), Path(memory_dir))
    memories = store.load_all(Path(memory_dir))
    result["usage"] = {m.filename: counts.get(m.filename, 0) for m in memories}
    result["never_read"] = sorted(n for n, c in result["usage"].items() if c == 0)
    return result

"""Component lint + post-render validation (docs/treasures-components.md).

The contract: an agent writes ONE markdown file that must read correctly in a
plain CommonMark tool (GitHub, VS Code preview, Obsidian) AND render the house
components once wrapped. Components are authored as

    <div data-component="hero">      <-- blank line REQUIRED after this
                                          and before the closing </div>
    </div>

    ... <span data-component="badge" data-tone="good">PASS</span> ...

Three stages live here, in the order `service.wrap` calls them:

`lint`      - autofixes the missing blank line, rejects an unknown component.
`validate`  - after render, proves each component reached the HTML as a real
              attribute rather than as escaped text.
`strip`     - the safe fallback: remove every component marker so the document
              renders as plain markdown.

Why autofix rather than reject for the blank line: the repair is deterministic
and unique (insert one blank line), so refusing the wrap would only cost a
round trip. Why reject for an unknown name: intent cannot be guessed, so that
one is fail-closed.

Why the blank line matters at all, and why nothing downstream can catch it:
pandoc's `markdown_in_html_blocks` parses the inner markdown either way, so the
artifact looks perfect while a CommonMark viewer prints the raw `#` line. The
divergence is invisible from inside the pipeline — hence a lint stage.
"""
import re

# One entry per component in docs/treasures-components.md §2. Adding a
# component means: an entry here, a rule block in tokens.css, a line in the
# skill. `hero`/`card` are block-level, `badge` is inline.
BLOCK_COMPONENTS = ("hero", "card")
INLINE_COMPONENTS = ("badge",)
COMPONENTS = BLOCK_COMPONENTS + INLINE_COMPONENTS

# An opening/closing tag for a BLOCK component, alone on its line. Inline
# components live inside a paragraph and need no blank-line handling, so the
# `div|section` restriction here is what keeps them out of the autofix.
_OPEN_RE = re.compile(
    r"^[ \t]*<(?P<tag>div|section)\b[^>]*\bdata-component=[\"'](?P<name>[^\"']+)[\"'][^>]*>[ \t]*$",
    re.IGNORECASE)
_CLOSE_RE = re.compile(r"^[ \t]*</(?:div|section)>[ \t]*$", re.IGNORECASE)

# Any data-component in the source, whatever the element. Used for the
# allowlist check and to count what validation must find in the output.
_ANY_RE = re.compile(r"data-component=[\"']([^\"']+)[\"']", re.IGNORECASE)

# A whole component tag, for `strip`. Non-greedy and attribute-safe.
_TAG_RE = re.compile(
    r"</?(?:div|section|span)\b[^>]*\bdata-component=[\"'][^\"']+[\"'][^>]*>"
    r"|</(?:div|section|span)>",
    re.IGNORECASE)


class ComponentError(ValueError):
    """An unknown component name. Fail-closed: nothing is rendered or stored."""


def unknown_components(text: str) -> list[str]:
    """Component names in `text` that are not in the allowlist, deduped."""
    seen, bad = set(), []
    for name in _ANY_RE.findall(text or ""):
        low = name.strip().lower()
        if low not in COMPONENTS and low not in seen:
            seen.add(low)
            bad.append(name.strip())
    return bad


def component_names(text: str) -> list[str]:
    """Every component name used in `text`, in source order, with duplicates."""
    return [n.strip().lower() for n in _ANY_RE.findall(text or "")]


def lint(text: str) -> tuple[str, list[str]]:
    """Return `(fixed_text, notes)`; raise ComponentError on an unknown name.

    The only fix applied is inserting the blank line CommonMark needs after a
    block component's opening tag and before its closing tag.
    """
    bad = unknown_components(text)
    if bad:
        raise ComponentError(
            f"unknown component(s): {', '.join(repr(b) for b in bad)} — "
            f"allowed: {', '.join(COMPONENTS)}")

    lines = text.split("\n")
    out: list[str] = []
    notes: list[str] = []
    # Track how deep we are inside block components, so a closing tag is only
    # padded when it actually closes one (a stray </div> is left alone).
    depth = 0
    for i, line in enumerate(lines):
        m = _OPEN_RE.match(line)
        if m and m.group("name").strip().lower() in BLOCK_COMPONENTS:
            out.append(line)
            depth += 1
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            if nxt.strip():
                out.append("")
                notes.append(
                    f"inserted the blank line CommonMark needs after "
                    f"<{m.group('tag')} data-component=\"{m.group('name')}\"> "
                    f"(line {i + 1})")
            continue
        if depth and _CLOSE_RE.match(line):
            if out and out[-1].strip():
                out.append("")
                notes.append(
                    f"inserted the blank line CommonMark needs before the "
                    f"closing tag (line {i + 1})")
            out.append(line)
            depth -= 1
            continue
        out.append(line)
    return "\n".join(out), notes


def strip(text: str) -> str:
    """Remove every component tag, leaving the inner markdown untouched.

    The safe fallback. A plain artifact still carries the full house
    typography, colour and background; only the custom elements are missing,
    which always beats an artifact printing raw tags as text.
    """
    cleaned = _TAG_RE.sub("", text or "")
    # Collapse the blank-line runs the removed tags leave behind.
    return re.sub(r"\n{3,}", "\n\n", cleaned)


def validate(source: str, html: str) -> list[str]:
    """Problems found by comparing the source's components with the output.

    A component that failed to become an element shows up in the HTML as the
    escaped literal `&lt;div data-component=` instead of a real attribute —
    that asymmetry is the entire test.
    """
    problems: list[str] = []
    escaped = re.findall(
        r"&lt;/?(?:div|section|span)[^&]*data-component", html or "",
        re.IGNORECASE)
    if escaped:
        problems.append(
            f"{len(escaped)} component tag(s) rendered as text instead of an "
            f"element — the markup reached the output escaped")
    wanted = len(component_names(source))
    got = len(_ANY_RE.findall(html or ""))
    if wanted and got < wanted:
        problems.append(
            f"only {got} of {wanted} component(s) survived into the HTML")
    return problems

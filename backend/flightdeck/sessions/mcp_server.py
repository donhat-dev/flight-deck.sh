"""Session-search MCP surface — search your own past Claude Code sessions.

Two tools, deliberately. `session_search` is cheap and answers "was this ever
discussed, and where"; `session_read` is expensive and answers "what exactly was
said around there". Splitting them leaves the decision of how much context a hit
is worth to the caller, instead of a tool spending it on a guess.

Three properties worth knowing before calling anything.

**An empty result and a broken query are different answers.** A query that parses
to nothing — all punctuation, all stop words — comes back as an `error` with the
reason, never as `results: []`. When you get an empty list, the corpus really was
searched and really had nothing.

**Two indexes, picked for you.** Words and phrases go to the ranked full-text
index. An unspaced fragment (`.env`, `_sale`, `CRM-121`, `作用`) falls back to a
substring index when word search finds nothing AND the word is genuinely absent
— never when a filter is what emptied the result. The `mode` field says which
index answered and `ranked` says whether the order means anything, so a
surprising result set is traceable.

**Accents fold, code does not stem.** The text configuration is `simple` plus
`unaccent`: `tim kiem` finds `tìm kiếm`, and `nakivo_sale` is not stemmed into
something that no longer matches. Search is over what was *said* and what was
*run* (tool inputs) — never over tool output, which is two thirds of the corpus
and none of the signal.

The corpus is whatever `flightdeck.ingest` has read. It grows on every ingest
sweep and needs no separate index maintenance.
"""
from flightdeck.agentsurface import runtime
from flightdeck.sessions import service, store


def session_search(query, project=None, since=None, role=None, has_text=False,
                   limit=10):
    return service.search(runtime.conn(), query, project=project, since=since,
                          role=role, has_text=has_text, limit=limit)


def session_read(uuid=None, session_id=None, before=5, after=5, mode="window"):
    return service.read(runtime.conn(), uuid=uuid, session_id=session_id,
                        before=before, after=after, mode=mode)


def session_corpus():
    store.require_pg()
    return store.corpus_stats(runtime.conn())


TOOLS = {
    "session_search": (
        session_search,
        "Search your own past Claude Code sessions by what was said and what was "
        "run. Returns pointers — snippet, session_id, uuid, title, timestamp, "
        "rank — not transcripts; feed a uuid to session_read to actually read "
        "around a hit. A query that parses to nothing returns an error stating "
        "why, so an empty `results` list always means the corpus was searched "
        "and had nothing. `mode` reports which index answered.",
        {"query": {"type": "string",
                   "description": "words, a \"quoted phrase\", -excluded terms, "
                                  "or an unspaced fragment for a substring "
                                  "search (3+ chars, or 2+ for CJK)"},
         "project": {"type": "string",
                     "description": "exact cwd to scope to, e.g. "
                                    "'/home/nathando/Documents/Projects'"},
         "since": {"type": "string",
                   "description": "ISO date or timestamp; only newer messages"},
         "role": {"type": "string", "enum": ["user", "assistant"],
                  "description": "'user' finds what you asked, 'assistant' what "
                                 "was answered"},
         "has_text": {"type": "boolean",
                      "description": "keep only messages that carry a text "
                                     "block. 61% of the corpus is a tool call "
                                     "with no text at all; this drops those. It "
                                     "does NOT judge whether the text is human "
                                     "prose — a pasted log still qualifies."},
         "limit": {"type": "integer", "description": "default 10, max 20"}},
        ["query"]),
    "session_read": (
        session_read,
        "Read the messages around a search hit. Default mode 'window' takes the "
        "`uuid` of a hit and returns the messages before and after it. Mode "
        "'bookend' takes a `session_id` and returns only its first and last few "
        "messages — the goal it started from and the conclusion it reached — "
        "without loading the middle.",
        {"uuid": {"type": "string",
                  "description": "anchor message, from a session_search result "
                                 "(window mode)"},
         "session_id": {"type": "string", "description": "bookend mode"},
         "before": {"type": "integer",
                    "description": "messages before the anchor, default 5, max "
                                   "40; in bookend mode this is how many from "
                                   "each end, default 3, max 10"},
         "after": {"type": "integer",
                   "description": "messages after the anchor, default 5, max 40"},
         "mode": {"type": "string", "enum": ["window", "bookend"],
                  "description": "default 'window'"}},
        []),
    "session_corpus": (
        session_corpus,
        "How much is actually indexed right now — message count and session "
        "count. Call this when a search returns nothing and you want to know "
        "whether the corpus is the reason.",
        {}, []),
}

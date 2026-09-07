/**
 * Plain words for what the API calls things.
 *
 * `memory_lint` returns eight machine names (`no_source`, `dead_origin`, …) and an
 * `action` verb per finding. Every one of them is translated here and nowhere else,
 * so the tiles, the filter row and the table cannot end up calling the same problem
 * two different things.
 *
 * `KINDS` keeps the API's order, which is the heaviest problem first — see
 * `backend/flightdeck/memory/lint.py`, where that order is the argument the report
 * is making.
 */

export const KINDS = [
  {
    key: "no_source",
    label: "No source",
    help: "Describes a live system with nothing to check it against.",
  },
  {
    key: "dead_origin",
    label: "Session gone",
    help: "The session that produced it is no longer on disk.",
  },
  {
    key: "weak_description",
    label: "Weak summary",
    help: "The summary line is too short, or reads like another one.",
  },
  {
    key: "not_in_index",
    label: "Not in index",
    help: "On disk, but the index does not list it, so it is never loaded.",
  },
  {
    key: "broken_link",
    label: "Broken link",
    help: "A link points at a memory that does not exist.",
  },
  {
    key: "not_linked",
    label: "No links",
    help: "Nothing points at it and it points at nothing.",
  },
  {
    key: "missing_file",
    label: "Missing file",
    help: "The index lists a file that is not on disk.",
  },
  {
    key: "future_target",
    label: "Not written yet",
    help: "Points at something nobody has written.",
  },
];

const BY_KEY = Object.fromEntries(KINDS.map((k) => [k.key, k]));

export const kindLabel = (key) => BY_KEY[key]?.label || key;
export const kindHelp = (key) => BY_KEY[key]?.help || "";

/**
 * What to do about a finding. Sentences, not buttons: every endpoint behind this tab
 * only reads, so a control that looked like it would fix the row would be lying.
 */
export const ACTIONS = {
  add_source: "Add a source to the file.",
  demote_tier: "Lower its tier. Keep the file.",
  add_to_index: "Add a line for it to MEMORY.md.",
  rename_link: "Rename the link to the memory that exists.",
  suggest_links: "Link it to a memory, or let it go.",
  remove_index_line: "Remove the index line.",
};

export const actionText = (finding) =>
  ACTIONS[finding.action]
  || (finding.kind === "weak_description" ? "Rewrite the summary line." : "")
  || (finding.kind === "future_target" ? "Write the target, or drop the link." : "")
  || "";

/** A memory's type, as a word a reader recognises. `null` becomes "untyped". */
export const typeLabel = (type) => type || "untyped";

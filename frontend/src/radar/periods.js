/**
 * Which periods a move may be recorded into.
 *
 * The radar's history stops are DERIVED from the moves themselves (see
 * `service._periods`), so a quarter with no moves is simply not a stop. That is
 * right for reading history and wrong for writing it: the next move is usually the
 * first one in its quarter, and a form offering only quarters that already have
 * moves could never start a new one.
 *
 * So the options are the union of two things, and the distinction is kept visible
 * rather than flattened: the quarters the radar already knows about, and the
 * quarter it is now. Nothing else is invented — there is no "next quarter" option,
 * because recording a move into the future is a claim about work not yet done.
 */

/** `Q3 2026`, in the same shape the store already holds. */
export function currentQuarter(today = new Date()) {
  return `Q${Math.floor(today.getUTCMonth() / 3) + 1} ${today.getUTCFullYear()}`;
}

/**
 * Options for the period select, newest first, with the current quarter always
 * present and always first.
 *
 * `isNew` marks the quarter that has no moves yet. The form shows that, because
 * "this move opens a new quarter" is worth knowing before recording it — it is the
 * difference between continuing a story and starting one.
 */
export function periodOptions(periods = [], today = new Date()) {
  const now = currentQuarter(today);
  const known = periods.map((p) => p.key).filter((k) => k !== now).reverse();
  return [
    { key: now, isNew: !periods.some((p) => p.key === now) },
    ...known.map((key) => ({ key, isNew: false })),
  ];
}

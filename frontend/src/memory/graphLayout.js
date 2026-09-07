/**
 * Where every node in the link drawing sits, worked out once from the payload.
 *
 * No physics. The store has no dense clusters — most memories carry nought to three
 * links — so a force simulation would have nothing to pull apart, and its node
 * positions would land somewhere different on every open. A view somebody reads daily
 * has to look the same each time, or comparing it with yesterday means nothing. So the
 * position is a function of the data alone: one column per type, memories inside a
 * column in the order the API already sorted them (fewest links first), and the same
 * input always draws the same picture.
 *
 * The last column holds the names memories link to that are not memories. They are not
 * in `nodes`, because they do not exist; dropping them would make the drawing look
 * healthy and quietly turn the memories that point at them into unlinked ones.
 *
 * Pure and self-contained, so the arithmetic is testable without a browser.
 */

export const MISSING_COLUMN = "Broken links";

export const LAYOUT = {
  columnWidth: 208,
  columnGap: 44,
  headerHeight: 34,
  nodeHeight: 24,
  nodeGap: 7,
  padding: 16,
};

/** Column order: the types the API grouped, biggest first, then the broken column. */
function columnOrder(groups, missing) {
  const types = Object.keys(groups || {}).sort(
    (a, b) => (groups[b] || []).length - (groups[a] || []).length || a.localeCompare(b),
  );
  return missing && missing.length ? [...types, MISSING_COLUMN] : types;
}

/**
 * @param {{nodes?: array, edges?: array, missing_targets?: array, groups?: object}} data
 * @param {object} [options] — override any LAYOUT measure
 */
export function layoutGraph(data, options = {}) {
  const m = { ...LAYOUT, ...options };
  const nodes = data?.nodes || [];
  const edges = data?.edges || [];
  const missing = data?.missing_targets || [];
  const titles = columnOrder(data?.groups, missing);

  const placed = [];
  const byName = new Map();
  const columns = titles.map((title, index) => {
    const x = m.padding + index * (m.columnWidth + m.columnGap);
    const members = title === MISSING_COLUMN
      ? missing.map((name) => ({ name, missing: true }))
      : nodes.filter((n) => (n.type || "untyped") === title);
    members.forEach((member, row) => {
      const node = {
        ...member,
        x,
        y: m.padding + m.headerHeight + row * (m.nodeHeight + m.nodeGap),
        width: m.columnWidth,
        height: m.nodeHeight,
        column: title,
        // A memory nothing points at and that points nowhere is the finding the
        // drawing exists to make visible, so it is marked rather than merely quiet.
        alone: !member.missing && !member.in && !member.out,
      };
      placed.push(node);
      byName.set(node.name, node);
    });
    return { title, x, y: m.padding, width: m.columnWidth, count: members.length };
  });

  // Leave the source on the side the target is on, so a link that runs back to an
  // earlier column curves out to the left instead of looping around the node.
  const drawn = edges.flatMap((edge) => {
    const from = byName.get(edge.source);
    const to = byName.get(edge.target);
    if (!from || !to) return [];
    const forward = to.x >= from.x;
    const x1 = forward ? from.x + from.width : from.x;
    const x2 = forward ? to.x : to.x + to.width;
    const y1 = from.y + from.height / 2;
    const y2 = to.y + to.height / 2;
    const bend = Math.max(24, Math.abs(x2 - x1) / 2);
    return [{
      ...edge,
      x1,
      y1,
      x2,
      y2,
      path: `M${x1} ${y1} C${x1 + (forward ? bend : -bend)} ${y1} `
          + `${x2 - (forward ? bend : -bend)} ${y2} ${x2} ${y2}`,
    }];
  });

  const tallest = columns.reduce((tall, c) => Math.max(tall, c.count), 0);
  return {
    columns,
    nodes: placed,
    edges: drawn,
    width: m.padding * 2 + titles.length * m.columnWidth
      + Math.max(0, titles.length - 1) * m.columnGap,
    height: m.padding * 2 + m.headerHeight
      + Math.max(0, tallest * (m.nodeHeight + m.nodeGap) - m.nodeGap),
  };
}

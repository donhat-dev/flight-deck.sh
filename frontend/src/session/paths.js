/* Path-list parsing (Grep / Glob / Bash-grep -> file tree). Lifted out of
   SessionDetail unchanged so the tree renderer and the registry can share it. */

/** Extract file paths (+ per-file hit counts) from a tool result. Handles Grep
 *  files_with_matches (bare paths), content mode (path:line:...), Glob
 *  listings, and Bash `grep -rl` output. Empty map when the output is not
 *  dominated by paths. */
export function extractPaths(text) {
  const counts = new Map();
  if (!text) return counts;
  let lines = 0;
  for (let line of String(text).split("\n")) {
    line = line.trim();
    if (!line) continue;
    lines++;
    const m = /^((?:~?\/)?[\w.@\-/]+\/[\w.@\-]+?):\d+[:-]/.exec(line);
    let p = m ? m[1] : null;
    if (!p && /^(?:~?\/)?[\w.@\-/]+\/[\w.@\-]+$/.test(line)) p = line;
    if (p) counts.set(p, (counts.get(p) || 0) + 1);
  }
  if (counts.size < 2 || counts.size / Math.max(lines, 1) < 0.5) return new Map();
  return counts;
}

/** counts(Map path -> hits) -> nested tree, single-child dir chains collapsed. */
export function buildTree(counts) {
  const root = { children: new Map(), count: 0 };
  for (const [p, n] of counts) {
    let node = root;
    for (const part of p.replace(/^\/+/, "").split("/")) {
      if (!node.children.has(part)) node.children.set(part, { children: new Map(), count: 0 });
      node = node.children.get(part);
    }
    node.count += n;
    node.isFile = true;
  }
  const collapse = (node) => {
    for (const [key, child] of [...node.children]) {
      collapse(child);
      if (child.children.size === 1 && !child.isFile) {
        const [[ck, cv]] = [...child.children];
        node.children.delete(key);
        node.children.set(`${key}/${ck}`, cv);
      }
    }
  };
  collapse(root);
  return root;
}

import { describe, it, expect } from "vitest";
import { unifiedDiff } from "./diff.js";

const kinds = (rows) => rows.map((r) => r.type).join("");

describe("unifiedDiff", () => {
  it("marks one changed line, not a rewrite", () => {
    const { rows, added, removed } = unifiedDiff("a\nb\nc", "a\nB\nc");
    expect(added).toBe(1);
    expect(removed).toBe(1);
    expect(kinds(rows)).toBe("ctxdeladdctx");
  });

  it("numbers old and new sides independently", () => {
    const { rows } = unifiedDiff("a\nb", "a\nx\nb");
    const add = rows.find((r) => r.type === "add");
    expect(add.a).toBe(null);
    expect(add.b).toBe(2);
  });

  it("folds context that is far from any change", () => {
    const oldStr = Array.from({ length: 30 }, (_, i) => `l${i}`).join("\n");
    const newStr = oldStr.replace("l15", "CHANGED");
    const { rows } = unifiedDiff(oldStr, newStr, { context: 2 });
    const fold = rows.filter((r) => r.type === "fold");
    expect(fold.length).toBe(2);
    expect(fold[0].count).toBeGreaterThan(5);
    expect(rows.length).toBeLessThan(20);
  });

  it("treats a new file as all additions", () => {
    const { rows, added, removed } = unifiedDiff("", "x\ny");
    expect(removed).toBe(0);
    expect(added).toBe(2);
    expect(kinds(rows)).toBe("addadd");
  });

  it("ignores a single trailing newline", () => {
    expect(unifiedDiff("a\n", "a").rows.every((r) => r.type === "ctx")).toBe(true);
  });
});

import { describe, expect, it } from "vitest";
import { pct, shortModel } from "./format.js";

describe("format helpers", () => {
  it("formats ratio values as percentages", () => {
    expect(pct(0.95276024)).toBe("95.3%");
    expect(pct(1)).toBe("100%");
    expect(pct(0)).toBe("0%");
  });

  it("formats invalid percentages safely", () => {
    expect(pct(undefined)).toBe("—");
    expect(pct("not-a-number")).toBe("—");
  });

  it("normalizes model identifiers", () => {
    expect(shortModel("claude-opus-4-8")).toBe("Opus 4 8");
    expect(shortModel(null)).toBe("Unknown");
  });
});

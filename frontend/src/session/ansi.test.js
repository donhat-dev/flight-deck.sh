import { describe, it, expect } from "vitest";
import { parseAnsi, hasAnsi, stripAnsi } from "./ansi.js";

const ESC = "\u001b";
const flat = (lines) => lines.map((segs) => segs.map((s) => s.text).join(""));

describe("parseAnsi", () => {
  it("keeps the text and drops the escapes", () => {
    const out = parseAnsi(`${ESC}[32m34 passed${ESC}[0m, 0 failed`);
    expect(flat(out)).toEqual(["34 passed, 0 failed"]);
  });

  it("colours the segment the producer coloured", () => {
    const [line] = parseAnsi(`${ESC}[32mPASS${ESC}[0m rest`);
    expect(line[0]).toMatchObject({ text: "PASS", key: "a2" });
    expect(line[1]).toMatchObject({ text: " rest", key: null });
  });

  it("carries state across a newline", () => {
    const out = parseAnsi(`${ESC}[31mone\ntwo${ESC}[0m`);
    expect(out).toHaveLength(2);
    expect(out[0][0].key).toBe("a1");
    expect(out[1][0].key).toBe("a1");
  });

  it("reads bright, 256 and truecolor", () => {
    expect(parseAnsi(`${ESC}[91mx`)[0][0].key).toBe("b1");
    expect(parseAnsi(`${ESC}[38;5;196mx`)[0][0].hex).toBe("rgb(255,0,0)");
    expect(parseAnsi(`${ESC}[38;2;1;2;3mx`)[0][0].hex).toBe("rgb(1,2,3)");
  });

  it("drops cursor moves and OSC titles instead of printing them", () => {
    expect(flat(parseAnsi(`${ESC}[2K${ESC}[1Gclean`))).toEqual(["clean"]);
    expect(flat(parseAnsi(`${ESC}]0;title\u0007body`))).toEqual(["body"]);
  });

  it("tracks bold and dim", () => {
    const [line] = parseAnsi(`${ESC}[1mB${ESC}[22mN`);
    expect(line[0].bold).toBe(true);
    expect(line[1].bold).toBe(false);
  });

  it("survives empty and non-string input", () => {
    expect(parseAnsi("")).toEqual([[]]);
    expect(parseAnsi(null)).toEqual([[]]);
  });
});

describe("hasAnsi / stripAnsi", () => {
  it("detects only real escapes", () => {
    expect(hasAnsi("plain [32m text")).toBe(false);
    expect(hasAnsi(`${ESC}[32mgreen`)).toBe(true);
  });
  it("strips to plain text", () => {
    expect(stripAnsi(`${ESC}[32mgreen${ESC}[0m`)).toBe("green");
  });
});

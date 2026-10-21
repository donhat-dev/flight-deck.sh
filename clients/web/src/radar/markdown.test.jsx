/**
 * The prose renderer's contract: markdown in, React elements out, markup NEVER through.
 *
 * The safety claim is structural — the parser emits elements and text nodes, with no
 * `dangerouslySetInnerHTML` to keep honest — so the tests attack it the way an injection
 * would: HTML in the text, a javascript: URL in a link, markup smuggled around code
 * spans. Each must come out as literal text, not as behaviour.
 */
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import Prose, { plain } from "./markdown.jsx";

const html = (text, props = {}) => renderToStaticMarkup(<Prose text={text} {...props} />);

describe("the subset renders", () => {
  it("renders plain text unchanged, as the paragraphs it always was", () => {
    // Every blip written before markdown existed passes through here.
    const out = html("Cách hai instance gọi nhau: các route có tên thay cho execute_kw.");
    expect(out).toContain("<p>");
    expect(out).toContain("route có tên thay cho execute_kw");
  });

  it("renders bold, italic, code and links", () => {
    const out = html("**39/39** rule pairs on *real data* via `price_rule_get` — [doc](https://example.test/x)");
    expect(out).toContain("<strong>39/39</strong>");
    expect(out).toContain("<em>real data</em>");
    expect(out).toContain("<code>price_rule_get</code>");
    expect(out).toContain('href="https://example.test/x"');
    expect(out).toContain('rel="noreferrer"');
  });

  it("renders a dash block as a real list", () => {
    const out = html("- one owner per value\n- no fallback chain\n- fail closed");
    expect(out).toContain("<ul");
    expect((out.match(/<li>/g) || []).length).toBe(3);
  });

  it("splits paragraphs on blank lines and soft-wraps single newlines", () => {
    const out = html("first block\nstill first\n\nsecond block", { className: "rdr-lede" });
    expect((out.match(/<p/g) || []).length).toBe(2);
    expect(out).toContain("first block still first");
    expect(out).toContain('class="rdr-lede"');
  });

  it("keeps markdown inside code spans literal", () => {
    // `**` inside backticks is code, not emphasis — the exact reason code matches first.
    const out = html("run `SELECT ** FROM x` first");
    expect(out).toContain("<code>SELECT ** FROM x</code>");
    expect(out).not.toContain("<strong>");
  });
});

describe("markup never becomes behaviour", () => {
  it("renders raw HTML as literal text", () => {
    // The store refuses this on write; the renderer is the independent second wall.
    const out = html('<img src=x onerror=alert(1)> and <script>alert(2)</script>');
    expect(out).not.toContain("<img");
    expect(out).not.toContain("<script>");
    expect(out).toContain("&lt;img");
    expect(out).toContain("&lt;script&gt;");
  });

  it("renders XML in code spans as text inside <code>", () => {
    // The Odoo vocabulary case the write-guard also carves out.
    const out = html('the view re-adds `<field name="tz"/>` on upgrade');
    expect(out).toContain("<code>");
    expect(out).toContain("&lt;field");
    expect(out).not.toContain("<field");
  });

  it("refuses a javascript: URL by not making it a link at all", () => {
    const out = html("[click](javascript:alert(1))");
    expect(out).not.toContain("<a");
    expect(out).toContain("[click](javascript:alert(1))");
  });
});

describe("plain() flattens for the surfaces that cannot carry markup", () => {
  it("strips every token the renderer draws", () => {
    expect(plain("**bold** and `code` and [t](https://x.test/) done"))
      .toBe("bold and code and t done");
  });

  it("flattens lists and paragraph breaks into one line", () => {
    expect(plain("- a\n- b\n\nafter")).toBe("a b — after");
  });

  it("passes plain text through untouched", () => {
    expect(plain("chuẩn chung của ngành")).toBe("chuẩn chung của ngành");
  });
});

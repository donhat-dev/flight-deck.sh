/**
 * Row rendering contract.
 *
 * These are the library's first tests: the audit noted TreasuresView and
 * TreasureDetail had none, so every refactor of them was unverified. The row is
 * where the design's load-bearing claims live, so it is where the tests start.
 */
import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import React from "react";

import { TreasureListRow, TreasureMobileCard, kb, relTime } from "./TreasureRow.jsx";

const ROW = {
  id: "abc123",
  title: "Master Data Access",
  slug: "22-master-data-access",
  kind: "design-doc",
  language: "vi",
  version: 19,
  status: "draft",
  render_bytes: 145408,
  updated_at: new Date(Date.now() - 3 * 3600 * 1000).toISOString(),
  origin_kind: "doc_file",
  origin_path: "/home/u/docs/22-master-data-access.md",
};

describe("a row is a real link", () => {
  it("renders an anchor to the treasure's hash route", () => {
    const html = renderToStaticMarkup(<TreasureListRow row={ROW} />);
    // The previous build used <tr role="link">, which cannot be opened in a new
    // tab, copied as a link, or middle-clicked. The href is the whole point.
    expect(html).toContain('href="#/treasure/abc123"');
    expect(html).toMatch(/^<a /);
  });

  it("encodes an id that needs it", () => {
    const html = renderToStaticMarkup(<TreasureListRow row={{ ...ROW, id: "a/b c" }} />);
    expect(html).toContain('href="#/treasure/a%2Fb%20c"');
  });

  it("uses an anchor on mobile too", () => {
    expect(renderToStaticMarkup(<TreasureMobileCard row={ROW} />)).toMatch(/^<a /);
  });
});

describe("the source column reports what the source IS", () => {
  it("never claims a freshness verdict the payload cannot support", () => {
    // The list payload has source_checksum but not the file's current hash, so
    // "source changed" is undecidable here. Saying it anyway would be inventing a
    // fact — this test exists to keep that from creeping back in.
    const html = renderToStaticMarkup(<TreasureListRow row={ROW} />);
    expect(html).not.toMatch(/source changed/i);
    expect(html).not.toMatch(/in sync/i);
    expect(html).toContain("Tracked file");
  });

  it("prefers the published destination when there is one", () => {
    const html = renderToStaticMarkup(
      <TreasureListRow row={{ ...ROW, status: "published", published_url: "https://claude.ai/x" }} />);
    expect(html).toContain("claude.ai");
  });

  it("distinguishes a session origin from a tracked file", () => {
    const html = renderToStaticMarkup(
      <TreasureListRow row={{ ...ROW, origin_kind: "claude_session", origin_path: "" }} />);
    expect(html).toContain("Session");
    expect(html).not.toContain("Tracked file");
  });
});

describe("metadata is secondary, not three columns", () => {
  it("keeps slug, kind, language and version on one meta line", () => {
    const html = renderToStaticMarkup(<TreasureListRow row={ROW} />);
    for (const part of ["22-master-data-access", "design-doc", "vi", "v19"]) {
      expect(html).toContain(part);
    }
  });

  it("does not render size as a column of its own", () => {
    // Size moved to the detail inspector; a library row is for finding, not auditing.
    expect(renderToStaticMarkup(<TreasureListRow row={ROW} />)).not.toContain("142.0 KB");
  });
});

describe("helpers", () => {
  it("formats recent times in the largest sensible unit", () => {
    const ago = (ms) => relTime(new Date(Date.now() - ms).toISOString());
    expect(ago(5 * 60000)).toBe("5m ago");
    expect(ago(3 * 3600000)).toBe("3h ago");
    expect(ago(2 * 86400000)).toBe("2d ago");
    expect(ago(14 * 86400000)).toBe("2w ago");
  });

  it("does not print a fake time for a missing or invalid timestamp", () => {
    expect(relTime(null)).toBe("—");
    expect(relTime("not a date")).toBe("—");
  });

  it("formats bytes and refuses to call zero a size", () => {
    expect(kb(145408)).toBe("142.0 KB");
    expect(kb(0)).toBe("—");
    expect(kb(undefined)).toBe("—");
  });
});

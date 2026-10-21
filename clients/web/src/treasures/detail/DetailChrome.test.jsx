/**
 * Detail chrome contracts.
 *
 * The refactor moved real capability decisions into markup — which actions exist,
 * which are refused, and what the page claims it can do. Those are the parts worth
 * pinning: a later edit that re-enables Edit during a history preview, or adds a
 * Restore button the API cannot honour, should fail here.
 */
import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import React from "react";

import CmsPanel from "./CmsPanel.jsx";
import DetailHeader from "./DetailHeader.jsx";
import { VersionList } from "./VersionHistory.jsx";

const DETAIL = {
  id: "abc123",
  title: "Master Data Access",
  slug: "22-master-data-access",
  kind: "design-doc",
  language: "vi",
  version: 7,
  status: "draft",
  source_format: "markdown",
  render_bytes: 145408,
  updated_at: new Date(Date.now() - 3 * 3600 * 1000).toISOString(),
  origin_kind: "doc_file",
  origin_path: "/home/u/docs/22-master-data-access.md",
  source_path: "/store/abc/v7/source.md",
  artifact_path: "/store/abc/v7/artifact.html",
  tags: ["odoo", "subscription"],
  font: "space-grotesk",
};

const header = (props) => renderToStaticMarkup(
  <DetailHeader detail={DETAIL} tab="preview" onTab={() => {}} published={false}
    viewingVersion={null} onBack={() => {}} panelOpen onTogglePanel={() => {}}
    staleClick={() => {}} action={null} {...props} />);

const panel = (props) => renderToStaticMarkup(
  <CmsPanel detail={DETAIL} id="abc123" open openSection={null}
    onOpen={() => {}} onOpenSection={() => {}} headDraft="" {...props} />);

describe("the header is one bar, and it says what the view can do", () => {
  it("carries the document's identity without a second title block", () => {
    const html = header();
    // The library had this bug: the app shell's Header and the view's own header
    // both said "Treasures". Detail renders inside a Shell with NO shared Header,
    // so this bar is the only title — exactly one h-level element names the doc.
    expect(html.match(/<h2/g) || []).toHaveLength(1);
    expect(html).toContain("Master Data Access");
    expect(html).toContain("v7");
  });

  it("refuses Edit while a historical version is on screen", () => {
    const html = header({ viewingVersion: 3, tab: "preview" });
    // The editor holds the CURRENT source. Editing from a v3 preview would let a
    // save write v3's bytes over v7 with nothing on screen saying so.
    expect(html).toMatch(/Edit<\/button>/);
    const editBtn = html.slice(html.indexOf(">Edit<") - 400, html.indexOf(">Edit<"));
    expect(editBtn).toContain("disabled");
    expect(html).toContain("viewing v3");
  });

  it("refuses Edit on a published artifact, and says why", () => {
    const html = header({ published: true });
    expect(html).toContain("claude.ai has no update API");
  });

  it("shows the source-changed control only when the server says stale", () => {
    expect(header()).not.toContain("Source changed");
    expect(header({ detail: { ...DETAIL, origin_stale: { stale: true, refreshable: true } } }))
      .toContain("Source changed");
  });
});

describe("the panel never claims a capability the API lacks", () => {
  it("does not offer to publish, because no publish endpoint exists", () => {
    const html = panel({ openSection: "publish" });
    expect(html).toContain("Publishing runs through the MCP tool");
    // Archive is the only write this section has. "Publish" may appear exactly
    // once — as the section's name. A second occurrence would be an action label,
    // implying an endpoint that does not exist.
    // ("Publishing…" in the note is excluded — it is the sentence that says the
    // dashboard cannot do it.)
    expect(html.match(/Publish(?!ing)/g)).toHaveLength(1);
    expect(html).toContain("Archive");
  });

  it("does not offer to restore a version, because no restore endpoint exists", () => {
    const rows = [
      { version: 2, render_bytes: 2048, written_at: null, has_artifact: true, is_current: true },
      { version: 1, render_bytes: 1024, written_at: null, has_artifact: true, is_current: false },
    ];
    const html = renderToStaticMarkup(
      <VersionList rows={rows} currentVersion={2} selected={null} onSelect={() => {}} />);
    // "Restore to draft" (un-archive) is a real PATCH and lives in Publish; what
    // must not appear anywhere is version rollback, which nothing implements.
    expect(html).not.toMatch(/restore v\d|roll ?back|revert to v/i);
    expect(html).toContain("no restore");
    expect(html).toContain("current");
  });

  it("disables a version whose render is gone instead of hiding it", () => {
    const rows = [
      { version: 2, render_bytes: 2048, written_at: null, has_artifact: true, is_current: true },
      { version: 1, render_bytes: null, written_at: null, has_artifact: false, is_current: false },
    ];
    const html = renderToStaticMarkup(
      <VersionList rows={rows} currentVersion={2} selected={null} onSelect={() => {}} />);
    expect(html).toContain("v1");
    expect(html).toContain("disabled");
    expect(html).toContain("only its source is on disk");
  });

  it("offers archive as the reversible alternative to delete", () => {
    expect(panel({ openSection: "publish" })).toContain("Archive");
    const danger = panel({ openSection: "danger" });
    expect(danger).toContain("Delete permanently");
    // Unarmed, the destructive label must not be the confirming one.
    expect(danger).not.toContain("Confirm permanent delete");
  });

  it("arms delete before confirming, and states the blast radius", () => {
    const html = panel({ openSection: "danger", deleteArmed: true });
    expect(html).toContain("Confirm permanent delete");
    expect(html).toContain("Removes every version&#x27;s files and the index row");
  });

  it("only offers the fonts the renderer accepts", () => {
    const html = panel({ openSection: "appearance" });
    for (const value of ["space-grotesk", "jetbrains-mono", "default"]) {
      expect(html).toContain(`value="${value}"`);
    }
    // render.FONTS is the whole list; anything else is a 400 from the backend.
    expect(html.match(/<option value="/g)).toHaveLength(3);
  });
});

describe("collapsing the panel keeps it discoverable", () => {
  it("renders a rail of section buttons rather than nothing", () => {
    const html = panel({ open: false });
    // Hiding it entirely would make eight sections' worth of controls vanish with
    // no trace of where they went.
    for (const title of ["Publish", "Source", "Version history", "Danger zone"]) {
      expect(html).toContain(title);
    }
    expect(html).not.toContain("Delete permanently");
  });
});

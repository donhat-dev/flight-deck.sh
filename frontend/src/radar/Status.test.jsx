/**
 * The four things a data-driven page can be, and why they are four.
 *
 * This file exists because of a real failure. The page opened on a hardcoded slug and
 * the states were loading / error / one empty. When that radar was deleted the install
 * had zero radars — a situation with no state of its own — so it fell through to ERROR
 * and the page read "Could not load the radar. no radar 'subscription-migration'".
 * Nothing had failed. The reader was told to retry something that would never succeed.
 *
 * Loading, failure, no-radars and no-blips look similar and mean different things, and
 * only one of them is worth a retry button. That is the whole content of these tests.
 */
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { Status } from "./RadarPage.jsx";

const html = (props) => renderToStaticMarkup(<Status {...props} />);

describe("the states are distinguishable", () => {
  it("says it is loading, and offers nothing to press", () => {
    const out = html({ loading: true });
    expect(out).toContain("Loading");
    expect(out).not.toContain("<button");
  });

  it("marks a failed request as a failure and offers a retry", () => {
    const out = html({ error: new Error("connection refused"), onRetry: () => {} });
    expect(out).toContain('data-tone="error"');
    expect(out).toContain("connection refused");
    expect(out).toContain("Try again");
  });

  it("treats an install with no radars as empty, NOT as an error", () => {
    // The regression. `noRadars` used to have no branch, so it reached the error case.
    const out = html({ noRadars: true });
    expect(out).toContain("No radars yet");
    expect(out).not.toContain('data-tone="error"');
    // No retry: re-fetching cannot conjure a radar. It tells the reader what would.
    expect(out).not.toContain("Try again");
    expect(out).toContain("radar_create");
  });

  it("distinguishes no radars from a radar with no blips", () => {
    const none = html({ noRadars: true });
    const bare = html({ empty: true });
    expect(bare).toContain("no blips yet");
    expect(none).not.toBe(bare);
    expect(bare).not.toContain('data-tone="error"');
  });

  it("shows loading before anything else when several are true", () => {
    // A slow request that will fail is still loading; showing the previous failure
    // beside a spinner tells the reader two contradictory things at once.
    const out = html({ loading: true, error: new Error("stale"), noRadars: true });
    expect(out).toContain("Loading");
    expect(out).not.toContain("stale");
    expect(out).not.toContain("No radars yet");
  });

  it("renders nothing when there is nothing to say", () => {
    // The caller mounts <Status> unconditionally in some branches, so "all clear" has
    // to be empty output rather than a stray container with padding.
    expect(html({})).toBe("");
  });
});

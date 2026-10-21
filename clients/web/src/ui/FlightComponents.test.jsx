import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import {
  Button,
  CheckField,
  DataList,
  ProgressBar,
  SelectField,
  Tabs,
  TextAreaField,
  Toggle,
} from "./FlightComponents.jsx";

describe("FlightDeck component contracts", () => {
  it("keeps async button state explicit and non-repeatable", () => {
    const html = renderToStaticMarkup(<Button loading>Run check</Button>);

    expect(html).toContain('data-state="loading"');
    expect(html).toContain('aria-busy="true"');
    expect(html).toContain("disabled");
    expect(html).toContain("Working");
  });

  it("connects expanded fields to their guidance and errors", () => {
    const selectHtml = renderToStaticMarkup(
      <SelectField
        id="environment"
        label="Environment"
        defaultValue="local"
        options={[{ label: "Local", value: "local" }]}
        error="Environment is locked."
      />,
    );
    const textareaHtml = renderToStaticMarkup(
      <TextAreaField id="note" label="Release note" hint="Describe the recovery path." defaultValue="No data loss." />,
    );

    expect(selectHtml).toContain('aria-invalid="true"');
    expect(selectHtml).toContain('aria-describedby="environment-description"');
    expect(selectHtml).toContain('role="alert"');
    expect(textareaHtml).toContain("<textarea");
    expect(textareaHtml).toContain('aria-describedby="note-description"');
  });

  it("exposes boolean controls with native checked semantics", () => {
    const toggleHtml = renderToStaticMarkup(
      <Toggle label="Autosave" checked onChange={() => {}} />,
    );
    const checkHtml = renderToStaticMarkup(
      <CheckField label="Retain logs" checked onChange={() => {}} />,
    );

    expect(toggleHtml).toContain('role="switch"');
    expect(toggleHtml).toContain('aria-checked="true"');
    expect(checkHtml).toContain('type="checkbox"');
    expect(checkHtml).toContain("checked");
  });

  it("renders tabs, progress, and data as named semantic structures", () => {
    const tabsHtml = renderToStaticMarkup(
      <Tabs
        label="Mission views"
        value="overview"
        onChange={() => {}}
        items={[
          { label: "Overview", value: "overview", content: <p>Nominal</p> },
          { label: "Timeline", value: "timeline", content: <p>12 events</p> },
        ]}
      />,
    );
    const progressHtml = renderToStaticMarkup(<ProgressBar label="Validation" value={68} />);
    const dataHtml = renderToStaticMarkup(
      <DataList label="Runtime" items={[{ label: "Backend", value: "8010", detail: "local" }]} />,
    );

    expect(tabsHtml).toContain('role="tablist"');
    expect(tabsHtml).toContain('aria-selected="true"');
    expect(tabsHtml).toContain('role="tabpanel"');
    expect(progressHtml).toContain('aria-label="Validation"');
    expect(progressHtml).toContain("<progress");
    expect(dataHtml).toContain("<dl");
    expect(dataHtml).toContain('aria-label="Runtime"');
  });
});

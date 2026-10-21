import React, { useCallback, useEffect, useState } from "react";

import { get } from "../api.js";
import { Tabs } from "../ui/FlightComponents.jsx";
import LinksPanel from "./LinksPanel.jsx";
import ProblemsPanel from "./ProblemsPanel.jsx";
import SearchPanel from "./SearchPanel.jsx";

/* ---- Memory: the auto-memory store, read four ways ---------------------- */
// Data: GET /api/memory/{lint,search,graph,history} — four read-only views over
// ~/.claude/projects/<project>/memory, the store the harness writes and injects
// one line of into every session. Nothing on this tab writes to it.
//
// One page with three tabs rather than three sidebar entries: Problems, Search
// and Links are three questions about ONE store, and the header that says which
// store and how big it is answers all three. Splitting them would repeat that
// header three times and make "the store I was just looking at" a navigation
// step. The tabs are the kit's, so the arrow keys work without extra code.
//
// Both payloads load once, on open. `lint` is the slow one (it scans the
// transcripts to count reads) and it still comes back well under a second on a
// store this size; `graph` is instant, and Problems joins its timestamps in, so
// fetching it lazily would only buy a table with an empty column.

export default function MemoryView() {
  const [lint, setLint] = useState(null);
  const [graph, setGraph] = useState(null);
  const [error, setError] = useState(null);
  const [tab, setTab] = useState("problems");

  const [draft, setDraft] = useState("");
  const [query, setQuery] = useState("");
  const [result, setResult] = useState(null);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState(null);

  const load = useCallback(() => {
    setError(null);
    Promise.all([get("/api/memory/lint"), get("/api/memory/graph")])
      .then(([l, g]) => { setLint(l); setGraph(g); })
      .catch(setError);
  }, []);

  useEffect(load, [load]);

  const search = useCallback((text) => {
    const term = (text || "").trim();
    setQuery(term);
    if (!term) { setResult(null); setSearchError(null); return; }
    setSearching(true);
    setSearchError(null);
    get(`/api/memory/search?query=${encodeURIComponent(term)}&limit=25`)
      .then(setResult)
      .catch((e) => { setResult(null); setSearchError(e); })
      .finally(() => setSearching(false));
  }, []);

  const problems = lint?.findings?.length;

  return (
    <Tabs
      label="What to read about the memory store"
      value={tab}
      onChange={setTab}
      items={[
        {
          value: "problems",
          label: "Problems",
          meta: problems ? String(problems) : undefined,
          content: (
            <ProblemsPanel lint={lint} graph={graph} error={error} onRetry={load} />
          ),
        },
        {
          value: "search",
          label: "Search",
          content: (
            <SearchPanel
              draft={draft}
              onDraft={setDraft}
              query={query}
              onSearch={search}
              result={result}
              busy={searching}
              error={searchError}
            />
          ),
        },
        {
          value: "links",
          label: "Links",
          meta: graph ? String((graph.edges || []).length) : undefined,
          content: <LinksPanel graph={graph} error={error} onRetry={load} />,
        },
      ]}
    />
  );
}

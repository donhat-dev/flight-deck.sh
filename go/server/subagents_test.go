package server

import (
	"encoding/json"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"
)

// /api/sessions attaches the subagent breakdown read from the transcript
// files under <projects>/<session>/subagents/, priced like the ledger rows.
func TestSessionsRowsCarrySubagents(t *testing.T) {
	s, projects := newServer(t)
	appendTo(t, filepath.Join(projects, "-p", "s1.jsonl"), line("u1", "m1"))
	appendTo(t, filepath.Join(projects, "-p", "s1", "subagents", "agent-exp.jsonl"),
		`{"type":"user","uuid":"d1","sessionId":"s1","timestamp":"2026-09-25T06:59:00.000Z",`+
			`"attributionAgent":"Explore","message":{"role":"user","content":"survey the repo"}}`+"\n"+
			line("su1", "m-sub"))
	appendTo(t, filepath.Join(projects, "-p", "s2.jsonl"), lineIn("v1", "m2", "s2"))
	s.SweepNow()

	rec := httptest.NewRecorder()
	s.Handler().ServeHTTP(rec, httptest.NewRequest("GET", "/api/sessions", nil))
	var rows []map[string]any
	if rec.Code != 200 || json.Unmarshal(rec.Body.Bytes(), &rows) != nil {
		t.Fatalf("%d %s", rec.Code, rec.Body)
	}
	byID := map[string]map[string]any{}
	for _, r := range rows {
		byID[r["session_id"].(string)] = r
	}
	subs, ok := byID["s1"]["subagents"].([]any)
	if !ok || len(subs) != 1 {
		t.Fatalf("s1 subagents %v", byID["s1"]["subagents"])
	}
	sub := subs[0].(map[string]any)
	if sub["agent_id"] != "exp" || sub["agent_type"] != "Explore" ||
		sub["model"] != "claude-opus-5-5" || sub["dispatch"] != "survey the repo" ||
		sub["turns"] != 1.0 || sub["input_raw"] != 1.0 || sub["output"] != 2.0 {
		t.Fatalf("subagent row %v", sub)
	}
	// claude-opus-5-5: 1*$4 + 2*$20 per Mtok.
	if sub["cost"] != 0.000044 {
		t.Fatalf("cost %v", sub["cost"])
	}
	// The key is always present, possibly an empty list.
	if got := byID["s2"]["subagents"]; got == nil || len(got.([]any)) != 0 {
		t.Fatalf("s2 subagents %v", got)
	}
	if !strings.Contains(rec.Body.String(), `"subagents":[]`) {
		t.Fatalf("empty list did not marshal as []: %s", rec.Body)
	}
}

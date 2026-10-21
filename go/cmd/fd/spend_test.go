package main

import (
	"math"
	"os"
	"path/filepath"
	"testing"
)

// writeProjects holds one response written twice (two content blocks): one billed row.
func writeProjects(t *testing.T) (db, projects string) {
	t.Helper()
	dir := t.TempDir()
	projects = filepath.Join(dir, "projects")
	p := filepath.Join(projects, "-home-p", "s1.jsonl")
	if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
		t.Fatal(err)
	}
	line := `{"type":"assistant","uuid":"u1","sessionId":"s1","cwd":"/p","timestamp":"2026-09-25T07:00:00.000Z",` +
		`"requestId":"r1","message":{"id":"m1","model":"claude-opus-5-5","usage":{"input_tokens":1000,` +
		`"cache_read_input_tokens":1000000,"output_tokens":1000}}}` + "\n"
	if err := os.WriteFile(p, []byte(line+line), 0o644); err != nil {
		t.Fatal(err)
	}
	return filepath.Join(dir, "fd", "ledger.db"), projects
}

func TestSpendIngestsThenAnswers(t *testing.T) {
	db, projects := writeProjects(t)
	code, out, errOut := runFd("spend", "--json", "--db", db, "--projects", projects)
	if code != 0 {
		t.Fatalf("exit %d: %s", code, errOut)
	}
	var v struct {
		Range   string `json:"range"`
		Summary struct {
			TotalCost    float64 `json:"total_cost"`
			MessageCount int     `json:"message_count"`
		} `json:"summary"`
		ByModel []map[string]any `json:"by_model"`
		Daily   []map[string]any `json:"daily"`
	}
	oneDocument(t, out, &v)
	if v.Range != "all" || v.Summary.MessageCount != 1 || math.Abs(v.Summary.TotalCost-0.224) > 1e-9 ||
		len(v.ByModel) != 1 || len(v.Daily) != 1 {
		t.Fatalf("%+v", v)
	}
}

func TestSpendRefusesAnUnknownRange(t *testing.T) {
	db, projects := writeProjects(t)
	code, out, _ := runFd("spend", "--json", "--range", "week", "--db", db, "--projects", projects)
	var v map[string]string
	oneDocument(t, out, &v)
	if code != 2 || v["error"] == "" {
		t.Fatalf("exit %d %v", code, v)
	}
}

func TestSpendRefusesAMissingProjectsDirectory(t *testing.T) {
	code, out, _ := runFd("spend", "--json", "--db", filepath.Join(t.TempDir(), "l.db"), "--projects", "/no/such/dir")
	var v map[string]string
	oneDocument(t, out, &v)
	if code != 2 || v["error"] == "" {
		t.Fatalf("exit %d %v", code, v)
	}
}

func TestIngestCountsNewRowsOnce(t *testing.T) {
	db, projects := writeProjects(t)
	for i, want := range []float64{1, 0} {
		code, out, errOut := runFd("ingest", "--json", "--db", db, "--projects", projects)
		if code != 0 {
			t.Fatalf("run %d exit %d: %s", i, code, errOut)
		}
		var v map[string]any
		oneDocument(t, out, &v)
		if v["new_rows"] != want {
			t.Fatalf("run %d: %v", i, v)
		}
	}
}

func TestSessionsListsWithoutIngestWhenAsked(t *testing.T) {
	db, projects := writeProjects(t)
	code, out, _ := runFd("sessions", "--json", "--no-ingest", "--db", db, "--projects", projects)
	var empty []map[string]any
	oneDocument(t, out, &empty)
	if code != 0 || empty == nil || len(empty) != 0 {
		t.Fatalf("exit %d %v", code, empty)
	}
	code, out, _ = runFd("sessions", "--json", "--limit", "1", "--db", db, "--projects", projects)
	var rows []map[string]any
	oneDocument(t, out, &rows)
	if code != 0 || len(rows) != 1 || rows[0]["session_id"] != "s1" {
		t.Fatalf("exit %d %v", code, rows)
	}
}

package server

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"fdcore/internal/ledger"
)

func line(uuid, msg string) string {
	return fmt.Sprintf(`{"type":"assistant","uuid":%q,"sessionId":"s1","cwd":"/p",`+
		`"timestamp":"2026-09-25T07:00:00.000Z","requestId":"r","message":{"id":%q,`+
		`"model":"claude-opus-5-5","usage":{"input_tokens":1,"output_tokens":2}}}`+"\n", uuid, msg)
}

func newServer(t *testing.T) (*Server, string) {
	t.Helper()
	dir := t.TempDir()
	projects := filepath.Join(dir, "projects")
	if err := os.MkdirAll(filepath.Join(projects, "-p"), 0o755); err != nil {
		t.Fatal(err)
	}
	st, err := ledger.Open(filepath.Join(dir, "ledger.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { st.Close() })
	return &Server{Store: st, Projects: projects, Version: "test", Now: time.Now}, projects
}

func get(t *testing.T, s *Server, path string) *httptest.ResponseRecorder {
	t.Helper()
	rec := httptest.NewRecorder()
	s.Handler().ServeHTTP(rec, httptest.NewRequest("GET", path, nil))
	return rec
}

func appendTo(t *testing.T, path, text string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	f, err := os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	if _, err := f.WriteString(text); err != nil {
		t.Fatal(err)
	}
}

func waitRows(t *testing.T, s *Server, want int) {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for {
		var n int
		s.Store.DB.QueryRow(`SELECT count(*) FROM responses`).Scan(&n)
		if n == want {
			return
		}
		if time.Now().After(deadline) {
			t.Fatalf("rows %d after 5s, want %d", n, want)
		}
		time.Sleep(20 * time.Millisecond)
	}
}

func watch(t *testing.T, s *Server) {
	t.Helper()
	s.Ready = make(chan struct{})
	ctx, cancel := context.WithCancel(t.Context())
	t.Cleanup(cancel)
	go s.Watch(ctx, 50*time.Millisecond, time.Hour)
	<-s.Ready
}

func TestSummaryHasEveryFastAPIKey(t *testing.T) {
	s, projects := newServer(t)
	appendTo(t, filepath.Join(projects, "-p", "s1.jsonl"), line("u1", "m1"))
	s.SweepNow()
	rec := get(t, s, "/api/summary?range=all")
	var m map[string]any
	if rec.Code != 200 || json.Unmarshal(rec.Body.Bytes(), &m) != nil {
		t.Fatalf("%d %s", rec.Code, rec.Body)
	}
	for _, k := range []string{"total_cost", "cache_savings", "saved_vs_subscription", "rtk_savings",
		"cache_hit_rate", "input_tokens", "input_tokens_raw", "output_tokens", "session_count",
		"unknown_model_tokens", "message_count", "total_context_tokens", "avg_context_per_turn"} {
		if _, ok := m[k]; !ok {
			t.Errorf("missing %s", k)
		}
	}
	if m["message_count"] != 1.0 {
		t.Errorf("message_count %v", m["message_count"])
	}
}

func TestEmptyListsAreArrays(t *testing.T) {
	s, _ := newServer(t)
	for _, p := range []string{"/api/daily", "/api/by-model", "/api/sessions?limit=5"} {
		rec := get(t, s, p)
		if rec.Code != 200 || strings.TrimSpace(rec.Body.String()) != "[]" {
			t.Errorf("%s: %d %q", p, rec.Code, rec.Body)
		}
	}
}

func TestOtherAPIPathsAre404WithAnError(t *testing.T) {
	s, _ := newServer(t)
	rec := get(t, s, "/api/burn")
	var m map[string]string
	if rec.Code != 404 || json.Unmarshal(rec.Body.Bytes(), &m) != nil || m["error"] == "" {
		t.Fatalf("%d %s", rec.Code, rec.Body)
	}
}

func TestAnAppendedLineIsReadWithinSeconds(t *testing.T) {
	s, projects := newServer(t)
	f := filepath.Join(projects, "-p", "s1.jsonl")
	appendTo(t, f, line("u1", "m1"))
	s.SweepNow()
	watch(t, s)
	appendTo(t, f, line("u2", "m2"))
	waitRows(t, s, 2)
}

func TestANewDirectoryIsWatched(t *testing.T) {
	s, projects := newServer(t)
	watch(t, s)
	appendTo(t, filepath.Join(projects, "-p", "s1", "subagents", "agent-a.jsonl"), line("u1", "m1"))
	waitRows(t, s, 1)
	appendTo(t, filepath.Join(projects, "-p", "s1", "subagents", "agent-a.jsonl"), line("u2", "m2"))
	waitRows(t, s, 2)
}

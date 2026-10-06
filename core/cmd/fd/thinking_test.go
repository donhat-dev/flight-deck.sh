package main

import (
	"bytes"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// One response that thought, written an hour ago, after a tool result.
func writeThinking(t *testing.T) (db, projects string) {
	t.Helper()
	dir := t.TempDir()
	projects = filepath.Join(dir, "projects")
	p := filepath.Join(projects, "-home-p", "s1.jsonl")
	if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
		t.Fatal(err)
	}
	at := time.Now().UTC().Add(-time.Hour)
	ts := func(d time.Duration) string { return at.Add(d).Format("2006-01-02T15:04:05.000Z") }
	body := fmt.Sprintf(`{"type":"user","uuid":"u0","sessionId":"s1","timestamp":%q,"message":{"role":"user","content":"go"}}`+"\n", ts(0)) +
		fmt.Sprintf(`{"type":"assistant","uuid":"u1","sessionId":"s1","timestamp":%q,"requestId":"r1",`+
			`"thinkingDurationMs":2500,"perTurnEffort":"high","message":{"id":"m1","model":"claude-opus-5-5",`+
			`"content":[{"type":"thinking","thinking":""}],"usage":{"input_tokens":1,"output_tokens":300,`+
			`"output_tokens_details":{"thinking_tokens":250}}}}`+"\n", ts(3*time.Second))
	if err := os.WriteFile(p, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	return filepath.Join(dir, "fd", "ledger.db"), projects
}

type thinkingDoc struct {
	Source  string `json:"source"`
	Buckets []struct {
		Key     string `json:"key"`
		Thought int    `json:"thought"`
		Tokens  struct {
			P50 float64 `json:"p50"`
		} `json:"thinking_tokens"`
		Seconds struct {
			P50 float64 `json:"p50"`
		} `json:"thinking_seconds"`
		Wait struct {
			P50 float64 `json:"p50"`
		} `json:"wait_seconds"`
	} `json:"buckets"`
	Warnings []string `json:"warnings"`
	Coverage *struct {
		CompleteFrom string `json:"complete_from"`
		OldestLine   string `json:"oldest_line"`
	} `json:"coverage"`
}

func TestThinkingFromTheLedger(t *testing.T) {
	db, projects := writeThinking(t)
	code, out, errOut := runFd("thinking", "--json", "--db", db, "--projects", projects)
	if code != 0 {
		t.Fatalf("exit %d: %s", code, errOut)
	}
	var v thinkingDoc
	oneDocument(t, out, &v)
	if v.Source != "ledger" || len(v.Buckets) != 1 || v.Buckets[0].Tokens.P50 != 250 ||
		v.Buckets[0].Seconds.P50 != 2.5 || v.Buckets[0].Wait.P50 != 3 || len(v.Warnings) != 0 {
		t.Fatalf("%+v", v)
	}
}

// A direct read says it is slow and how far back the transcripts reach, on
// stderr, and keeps stdout one JSON document with the same answer.
func TestThinkingRawWarnsAndAnswersTheSame(t *testing.T) {
	_, projects := writeThinking(t)
	var warned bytes.Buffer
	warnOut = &warned
	t.Cleanup(func() { warnOut = os.Stderr })
	code, out, errOut := runFd("thinking", "--json", "--raw", "--days", "30", "--projects", projects)
	if code != 0 {
		t.Fatalf("exit %d: %s", code, errOut)
	}
	var v thinkingDoc
	oneDocument(t, out, &v)
	// The only transcript started an hour ago, so that is as far back as a read reaches.
	today := time.Now().UTC().Add(-time.Hour).Format(time.DateOnly)
	if v.Source != "transcripts" || len(v.Buckets) != 1 || v.Buckets[0].Tokens.P50 != 250 ||
		v.Coverage == nil || v.Coverage.CompleteFrom != today {
		t.Fatalf("%+v", v)
	}
	w := warned.String()
	for _, want := range []string{"This is slow", "can only answer from " + today,
		"cleanupPeriodDays", "are missing from this answer"} {
		if !strings.Contains(w, want) {
			t.Fatalf("warnings lack %q:\n%s", want, w)
		}
	}
	if len(v.Warnings) != 2 {
		t.Fatalf("warnings in the document: %v", v.Warnings)
	}
}

func TestThinkingRefusesAnUnknownGrouping(t *testing.T) {
	code, out, _ := runFd("thinking", "--json", "--by", "week")
	if code != 2 || !strings.Contains(out, "--by must be") {
		t.Fatalf("exit %d: %s", code, out)
	}
}

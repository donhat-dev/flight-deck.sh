package ingest

import (
	"database/sql"
	"fmt"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// A tool result, then one response written as a thinking line and a text line.
// Only the thinking line carries thinkingDurationMs; thinking tokens grow as
// the response streams.
func thinkingLines(msg string, at time.Time) []string {
	ts := func(d time.Duration) string { return at.Add(d).UTC().Format("2006-01-02T15:04:05.000Z") }
	usage := func(think int) string {
		return fmt.Sprintf(`"usage":{"input_tokens":2,"output_tokens":%d,`+
			`"output_tokens_details":{"thinking_tokens":%d}}`, think+10, think)
	}
	return []string{
		fmt.Sprintf(`{"type":"user","uuid":"u-%s","sessionId":"s1","timestamp":%q,"message":{"role":"user",`+
			`"content":[{"type":"tool_result","tool_use_id":"t1","content":"ok"}]}}`+"\n", msg, ts(0)),
		fmt.Sprintf(`{"type":"assistant","uuid":"a-%s","sessionId":"s1","timestamp":%q,"requestId":"r-%s",`+
			`"thinkingDurationMs":1500,"perTurnEffort":"high","effort":"low","message":{"id":%q,`+
			`"model":"claude-opus-5-5","content":[{"type":"thinking","thinking":"","signature":"x"}],%s}}`+"\n",
			msg, ts(4*time.Second), msg, msg, usage(100)),
		fmt.Sprintf(`{"type":"assistant","uuid":"b-%s","sessionId":"s1","timestamp":%q,"requestId":"r-%s",`+
			`"message":{"id":%q,"model":"claude-opus-5-5","content":[{"type":"text","text":"done"}],%s}}`+"\n",
			msg, ts(6*time.Second), msg, msg, usage(120)),
	}
}

func TestTheThinkingFiguresOfAResponseAreStored(t *testing.T) {
	s, in, root := setup(t)
	path := filepath.Join(root, "-p", "s1.jsonl")
	write(t, path, thinkingLines("m1", time.Date(2026, 10, 6, 2, 0, 0, 0, time.UTC))...)
	if n := mustFile(t, in, path); n != 1 {
		t.Fatalf("%d rows", n)
	}
	var tokens, ms, wait int64
	var effort string
	if err := s.DB.QueryRow(`SELECT thinking_tokens, thinking_ms, think_wait_ms, effort FROM responses`).
		Scan(&tokens, &ms, &wait, &effort); err != nil {
		t.Fatal(err)
	}
	if tokens != 120 || ms != 1500 || wait != 4000 || effort != "high" {
		t.Fatalf("tokens %d, ms %d, wait %d, effort %q", tokens, ms, wait, effort)
	}
}

func TestASubagentResponseIsMarked(t *testing.T) {
	s, in, root := setup(t)
	path := filepath.Join(root, "-p", "s1", "subagents", "agent-a1.jsonl")
	line := thinkingLines("m1", time.Date(2026, 10, 6, 2, 0, 0, 0, time.UTC))[1]
	write(t, path, strings.Replace(line, `"type":"assistant",`, `"type":"assistant","isSidechain":true,`, 1))
	mustFile(t, in, path)
	var side int
	if err := s.DB.QueryRow(`SELECT sidechain FROM responses`).Scan(&side); err != nil || side != 1 {
		t.Fatalf("sidechain %d, %v", side, err)
	}
}

// A read that starts mid-file does not know when the first response's request
// left, so it leaves the wait empty rather than measure it from nothing.
func TestAReadThatStartsMidResponseLeavesTheWaitEmpty(t *testing.T) {
	s, in, root := setup(t)
	path := filepath.Join(root, "-p", "s1.jsonl")
	lines := thinkingLines("m1", time.Date(2026, 10, 6, 2, 0, 0, 0, time.UTC))
	write(t, path, lines[0])
	mustFile(t, in, path)
	write(t, path, lines[1:]...)
	mustFile(t, in, path)
	var wait sql.NullInt64
	if err := s.DB.QueryRow(`SELECT think_wait_ms FROM responses`).Scan(&wait); err != nil {
		t.Fatal(err)
	}
	if wait.Valid {
		t.Fatalf("wait %d measured without a start", wait.Int64)
	}
}

func TestReadRawMergesAsTheLedgerDoes(t *testing.T) {
	_, _, root := setup(t)
	day := time.Date(2026, 10, 6, 2, 0, 0, 0, time.UTC)
	write(t, filepath.Join(root, "-p", "s1.jsonl"), thinkingLines("m1", day)...)
	write(t, filepath.Join(root, "-p", "s0.jsonl"), thinkingLines("m0", day.AddDate(0, 0, -3))...)
	files, err := List(root)
	if err != nil {
		t.Fatal(err)
	}
	if got := Oldest(files); got != "2026-10-03T02:00:00.000Z" {
		t.Fatalf("oldest %q", got)
	}
	resps, err := ReadRaw(files, time.Unix(0, 0), "2026-10-05")
	if err != nil {
		t.Fatal(err)
	}
	if len(resps) != 1 {
		t.Fatalf("%d responses, want only the one on or after the 5th", len(resps))
	}
	r := resps[0]
	if *r.ThinkingTokens != 120 || *r.ThinkingMS != 1500 || *r.ThinkWaitMS != 4000 || r.Effort != "high" {
		t.Fatalf("%+v", r)
	}
}

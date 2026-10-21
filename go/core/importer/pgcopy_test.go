package importer

import (
	"errors"
	"path/filepath"
	"strings"
	"testing"

	"flightdeck/core/ledger"
)

const header = "COPY public.messages (uuid, session_id, project, model, ts, input_tokens, cache_read, " +
	"cache_create_5m, cache_create_1h, output_tokens, service_tier, message_id, request_id, speed, " +
	"inference_geo, dup_of, dedupe_method, prompt_id, agent_id) FROM stdin;"

// row builds one COPY line; \N is NULL.
func row(uuid, session, model, input, cacheRead, out, msgID, reqID, dupOf, method string) string {
	return strings.Join([]string{uuid, session, `/p`, model, "2026-08-01T00:00:00.000Z", input, cacheRead,
		"0", "0", out, "standard", msgID, reqID, `\N`, `\N`, dupOf, method, `\N`, `\N`}, "\t")
}

func dump(rows ...string) string {
	return "--\n-- PostgreSQL database dump\n--\nSET statement_timeout = 0;\n\n" +
		header + "\n" + strings.Join(rows, "\n") + "\n\\.\n\n"
}

// ledgerWithTranscript holds one transcript response M9 in session s1.
func ledgerWithTranscript(t *testing.T) *ledger.Store {
	t.Helper()
	s, err := ledger.Open(filepath.Join(t.TempDir(), "ledger.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { s.Close() })
	tx, _ := s.DB.Begin()
	if _, err := ledger.Upsert(tx, ledger.Response{MessageID: "M9", RequestID: "R9", UUID: "t9", SessionID: "s1",
		Model: "claude-opus-5-5", TS: "2026-09-30T00:00:00.000Z", Input: 5, CacheRead: 20000, Output: 1}); err != nil {
		t.Fatal(err)
	}
	tx.Commit()
	return s
}

var sample = dump(
	row("a1", "gone", "claude-opus-5-5", "1", "2", "3", "M1", "R1", `\N`, "exact"),      // inserted
	row("a2", "gone", "claude-opus-5-5", "1", "2", "3", "M1", "R1", "a1", "exact"),      // a repeat in the source
	row("c1", "s1", "claude-opus-5-5", "5", "20000", "1", "M9", "R9", `\N`, "exact"),    // the transcript has it
	row("d1", "gone2", "claude-opus-5-5", "7", "8", "9", `\N`, `\N`, `\N`, "heuristic"), // inserted as uuid:d1
	row("e1", "s1", "claude-opus-5-5", "7", "8", "9", `\N`, `\N`, `\N`, "heuristic"),    // session on disk
	row("f1", "gone3", "claude-opus-5-5", "5", "20000", "1", `\N`, `\N`, `\N`, `\N`),    // inserted, overlaps M9
)

func count(t *testing.T, s *ledger.Store, where string) int {
	t.Helper()
	var n int
	if err := s.DB.QueryRow(`SELECT count(*) FROM responses WHERE ` + where).Scan(&n); err != nil {
		t.Fatal(err)
	}
	return n
}

func TestImportKeepsTranscriptRowsAndReportsOverlap(t *testing.T) {
	s := ledgerWithTranscript(t)
	st, err := PGCopy(s, strings.NewReader(sample), false)
	if err != nil {
		t.Fatal(err)
	}
	if st.Read != 6 || st.Billed != 5 || st.Inserted != 3 || st.KeyPresent != 1 || st.SessionOnDisk != 1 ||
		st.OverlapRows != 1 {
		t.Fatalf("%+v", st)
	}
	if count(t, s, "source='import'") != 3 || count(t, s, "message_id='uuid:d1' AND dedupe_method='heuristic'") != 1 {
		t.Fatal("imported rows are not marked")
	}
	var sid string
	s.DB.QueryRow(`SELECT session_id FROM responses WHERE message_id='M9'`).Scan(&sid)
	if sid != "s1" || count(t, s, "message_id='M9' AND source='transcript'") != 1 {
		t.Fatal("the transcript row was replaced")
	}
}

func TestImportingTwiceAddsNothing(t *testing.T) {
	s := ledgerWithTranscript(t)
	if _, err := PGCopy(s, strings.NewReader(sample), false); err != nil {
		t.Fatal(err)
	}
	st, err := PGCopy(s, strings.NewReader(sample), false)
	if err != nil {
		t.Fatal(err)
	}
	if st.Inserted != 0 || st.KeyPresent != 4 || count(t, s, "1=1") != 4 {
		t.Fatalf("%+v, rows %d", st, count(t, s, "1=1"))
	}
}

func TestADryRunChangesNothing(t *testing.T) {
	s := ledgerWithTranscript(t)
	st, err := PGCopy(s, strings.NewReader(sample), true)
	if err != nil {
		t.Fatal(err)
	}
	if st.Inserted != 3 || count(t, s, "1=1") != 1 {
		t.Fatalf("%+v, rows %d", st, count(t, s, "1=1"))
	}
}

func TestInputWithoutACopyHeaderIsRefused(t *testing.T) {
	s := ledgerWithTranscript(t)
	if _, err := PGCopy(s, strings.NewReader("SET x = 1;\n"), false); !errors.Is(err, ErrNoCopyHeader) {
		t.Fatalf("err %v", err)
	}
}

func TestCopyEscapes(t *testing.T) {
	if unescape(`\N`) != nil {
		t.Fatal(`\N is not NULL`)
	}
	if got := *unescape(`a\tb\\c\nd`); got != "a\tb\\c\nd" {
		t.Fatalf("%q", got)
	}
}

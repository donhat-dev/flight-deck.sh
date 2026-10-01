package ingest

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"fdcore/internal/ledger"
)

func asst(uuid, session, msg string, out int) string {
	return fmt.Sprintf(`{"type":"assistant","uuid":%q,"sessionId":%q,"cwd":"/p",`+
		`"timestamp":"2026-09-25T07:00:00.000Z","requestId":"req-%s","message":{"id":%q,`+
		`"model":"claude-opus-5-5","usage":{"input_tokens":2,"cache_read_input_tokens":1000,`+
		`"output_tokens":%d,"cache_creation":{"ephemeral_5m_input_tokens":0,`+
		`"ephemeral_1h_input_tokens":0}}}}`+"\n", uuid, session, msg, msg, out)
}

func setup(t *testing.T) (*ledger.Store, *Ingester, string) {
	t.Helper()
	dir := t.TempDir()
	s, err := ledger.Open(filepath.Join(dir, "ledger.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { s.Close() })
	root := filepath.Join(dir, "projects")
	if err := os.MkdirAll(filepath.Join(root, "-p"), 0o755); err != nil {
		t.Fatal(err)
	}
	return s, &Ingester{Store: s}, root
}

func write(t *testing.T, path string, lines ...string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	f, err := os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	for _, l := range lines {
		if _, err := f.WriteString(l); err != nil {
			t.Fatal(err)
		}
	}
}

func rows(t *testing.T, s *ledger.Store) int {
	t.Helper()
	var n int
	if err := s.DB.QueryRow(`SELECT count(*) FROM responses`).Scan(&n); err != nil {
		t.Fatal(err)
	}
	return n
}

func mustFile(t *testing.T, in *Ingester, path string) int {
	t.Helper()
	n, err := in.File(path)
	if err != nil {
		t.Fatal(err)
	}
	return n
}

func mustSweep(t *testing.T, in *Ingester, root string) Stats {
	t.Helper()
	st, err := in.Sweep(root)
	if err != nil {
		t.Fatal(err)
	}
	return st
}

func TestThreeLinesOfOneResponseAreOneRowWithTheLargestOutput(t *testing.T) {
	s, in, root := setup(t)
	f := filepath.Join(root, "-p", "s1.jsonl")
	write(t, f, asst("u1", "s1", "M1", 10), asst("u2", "s1", "M1", 10), asst("u3", "s1", "M1", 300))
	if n := mustFile(t, in, f); n != 1 {
		t.Fatalf("new rows %d", n)
	}
	var out int
	s.DB.QueryRow(`SELECT output_tokens FROM responses`).Scan(&out)
	if rows(t, s) != 1 || out != 300 {
		t.Fatalf("rows %d output %d", rows(t, s), out)
	}
}

func TestOnlyAssistantLinesWithUsageAndAUUIDAreBilled(t *testing.T) {
	s, in, root := setup(t)
	f := filepath.Join(root, "-p", "s1.jsonl")
	write(t, f,
		`{"type":"user","uuid":"x1","sessionId":"s1","message":{"role":"user","content":"hi"}}`+"\n",
		`{"type":"assistant","uuid":"x2","sessionId":"s1","message":{"id":"M2","model":"claude-opus-5-5","usage":{}}}`+"\n",
		`{"type":"assistant","sessionId":"s1","message":{"id":"M3","model":"claude-opus-5-5","usage":{"output_tokens":1}}}`+"\n",
		`{"type":"assistant","uuid":"x4","sessionId":"s1","message":{"model":"claude-opus-4-1","usage":{"output_tokens":1}}}`+"\n")
	mustFile(t, in, f)
	var id string
	s.DB.QueryRow(`SELECT message_id FROM responses`).Scan(&id)
	if rows(t, s) != 1 || id != "uuid:x4" {
		t.Fatalf("rows %d id %q", rows(t, s), id)
	}
}

func TestTitlesAreRecorded(t *testing.T) {
	s, in, root := setup(t)
	f := filepath.Join(root, "-p", "s1.jsonl")
	write(t, f, `{"type":"custom-title","sessionId":"s1","customTitle":"Mine"}`+"\n",
		`{"type":"ai-title","sessionId":"s1","aiTitle":"Generated"}`+"\n")
	mustFile(t, in, f)
	var title string
	s.DB.QueryRow(`SELECT title FROM session_meta WHERE session_id='s1'`).Scan(&title)
	if title != "Mine" {
		t.Fatalf("title %q", title)
	}
}

func TestATrailingLineWithoutNewlineWaitsForTheNextPass(t *testing.T) {
	s, in, root := setup(t)
	f := filepath.Join(root, "-p", "s1.jsonl")
	write(t, f, asst("u1", "s1", "M1", 5), strings.TrimSuffix(asst("u2", "s1", "M2", 5), "\n"))
	mustFile(t, in, f)
	if n := rows(t, s); n != 1 {
		t.Fatalf("rows %d before the newline", n)
	}
	write(t, f, "\n")
	mustFile(t, in, f)
	if n := rows(t, s); n != 2 {
		t.Fatalf("rows %d after the newline", n)
	}
}

func TestANulRunIsSkippedNotFatal(t *testing.T) {
	s, in, root := setup(t)
	f := filepath.Join(root, "-p", "s1.jsonl")
	write(t, f, asst("u1", "s1", "M1", 5), strings.Repeat("\x00", 64)+"\n", asst("u2", "s1", "M2", 5))
	mustFile(t, in, f)
	if n := rows(t, s); n != 2 {
		t.Fatalf("rows %d", n)
	}
}

func TestARewrittenShorterFileIsReadFromTheTop(t *testing.T) {
	s, in, root := setup(t)
	f := filepath.Join(root, "-p", "s1.jsonl")
	write(t, f, asst("u1", "s1", "M1", 5), asst("u2", "s1", "M2", 5), asst("u3", "s1", "M3", 5))
	mustFile(t, in, f)
	short := asst("u4", "s1", "M4", 5)
	if err := os.WriteFile(f, []byte(short), 0o644); err != nil {
		t.Fatal(err)
	}
	mustFile(t, in, f)
	cur, _, _ := ledger.GetCursor(s.DB, f)
	if rows(t, s) != 4 || cur.BytesRead != int64(len(short)) {
		t.Fatalf("rows %d bytes_read %d", rows(t, s), cur.BytesRead)
	}
}

// 2026-09-29: crash recovery emptied the Python ledger while its process cache
// still marked every unchanged file as read. Here the cursor row is the only
// skip state, so an emptied ledger is read again in full.
func TestAWipedLedgerIsReadAgainInFull(t *testing.T) {
	s, in, root := setup(t)
	write(t, filepath.Join(root, "-p", "a.jsonl"), asst("u1", "a", "M1", 5))
	write(t, filepath.Join(root, "-p", "b.jsonl"), asst("u2", "b", "M2", 5))
	mustSweep(t, in, root)
	if _, err := s.DB.Exec(`DELETE FROM responses; DELETE FROM files`); err != nil {
		t.Fatal(err)
	}
	st := mustSweep(t, in, root)
	if st.NewRows != 2 || rows(t, s) != 2 {
		t.Fatalf("new %d rows %d", st.NewRows, rows(t, s))
	}
}

func TestADeletedTranscriptKeepsItsRows(t *testing.T) {
	s, in, root := setup(t)
	f := filepath.Join(root, "-p", "s1.jsonl")
	write(t, f, asst("u1", "s1", "M1", 5))
	mustSweep(t, in, root)
	if err := os.Remove(f); err != nil {
		t.Fatal(err)
	}
	mustSweep(t, in, root)
	if n := rows(t, s); n != 1 {
		t.Fatalf("rows %d", n)
	}
}

func TestALineLargerThanTheReadBufferIsParsed(t *testing.T) {
	s, in, root := setup(t)
	f := filepath.Join(root, "-p", "s1.jsonl")
	big := strings.Repeat("x", 3<<20)
	write(t, f, fmt.Sprintf(`{"type":"user","uuid":"b1","sessionId":"s1","message":{"role":"user","content":%q}}`+"\n", big),
		asst("u1", "s1", "M1", 5))
	mustFile(t, in, f)
	if n := rows(t, s); n != 1 {
		t.Fatalf("rows %d", n)
	}
}

func TestTwoReadersAtOnceStoreEachResponseOnce(t *testing.T) {
	dir := t.TempDir()
	root := filepath.Join(dir, "projects")
	var lines []string
	for i := range 200 {
		lines = append(lines, asst(fmt.Sprintf("u%d", i), "s1", fmt.Sprintf("M%d", i), 5))
	}
	write(t, filepath.Join(root, "-p", "s1.jsonl"), lines...)
	stores := make([]*ledger.Store, 2)
	for i := range stores {
		st, err := ledger.Open(filepath.Join(dir, "ledger.db"))
		if err != nil {
			t.Fatal(err)
		}
		defer st.Close()
		stores[i] = st
	}
	var wg sync.WaitGroup
	errs := make([]error, 2)
	for i := range stores {
		wg.Add(1)
		go func() {
			defer wg.Done()
			_, errs[i] = (&Ingester{Store: stores[i]}).Sweep(root)
		}()
	}
	wg.Wait()
	for _, err := range errs {
		if err != nil {
			t.Fatal(err)
		}
	}
	if n := rows(t, stores[0]); n != 200 {
		t.Fatalf("rows %d", n)
	}
}

// A resumed session copies its parent's responses under new uuids; the parent
// must be read first so it keeps them. The names sort the other way round.
func TestSweepReadsTheOldestFileFirst(t *testing.T) {
	s, in, root := setup(t)
	parent := filepath.Join(root, "-p", "zz-parent.jsonl")
	child := filepath.Join(root, "-p", "aa-child.jsonl")
	write(t, parent, asst("u1", "parent", "M1", 5))
	write(t, child, asst("u9", "child", "M1", 5))
	old := time.Now().Add(-time.Hour)
	if err := os.Chtimes(parent, old, old); err != nil {
		t.Fatal(err)
	}
	mustSweep(t, in, root)
	var sid string
	s.DB.QueryRow(`SELECT session_id FROM responses`).Scan(&sid)
	if sid != "parent" {
		t.Fatalf("billed to %s", sid)
	}
}

func TestSweepReadsSubagentsAndSkipsDotDirectories(t *testing.T) {
	s, in, root := setup(t)
	write(t, filepath.Join(root, "-p", "s1", "subagents", "agent-a.jsonl"), asst("u1", "s1", "M1", 5))
	write(t, filepath.Join(root, ".trash", "x.jsonl"), asst("u2", "s2", "M2", 5))
	st := mustSweep(t, in, root)
	if st.Files != 1 || rows(t, s) != 1 {
		t.Fatalf("files %d rows %d", st.Files, rows(t, s))
	}
}

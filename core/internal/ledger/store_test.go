package ledger

import (
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// A read-only command must open the ledger while another process holds a long
// write transaction (fd import, a cold read): WAL lets readers through.
func TestOpenDoesNotWaitForAWriter(t *testing.T) {
	s, path := open(t)
	tx, err := s.DB.Begin()
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback()
	if _, err := tx.Exec(`INSERT INTO meta (key, value) VALUES ('probe', 'x')`); err != nil {
		t.Fatal(err)
	}
	done := make(chan error, 1)
	go func() {
		s2, err := Open(path)
		if err == nil {
			_, err = Prices(s2.DB)
			s2.Close()
		}
		done <- err
	}()
	select {
	case err := <-done:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(3 * time.Second):
		t.Fatal("Open waited for the writer")
	}
}

// An older fd must not write into a ledger whose layout a newer fd changed.
func TestALedgerFromANewerFdIsRefused(t *testing.T) {
	s, path := open(t)
	if _, err := s.DB.Exec(`UPDATE meta SET value='99' WHERE key='schema_version'`); err != nil {
		t.Fatal(err)
	}
	s.Close()
	if s2, err := Open(path); err == nil || !strings.Contains(err.Error(), "newer") {
		if s2 != nil {
			s2.Close()
		}
		t.Fatalf("opened a schema 99 ledger: %v", err)
	}
}

func open(t *testing.T) (*Store, string) {
	t.Helper()
	path := filepath.Join(t.TempDir(), "fd", "ledger.db")
	s, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { s.Close() })
	return s, path
}

func upsert(t *testing.T, s *Store, r Response) Outcome {
	t.Helper()
	tx, err := s.DB.Begin()
	if err != nil {
		t.Fatal(err)
	}
	o, err := Upsert(tx, r)
	if err != nil {
		t.Fatal(err)
	}
	if err := tx.Commit(); err != nil {
		t.Fatal(err)
	}
	return o
}

func TestOneResponseOverThreeLinesIsOneRowWithTheLargestOutput(t *testing.T) {
	s, _ := open(t)
	base := Response{MessageID: "M1", RequestID: "R1", SessionID: "s1", Model: "claude-opus-5-5",
		TS: "2026-09-25T07:00:00.000Z", Input: 2, CacheRead: 1000, Output: 10}
	l1, l2, l3 := base, base, base
	l1.UUID, l2.UUID, l3.UUID = "u1", "u2", "u3"
	l3.Output = 300
	got := []Outcome{upsert(t, s, l1), upsert(t, s, l2), upsert(t, s, l3)}
	if got[0] != New || got[1] != Repeat || got[2] != Repeat {
		t.Fatalf("outcomes %v", got)
	}
	var n, out int64
	s.DB.QueryRow(`SELECT count(*), max(output_tokens) FROM responses`).Scan(&n, &out)
	if n != 1 || out != 300 {
		t.Fatalf("rows %d output %d", n, out)
	}
}

func TestALaterSmallerOutputDoesNotLowerIt(t *testing.T) {
	s, _ := open(t)
	upsert(t, s, Response{MessageID: "M1", RequestID: "R1", UUID: "u1", SessionID: "s1", Output: 300})
	upsert(t, s, Response{MessageID: "M1", RequestID: "R1", UUID: "u2", SessionID: "s1", Output: 10})
	var out int64
	s.DB.QueryRow(`SELECT output_tokens FROM responses`).Scan(&out)
	if out != 300 {
		t.Fatalf("output %d", out)
	}
}

func TestAResumedCopyStaysWithTheFirstSession(t *testing.T) {
	s, _ := open(t)
	orig := Response{MessageID: "M1", RequestID: "R1", UUID: "u1", SessionID: "s1", Output: 5}
	cp := orig
	cp.UUID, cp.SessionID = "u9", "s2"
	upsert(t, s, orig)
	if o := upsert(t, s, cp); o != Copy {
		t.Fatalf("outcome %v", o)
	}
	var sid string
	s.DB.QueryRow(`SELECT session_id FROM responses`).Scan(&sid)
	if sid != "s1" {
		t.Fatalf("billed to %s", sid)
	}
}

func TestEmptyStringsAreStoredAsNull(t *testing.T) {
	s, _ := open(t)
	upsert(t, s, Response{MessageID: "M1", UUID: "u1"})
	var nulls int
	s.DB.QueryRow(`SELECT (session_id IS NULL) + (ts IS NULL) + (model IS NULL) + (speed IS NULL) FROM responses`).Scan(&nulls)
	if nulls != 4 {
		t.Fatalf("%d of 4 columns are NULL", nulls)
	}
}

func TestACustomTitleWinsWhateverTheOrder(t *testing.T) {
	s, _ := open(t)
	tx, _ := s.DB.Begin()
	if err := SetTitle(tx, "s1", "Mine", "custom"); err != nil {
		t.Fatal(err)
	}
	if err := SetTitle(tx, "s1", "Generated", "ai"); err != nil {
		t.Fatal(err)
	}
	if err := SetTitle(tx, "s2", "Generated", "ai"); err != nil {
		t.Fatal(err)
	}
	if err := SetTitle(tx, "s2", "Renamed", "custom"); err != nil {
		t.Fatal(err)
	}
	tx.Commit()
	for sid, want := range map[string]string{"s1": "Mine", "s2": "Renamed"} {
		var title string
		s.DB.QueryRow(`SELECT title FROM session_meta WHERE session_id=?`, sid).Scan(&title)
		if title != want {
			t.Errorf("%s title %q, want %q", sid, title, want)
		}
	}
}

func TestTheCursorRoundTripsAndSurvivesReopen(t *testing.T) {
	s, path := open(t)
	if _, seen, err := GetCursor(s.DB, "/a.jsonl"); err != nil || seen {
		t.Fatalf("seen %v err %v", seen, err)
	}
	tx, _ := s.DB.Begin()
	if err := PutCursor(tx, Cursor{Path: "/a.jsonl", MtimeNS: 7, Size: 100, BytesRead: 90, Lines: 3}); err != nil {
		t.Fatal(err)
	}
	tx.Commit()
	s.Close()
	s2, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer s2.Close()
	c, seen, err := GetCursor(s2.DB, "/a.jsonl")
	if err != nil || !seen || c.BytesRead != 90 || c.Lines != 3 || c.MtimeNS != 7 {
		t.Fatalf("cursor %+v seen %v err %v", c, seen, err)
	}
}

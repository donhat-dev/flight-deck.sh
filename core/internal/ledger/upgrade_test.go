package ledger

import (
	"database/sql"
	"path/filepath"
	"testing"
)

const schemaV1 = `
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE responses (
  message_id TEXT NOT NULL, request_id TEXT NOT NULL, uuid TEXT NOT NULL, session_id TEXT,
  project TEXT, model TEXT, ts TEXT, input_tokens INTEGER NOT NULL DEFAULT 0,
  cache_read INTEGER NOT NULL DEFAULT 0, cache_create_5m INTEGER NOT NULL DEFAULT 0,
  cache_create_1h INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
  service_tier TEXT, speed TEXT, inference_geo TEXT,
  source TEXT NOT NULL DEFAULT 'transcript', dedupe_method TEXT,
  PRIMARY KEY (message_id, request_id)) WITHOUT ROWID;
CREATE TABLE files (path TEXT PRIMARY KEY, mtime_ns INTEGER NOT NULL, size INTEGER NOT NULL,
  bytes_read INTEGER NOT NULL, lines INTEGER NOT NULL) WITHOUT ROWID;
INSERT INTO meta VALUES ('schema_version', '1');
INSERT INTO responses (message_id, request_id, uuid, session_id, output_tokens)
  VALUES ('m1', 'r1', 'u1', 's1', 50);
INSERT INTO files VALUES ('/p/s1.jsonl', 9, 100, 100, 3);
`

// A version 1 ledger keeps its rows, gains the thinking columns, and has every
// cursor set back so the next ingest fills them in.
func TestAVersion1LedgerIsUpgradedAndReadAgain(t *testing.T) {
	path := filepath.Join(t.TempDir(), "ledger.db")
	db, err := sql.Open("sqlite", "file:"+path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := db.Exec(schemaV1); err != nil {
		t.Fatal(err)
	}
	db.Close()

	s, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	var version string
	var read int64
	if err := s.DB.QueryRow(`SELECT value FROM meta WHERE key='schema_version'`).Scan(&version); err != nil {
		t.Fatal(err)
	}
	if err := s.DB.QueryRow(`SELECT bytes_read FROM files`).Scan(&read); err != nil {
		t.Fatal(err)
	}
	if version != "2" || read != 0 {
		t.Fatalf("version %s, bytes_read %d", version, read)
	}
	think := int64(80)
	if o := upsert(t, s, Response{MessageID: "m1", RequestID: "r1", UUID: "u9", SessionID: "s1",
		Output: 50, ThinkingTokens: &think, Effort: "high"}); o != Repeat {
		t.Fatalf("outcome %v", o)
	}
	var got int64
	var effort string
	var n int
	if err := s.DB.QueryRow(`SELECT thinking_tokens, effort, (SELECT count(*) FROM responses) FROM responses`).
		Scan(&got, &effort, &n); err != nil {
		t.Fatal(err)
	}
	if got != 80 || effort != "high" || n != 1 {
		t.Fatalf("thinking %d, effort %q, rows %d", got, effort, n)
	}
}

// Thinking tokens only grow: a later line with fewer never lowers them.
func TestThinkingTokensKeepTheLargest(t *testing.T) {
	s, _ := open(t)
	big, small := int64(120), int64(100)
	upsert(t, s, Response{MessageID: "m", RequestID: "r", UUID: "a", SessionID: "s", ThinkingTokens: &big})
	upsert(t, s, Response{MessageID: "m", RequestID: "r", UUID: "b", SessionID: "s", ThinkingTokens: &small})
	var got int64
	if err := s.DB.QueryRow(`SELECT thinking_tokens FROM responses`).Scan(&got); err != nil {
		t.Fatal(err)
	}
	if got != 120 {
		t.Fatalf("thinking tokens %d", got)
	}
}

// Package ledger is the durable spend ledger: one row per billed API response.
//
// It is owned data, not a cache. Claude Code deletes old transcripts, and twice
// (2026-08-25, 2026-09-29) Postgres crash recovery emptied the Python ledger's
// UNLOGGED tables; rows whose transcripts were already gone never came back. So
// a re-read never deletes a row, and the per-file read cursor lives in this file
// and commits in the same transaction as the rows it covers.
package ledger

import (
	"database/sql"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"

	_ "modernc.org/sqlite"
)

const schemaVersion = 1

const schema = `
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS responses (
  message_id      TEXT NOT NULL,
  request_id      TEXT NOT NULL,
  uuid            TEXT NOT NULL,
  session_id      TEXT,
  project         TEXT,
  model           TEXT,
  ts              TEXT,
  input_tokens    INTEGER NOT NULL DEFAULT 0,
  cache_read      INTEGER NOT NULL DEFAULT 0,
  cache_create_5m INTEGER NOT NULL DEFAULT 0,
  cache_create_1h INTEGER NOT NULL DEFAULT 0,
  output_tokens   INTEGER NOT NULL DEFAULT 0,
  service_tier    TEXT,
  speed           TEXT,
  inference_geo   TEXT,
  source          TEXT NOT NULL DEFAULT 'transcript',
  dedupe_method   TEXT,
  PRIMARY KEY (message_id, request_id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS responses_ts ON responses(ts);
CREATE INDEX IF NOT EXISTS responses_session ON responses(session_id);
CREATE TABLE IF NOT EXISTS files (
  path       TEXT PRIMARY KEY,
  mtime_ns   INTEGER NOT NULL,
  size       INTEGER NOT NULL,
  bytes_read INTEGER NOT NULL,
  lines      INTEGER NOT NULL
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS session_meta (
  session_id   TEXT PRIMARY KEY,
  title        TEXT,
  title_source TEXT
) WITHOUT ROWID;
`

type Store struct{ DB *sql.DB }

// Open creates or opens the ledger. WAL lets readers run beside the writer;
// _txlock=immediate takes the write lock at BEGIN, so two writers (the CLI and
// `fd serve`) queue on busy_timeout instead of failing on a lock upgrade.
func Open(path string) (*Store, error) {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return nil, err
	}
	dsn := "file:" + path + "?_pragma=journal_mode(WAL)&_pragma=synchronous(NORMAL)" +
		"&_pragma=busy_timeout(10000)&_txlock=immediate"
	db, err := sql.Open("sqlite", dsn)
	if err != nil {
		return nil, err
	}
	if err := prepare(db); err != nil {
		db.Close()
		return nil, err
	}
	return &Store{DB: db}, nil
}

// prepare creates a new ledger, or checks an existing one with reads only, so
// a read-only command opens it while another process holds the write lock.
// A ledger written by a newer fd is refused: this binary would write rows its
// layout does not expect.
func prepare(db *sql.DB) error {
	var v string
	err := db.QueryRow(`SELECT value FROM meta WHERE key='schema_version'`).Scan(&v)
	if err == nil {
		n, perr := strconv.Atoi(v)
		if perr != nil || n > schemaVersion {
			return fmt.Errorf("ledger schema version %q is newer than this fd understands (%d); use a newer fd",
				v, schemaVersion)
		}
		return nil // steps from version n up to schemaVersion go here once a second version exists
	}
	if !errors.Is(err, sql.ErrNoRows) && !strings.Contains(err.Error(), "no such table") {
		return err
	}
	if _, err := db.Exec(schema + pricesSchema); err != nil {
		return fmt.Errorf("ledger schema: %w", err)
	}
	if _, err := db.Exec(`INSERT INTO meta (key, value) VALUES ('schema_version', ?)
		ON CONFLICT(key) DO NOTHING`, strconv.Itoa(schemaVersion)); err != nil {
		return err
	}
	if err := seedPrices(db); err != nil {
		return fmt.Errorf("seed prices: %w", err)
	}
	return nil
}

func (s *Store) Close() error { return s.DB.Close() }

// Response is one billed API response. MessageID is message.id, or
// "uuid:<line uuid>" for a line written before message.id existed.
type Response struct {
	MessageID, RequestID, UUID                   string
	SessionID, Project, Model, TS                string
	Input, CacheRead, Create5m, Create1h, Output int64
	ServiceTier, Speed, Geo                      string
}

type Outcome int

const (
	New    Outcome = iota // the first line of a response: a new billed row
	Repeat                // a further line of a response this session already holds
	Copy                  // the response belongs to another session (resume or fork history)
)

// Upsert stores one billed line. The first session to bring a response keeps
// it; a later line may raise output_tokens (early lines carry a partial count).
func Upsert(tx *sql.Tx, r Response) (Outcome, error) {
	var session sql.NullString
	err := tx.QueryRow(`SELECT session_id FROM responses WHERE message_id=? AND request_id=?`,
		r.MessageID, r.RequestID).Scan(&session)
	if errors.Is(err, sql.ErrNoRows) {
		_, err = tx.Exec(`INSERT INTO responses (message_id, request_id, uuid, session_id, project,
			model, ts, input_tokens, cache_read, cache_create_5m, cache_create_1h, output_tokens,
			service_tier, speed, inference_geo) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,
			r.MessageID, r.RequestID, r.UUID, null(r.SessionID), null(r.Project), null(r.Model),
			null(r.TS), r.Input, r.CacheRead, r.Create5m, r.Create1h, r.Output,
			null(r.ServiceTier), null(r.Speed), null(r.Geo))
		return New, err
	}
	if err != nil {
		return 0, err
	}
	if _, err := tx.Exec(`UPDATE responses SET output_tokens=? WHERE message_id=? AND request_id=?
		AND output_tokens<?`, r.Output, r.MessageID, r.RequestID, r.Output); err != nil {
		return 0, err
	}
	if session.String != r.SessionID {
		return Copy, nil
	}
	return Repeat, nil
}

// null stores "" as NULL, as the Python ledger stores a missing field.
func null(s string) any {
	if s == "" {
		return nil
	}
	return s
}

// SetTitle records a session title. A user-set ("custom") title wins over the
// generated ("ai") one whatever order the records arrive in.
func SetTitle(tx *sql.Tx, sessionID, title, source string) error {
	q := `INSERT INTO session_meta (session_id, title, title_source) VALUES (?,?,?)
		ON CONFLICT(session_id) DO UPDATE SET title=excluded.title, title_source=excluded.title_source`
	if source == "ai" {
		q += ` WHERE session_meta.title_source IS NOT 'custom'`
	}
	_, err := tx.Exec(q, sessionID, title, source)
	return err
}

// Cursor is how far one transcript has been read.
type Cursor struct {
	Path                            string
	MtimeNS, Size, BytesRead, Lines int64
}

// Querier is satisfied by *sql.DB and *sql.Tx.
type Querier interface {
	QueryRow(query string, args ...any) *sql.Row
	Query(query string, args ...any) (*sql.Rows, error)
}

func GetCursor(q Querier, path string) (Cursor, bool, error) {
	c := Cursor{Path: path}
	err := q.QueryRow(`SELECT mtime_ns, size, bytes_read, lines FROM files WHERE path=?`, path).
		Scan(&c.MtimeNS, &c.Size, &c.BytesRead, &c.Lines)
	if errors.Is(err, sql.ErrNoRows) {
		return c, false, nil
	}
	return c, err == nil, err
}

func PutCursor(tx *sql.Tx, c Cursor) error {
	_, err := tx.Exec(`INSERT INTO files (path, mtime_ns, size, bytes_read, lines) VALUES (?,?,?,?,?)
		ON CONFLICT(path) DO UPDATE SET mtime_ns=excluded.mtime_ns, size=excluded.size,
		bytes_read=excluded.bytes_read, lines=excluded.lines`,
		c.Path, c.MtimeNS, c.Size, c.BytesRead, c.Lines)
	return err
}

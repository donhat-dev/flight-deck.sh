// Package importer brings billed rows from an old FlightDeck ledger into this one.
//
// The source is the text `pg_restore -f - --data-only --table=messages <dump>`
// prints: a COPY header naming the columns, tab-separated rows, then `\.`. No
// Postgres server is involved.
package importer

import (
	"bufio"
	"database/sql"
	"errors"
	"fmt"
	"io"
	"strconv"
	"strings"

	"fdcore/internal/ledger"
	"fdcore/internal/pricing"
)

var ErrNoCopyHeader = errors.New("no COPY header: expected `pg_restore -f - --data-only --table=messages` output")

type Stats struct {
	Read          int     `json:"read"`
	Billed        int     `json:"billed"`
	Inserted      int     `json:"inserted"`
	KeyPresent    int     `json:"skipped_key_present"`
	SessionOnDisk int     `json:"skipped_session_on_disk"`
	OverlapRows   int     `json:"possible_overlap_rows"`
	OverlapCost   float64 `json:"possible_overlap_cost"`
}

// PGCopy imports the billed rows (dup_of NULL) of a FlightDeck `messages` table.
// A transcript row always wins: a keyed row is skipped when the key is present,
// and a row without message.id, vouched for only by the heuristic dedupe, is
// imported only for a session with no transcript rows. Imported rows carry
// source='import'. Reversing it takes `DELETE FROM responses WHERE source='import';
// DELETE FROM files;` and a fresh `fd ingest`, since a transcript line read after
// the import may have matched an imported key and moved its cursor past.
func PGCopy(s *ledger.Store, r io.Reader, dryRun bool) (Stats, error) {
	var st Stats
	tx, err := s.DB.Begin()
	if err != nil {
		return st, err
	}
	defer tx.Rollback()
	err = readCopy(r, func(row map[string]*string) error {
		st.Read++
		if row["dup_of"] != nil {
			return nil
		}
		st.Billed++
		return importRow(tx, row, &st)
	})
	if err != nil {
		return st, err
	}
	if err := overlap(tx, &st); err != nil {
		return st, err
	}
	if dryRun {
		return st, nil
	}
	return st, tx.Commit()
}

func importRow(tx *sql.Tx, row map[string]*string, st *Stats) error {
	uuid := val(row, "uuid")
	if uuid == "" {
		return nil
	}
	msgID, reqID := val(row, "message_id"), val(row, "request_id")
	if msgID == "" {
		var n int
		if err := tx.QueryRow(`SELECT count(*) FROM responses WHERE session_id=? AND source='transcript'`,
			val(row, "session_id")).Scan(&n); err != nil {
			return err
		}
		if n > 0 {
			st.SessionOnDisk++
			return nil
		}
		msgID = "uuid:" + uuid
	}
	res, err := tx.Exec(`INSERT INTO responses (message_id, request_id, uuid, session_id, project, model, ts,
		input_tokens, cache_read, cache_create_5m, cache_create_1h, output_tokens,
		service_tier, speed, inference_geo, source, dedupe_method)
		VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'import',?) ON CONFLICT(message_id, request_id) DO NOTHING`,
		msgID, reqID, uuid, ptr(row, "session_id"), ptr(row, "project"), ptr(row, "model"), ptr(row, "ts"),
		num(row, "input_tokens"), num(row, "cache_read"), num(row, "cache_create_5m"),
		num(row, "cache_create_1h"), num(row, "output_tokens"),
		ptr(row, "service_tier"), ptr(row, "speed"), ptr(row, "inference_geo"), ptr(row, "dedupe_method"))
	if err != nil {
		return err
	}
	if n, _ := res.RowsAffected(); n == 0 {
		st.KeyPresent++
	} else {
		st.Inserted++
	}
	return nil
}

type tuple struct {
	model                                string
	input, cacheRead, create5m, create1h int64
}

// overlap counts imported uuid-keyed rows that match a transcript row on the
// input-side token tuple with at least 10k cached tokens, the cross-session key
// scripts/cost-audit/audit_cost.py used. Such a row may bill a response twice;
// it is reported, not removed.
func overlap(tx *sql.Tx, st *Stats) error {
	const cached = `cache_read + cache_create_5m + cache_create_1h >= 10000`
	prices, err := ledger.PriceTable(tx)
	if err != nil {
		return err
	}
	seen := map[tuple]bool{}
	rows, err := tx.Query(`SELECT COALESCE(model,''), input_tokens, cache_read, cache_create_5m, cache_create_1h
		FROM responses WHERE source='transcript' AND ` + cached)
	if err != nil {
		return err
	}
	for rows.Next() {
		var k tuple
		if err := rows.Scan(&k.model, &k.input, &k.cacheRead, &k.create5m, &k.create1h); err != nil {
			rows.Close()
			return err
		}
		seen[k] = true
	}
	rows.Close()
	rows, err = tx.Query(`SELECT COALESCE(model,''), input_tokens, cache_read, cache_create_5m, cache_create_1h,
		output_tokens, COALESCE(ts,'') FROM responses
		WHERE source='import' AND message_id LIKE 'uuid:%' AND ` + cached)
	if err != nil {
		return err
	}
	defer rows.Close()
	for rows.Next() {
		var k tuple
		var out int64
		var ts string
		if err := rows.Scan(&k.model, &k.input, &k.cacheRead, &k.create5m, &k.create1h, &out, &ts); err != nil {
			return err
		}
		if !seen[k] {
			continue
		}
		st.OverlapRows++
		if c, ok := prices.Cost(pricing.Usage{Model: k.model, Input: k.input, CacheRead: k.cacheRead,
			Create5m: k.create5m, Create1h: k.create1h, Out: out, TS: ts}); ok {
			st.OverlapCost += c
		}
	}
	return rows.Err()
}

// readCopy calls each with one row per data line, keyed by the header's column names.
func readCopy(r io.Reader, each func(map[string]*string) error) error {
	sc := bufio.NewScanner(r)
	sc.Buffer(make([]byte, 1<<20), 64<<20)
	var cols []string
	for sc.Scan() {
		line := sc.Text()
		if cols == nil {
			if strings.HasPrefix(line, "COPY ") {
				cols = copyColumns(line)
			}
			continue // pg_restore's preamble: comments and SET statements
		}
		if line == `\.` {
			return nil
		}
		fields := strings.Split(line, "\t")
		if len(fields) != len(cols) {
			return fmt.Errorf("a row has %d fields, the header %d", len(fields), len(cols))
		}
		row := make(map[string]*string, len(cols))
		for i, f := range fields {
			row[cols[i]] = unescape(f)
		}
		if err := each(row); err != nil {
			return err
		}
	}
	if err := sc.Err(); err != nil {
		return err
	}
	if cols == nil {
		return ErrNoCopyHeader
	}
	return errors.New("the COPY data ends without its \\. terminator")
}

// copyColumns reads `COPY public.messages (uuid, session_id, ...) FROM stdin;`.
func copyColumns(line string) []string {
	open, end := strings.Index(line, "("), strings.LastIndex(line, ")")
	if open < 0 || end < open {
		return nil
	}
	parts := strings.Split(line[open+1:end], ",")
	for i := range parts {
		parts[i] = strings.Trim(strings.TrimSpace(parts[i]), `"`)
	}
	return parts
}

// unescape decodes one COPY text field: \N is NULL, backslash escapes as Postgres writes them.
func unescape(f string) *string {
	if f == `\N` {
		return nil
	}
	if !strings.Contains(f, `\`) {
		return &f
	}
	var b strings.Builder
	for i := 0; i < len(f); i++ {
		c := f[i]
		if c != '\\' || i+1 == len(f) {
			b.WriteByte(c)
			continue
		}
		i++
		switch f[i] {
		case 't':
			b.WriteByte('\t')
		case 'n':
			b.WriteByte('\n')
		case 'r':
			b.WriteByte('\r')
		case 'b':
			b.WriteByte('\b')
		case 'f':
			b.WriteByte('\f')
		case 'v':
			b.WriteByte('\v')
		default:
			b.WriteByte(f[i])
		}
	}
	s := b.String()
	return &s
}

func val(row map[string]*string, k string) string {
	if p := row[k]; p != nil {
		return *p
	}
	return ""
}

func ptr(row map[string]*string, k string) any {
	if v := val(row, k); v != "" {
		return v
	}
	return nil
}

func num(row map[string]*string, k string) int64 {
	n, _ := strconv.ParseInt(val(row, k), 10, 64)
	return n
}

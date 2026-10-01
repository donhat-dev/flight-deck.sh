package ingest

import (
	"bufio"
	"encoding/json"
	"io"
	"os"

	"fdcore/internal/ledger"
)

// Ingester reads transcripts into one ledger.
type Ingester struct{ Store *ledger.Store }

// File reads what is new in one transcript and returns the new billed rows. The
// cursor row is the only "already read" state, so a wiped ledger is read again
// in full; the Python reader's process cache left 520 files unread after the
// 2026-09-29 crash. Rows and cursor commit together.
func (in *Ingester) File(path string) (int, error) {
	st, err := os.Stat(path)
	if err != nil {
		return 0, nil // removed between listing and reading; its rows stay
	}
	cur, seen, err := ledger.GetCursor(in.Store.DB, path)
	if err != nil {
		return 0, err
	}
	mtime := st.ModTime().UnixNano()
	if seen && cur.MtimeNS == mtime && cur.Size == st.Size() {
		return 0, nil
	}
	var start, lines int64
	if seen && st.Size() >= cur.BytesRead {
		start, lines = cur.BytesRead, cur.Lines
	} // else new, or shorter than what was read (rewritten): from the top; keys dedupe
	f, err := os.Open(path)
	if err != nil {
		return 0, nil
	}
	defer f.Close()
	if _, err := f.Seek(start, io.SeekStart); err != nil {
		return 0, err
	}
	tx, err := in.Store.DB.Begin()
	if err != nil {
		return 0, err
	}
	defer tx.Rollback()
	rd := bufio.NewReaderSize(f, 1<<20)
	added, consumed := 0, int64(0)
	for {
		raw, err := rd.ReadBytes('\n')
		if err == io.EOF {
			break // a trailing line without "\n" is still being written
		}
		if err != nil {
			return 0, err
		}
		consumed += int64(len(raw))
		lines++
		var r record
		if json.Unmarshal(raw, &r) != nil {
			continue // torn write: one real file has a run of NUL bytes
		}
		if resp, ok := billed(r); ok {
			o, err := ledger.Upsert(tx, resp)
			if err != nil {
				return 0, err
			}
			if o == ledger.New {
				added++
			}
			continue
		}
		if err := title(tx, r); err != nil {
			return 0, err
		}
	}
	if err := ledger.PutCursor(tx, ledger.Cursor{Path: path, MtimeNS: mtime, Size: st.Size(),
		BytesRead: start + consumed, Lines: lines}); err != nil {
		return 0, err
	}
	return added, tx.Commit()
}

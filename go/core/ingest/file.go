package ingest

import (
	"bufio"
	"io"
	"os"

	"flightdeck/core/activity"
	"flightdeck/core/ledger"
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
	mtime := st.ModTime().UnixNano()
	unchanged := func(cur ledger.Cursor, seen bool) bool {
		return seen && cur.MtimeNS == mtime && cur.Size == st.Size()
	}
	// Cheap check without the write lock, so a sweep over unchanged files does
	// not queue behind another writer.
	if cur, seen, err := ledger.GetCursor(in.Store.DB, path); err != nil {
		return 0, err
	} else if unchanged(cur, seen) {
		return 0, nil
	}
	// The store begins IMMEDIATE, so this holds the write lock. The cursor is
	// read again under it: another process (fd serve beside fd ingest) may have
	// read the same lines meanwhile, and its busy time must not be added twice.
	tx, err := in.Store.DB.Begin()
	if err != nil {
		return 0, err
	}
	defer tx.Rollback()
	cur, seen, err := ledger.GetCursor(tx, path)
	if err != nil {
		return 0, err
	}
	if unchanged(cur, seen) {
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
	// The turn and busy-time tracker reads the same lines, resumes mid-file
	// from the cursor's act_* state, and writes in this transaction.
	tracker := activity.NewTracker(tx, path, activity.State{
		LastTS: cur.ActLastTS, InTurn: cur.ActInTurn, WaitHuman: cur.ActWaitHuman,
		PromptID: cur.ActPromptID, Answered: cur.ActAnswered},
		seen && start > 0, activity.IdleCapSeconds())
	rd := bufio.NewReaderSize(f, 1<<20)
	added, consumed := 0, int64(0)
	var s stream
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
		resp, ok, r, parsed := s.next(raw)
		if !parsed {
			continue
		}
		copied := false
		if ok {
			// A replayed line keeps the turn it was stamped with when first read.
			if !tracker.IsReplay(r.Timestamp) {
				resp.PromptID, resp.AgentID = tracker.Stamp()
			}
			o, err := ledger.Upsert(tx, resp)
			if err != nil {
				return 0, err
			}
			copied = o == ledger.Copy
			if o == ledger.New {
				added++
			} else if err := title(tx, r); err != nil {
				return 0, err
			}
		} else if err := title(tx, r); err != nil {
			return 0, err
		}
		if err := tracker.Feed(lineOf(r), copied); err != nil {
			return 0, err
		}
	}
	if err := tracker.Flush(); err != nil {
		return 0, err
	}
	act := tracker.State()
	if err := ledger.PutCursor(tx, ledger.Cursor{Path: path, MtimeNS: mtime, Size: st.Size(),
		BytesRead: start + consumed, Lines: lines,
		ActLastTS: act.LastTS, ActInTurn: act.InTurn, ActWaitHuman: act.WaitHuman,
		ActPromptID: act.PromptID, ActAnswered: act.Answered}); err != nil {
		return 0, err
	}
	return added, tx.Commit()
}

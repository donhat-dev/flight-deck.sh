package ingest

import (
	"bufio"
	"encoding/json"
	"io"
	"os"
	"time"

	"fdcore/internal/ledger"
)

// headBytes is how far into a transcript its first timestamp is looked for.
// The first lines are small: a snapshot, a mode, the first prompt.
const headBytes = 256 << 10

// Oldest is the earliest timestamp at the head of any of the files: the first
// moment a direct read can answer for, since Claude Code deletes older
// transcripts. "" when no file has one.
func Oldest(files []Transcript) string {
	oldest := ""
	for _, t := range files {
		if ts := firstTS(t.Path); ts != "" && (oldest == "" || ts < oldest) {
			oldest = ts
		}
	}
	return oldest
}

func firstTS(path string) string {
	f, err := os.Open(path)
	if err != nil {
		return ""
	}
	defer f.Close()
	rd := bufio.NewReader(io.LimitReader(f, headBytes))
	for {
		raw, err := rd.ReadBytes('\n')
		var r struct {
			Timestamp string `json:"timestamp"`
		}
		if json.Unmarshal(raw, &r) == nil && r.Timestamp != "" {
			return r.Timestamp
		}
		if err != nil {
			return ""
		}
	}
}

// ReadRaw reads the transcripts themselves, without the ledger: every file
// written to on or after `cutoff`, and of them the responses whose timestamp is
// at or after `since` (a YYYY-MM-DD compared with the UTC timestamp, as the
// ledger queries do). Responses are merged as the ledger merges them: the first
// file in creation order keeps a response, later lines only raise its counts.
func ReadRaw(files []Transcript, cutoff time.Time, since string) ([]ledger.Response, error) {
	byKey := map[[2]string]int{}
	var out []ledger.Response
	for _, t := range files {
		if t.MtimeNS < cutoff.UnixNano() {
			continue // not written to since the cutoff: it holds nothing newer
		}
		if err := readRawFile(t.Path, since, byKey, &out); err != nil {
			return nil, err
		}
	}
	return out, nil
}

func readRawFile(path, since string, byKey map[[2]string]int, out *[]ledger.Response) error {
	f, err := os.Open(path)
	if err != nil {
		return nil // removed between listing and reading
	}
	defer f.Close()
	rd := bufio.NewReaderSize(f, 1<<20)
	var s stream
	for {
		raw, err := rd.ReadBytes('\n')
		if err == io.EOF {
			return nil
		}
		if err != nil {
			return err
		}
		resp, ok, _, _ := s.next(raw)
		if !ok || resp.TS < since {
			continue
		}
		key := [2]string{resp.MessageID, resp.RequestID}
		i, seen := byKey[key]
		if !seen {
			byKey[key] = len(*out)
			*out = append(*out, resp)
			continue
		}
		merge(&(*out)[i], resp)
	}
}

// merge applies what Upsert does to a row it already holds.
func merge(held *ledger.Response, r ledger.Response) {
	if r.Output > held.Output {
		held.Output = r.Output
	}
	held.ThinkingTokens = larger(held.ThinkingTokens, r.ThinkingTokens)
	held.ThinkingMS = larger(held.ThinkingMS, r.ThinkingMS)
	held.ThinkWaitMS = larger(held.ThinkWaitMS, r.ThinkWaitMS)
	if held.Effort == "" {
		held.Effort = r.Effort
	}
}

func larger(a, b *int64) *int64 {
	if b == nil || (a != nil && *a >= *b) {
		return a
	}
	return b
}

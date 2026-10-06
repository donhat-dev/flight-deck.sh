// Package ingest reads Claude Code transcripts (<projects>/**/*.jsonl) into the
// ledger, incrementally, by byte offset.
package ingest

import (
	"database/sql"
	"encoding/json"
	"strings"
	"time"

	"fdcore/internal/ledger"
)

// record holds the fields any line type may carry. `message` stays raw: its
// shape differs by line type and only assistant lines need it decoded.
type record struct {
	Type        string          `json:"type"`
	UUID        string          `json:"uuid"`
	SessionID   string          `json:"sessionId"`
	CWD         string          `json:"cwd"`
	Timestamp   string          `json:"timestamp"`
	RequestID   string          `json:"requestId"`
	CustomTitle string          `json:"customTitle"`
	AITitle     string          `json:"aiTitle"`
	Message     json.RawMessage `json:"message"`
	// On the line of a thinking block, from CLI 2.1.289 on.
	ThinkingDurationMs *int64 `json:"thinkingDurationMs"`
	Effort             string `json:"effort"`
	PerTurnEffort      string `json:"perTurnEffort"`
	IsSidechain        bool   `json:"isSidechain"`
}

type assistantMessage struct {
	ID      string          `json:"id"`
	Model   string          `json:"model"`
	Usage   json.RawMessage `json:"usage"`
	Content json.RawMessage `json:"content"`
}

type block struct {
	Type string `json:"type"`
}

type usage struct {
	InputTokens   int64  `json:"input_tokens"`
	CacheRead     int64  `json:"cache_read_input_tokens"`
	OutputTokens  int64  `json:"output_tokens"`
	ServiceTier   string `json:"service_tier"`
	Speed         string `json:"speed"`
	InferenceGeo  string `json:"inference_geo"`
	CacheCreation struct {
		E5m int64 `json:"ephemeral_5m_input_tokens"`
		E1h int64 `json:"ephemeral_1h_input_tokens"`
	} `json:"cache_creation"`
	OutputTokensDetails struct {
		ThinkingTokens *int64 `json:"thinking_tokens"`
	} `json:"output_tokens_details"`
}

// A gap longer than this between the last line before a response and its
// thinking is not the response's own wait: the history was resumed or copied.
const maxThinkWait = 30 * time.Minute

// stream carries what one read of a transcript needs across lines: when the
// line before the current response was written, which is when its request
// left. A read that starts mid-file knows neither for its first response.
type stream struct {
	lastTS, curMsg, start string
}

// next parses one line. ok reports a billed row in resp; rec is the parsed
// line either way (parsed is false for a torn write).
func (s *stream) next(raw []byte) (resp ledger.Response, ok bool, rec record, parsed bool) {
	if json.Unmarshal(raw, &rec) != nil {
		return resp, false, rec, false // torn write: one real file has a run of NUL bytes
	}
	var thinking bool
	resp, thinking, ok = billedLine(rec)
	if ok {
		if resp.MessageID != s.curMsg {
			s.curMsg, s.start = resp.MessageID, s.lastTS
		}
		if thinking {
			resp.ThinkWaitMS = waitMS(s.start, rec.Timestamp)
		}
	}
	if rec.Timestamp != "" {
		s.lastTS = rec.Timestamp
	}
	return resp, ok, rec, true
}

func thinks(content json.RawMessage) bool {
	var blocks []block
	if json.Unmarshal(content, &blocks) != nil {
		return false
	}
	for _, b := range blocks {
		if b.Type == "thinking" || b.Type == "redacted_thinking" {
			return true
		}
	}
	return false
}

func waitMS(start, end string) *int64 {
	a, err1 := time.Parse(time.RFC3339Nano, start)
	b, err2 := time.Parse(time.RFC3339Nano, end)
	if err1 != nil || err2 != nil {
		return nil
	}
	d := b.Sub(a)
	if d < 0 || d > maxThinkWait {
		return nil
	}
	ms := d.Milliseconds()
	return &ms
}

// billed turns an assistant line with usage into a ledger row. One API response
// is written as one line per content block, each repeating its usage, so the
// key is (message.id, requestId), not the line uuid.
func billed(r record) (ledger.Response, bool) {
	resp, _, ok := billedLine(r)
	return resp, ok
}

// billedLine is billed, and whether the line holds a thinking block.
func billedLine(r record) (ledger.Response, bool, bool) {
	if r.Type != "assistant" || r.UUID == "" || len(r.Message) == 0 {
		return ledger.Response{}, false, false
	}
	var m assistantMessage
	if json.Unmarshal(r.Message, &m) != nil || noUsage(m.Usage) {
		return ledger.Response{}, false, false
	}
	var u usage
	if json.Unmarshal(m.Usage, &u) != nil {
		return ledger.Response{}, false, false
	}
	id := m.ID
	if id == "" {
		id = "uuid:" + r.UUID
	}
	return ledger.Response{
		MessageID: id, RequestID: r.RequestID, UUID: r.UUID,
		SessionID: r.SessionID, Project: r.CWD, Model: m.Model, TS: r.Timestamp,
		Input: u.InputTokens, CacheRead: u.CacheRead,
		Create5m: u.CacheCreation.E5m, Create1h: u.CacheCreation.E1h, Output: u.OutputTokens,
		ServiceTier: u.ServiceTier, Speed: u.Speed, Geo: u.InferenceGeo,
		ThinkingTokens: u.OutputTokensDetails.ThinkingTokens, ThinkingMS: r.ThinkingDurationMs,
		Effort: firstOf(r.PerTurnEffort, r.Effort), Sidechain: r.IsSidechain,
	}, thinks(m.Content), true
}

func firstOf(a, b string) string {
	if a != "" {
		return a
	}
	return b
}

// noUsage mirrors the Python reader's `if not usage`: absent, null and {} are not billed.
func noUsage(raw json.RawMessage) bool {
	s := strings.TrimSpace(string(raw))
	return s == "" || s == "null" || s == "{}"
}

func title(tx *sql.Tx, r record) error {
	switch {
	case r.Type == "custom-title" && r.SessionID != "" && r.CustomTitle != "":
		return ledger.SetTitle(tx, r.SessionID, r.CustomTitle, "custom")
	case r.Type == "ai-title" && r.SessionID != "" && r.AITitle != "":
		return ledger.SetTitle(tx, r.SessionID, r.AITitle, "ai")
	}
	return nil
}

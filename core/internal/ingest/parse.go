// Package ingest reads Claude Code transcripts (<projects>/**/*.jsonl) into the
// ledger, incrementally, by byte offset.
package ingest

import (
	"database/sql"
	"encoding/json"
	"strings"

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
}

type assistantMessage struct {
	ID    string          `json:"id"`
	Model string          `json:"model"`
	Usage json.RawMessage `json:"usage"`
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
}

// billed turns an assistant line with usage into a ledger row. One API response
// is written as one line per content block, each repeating its usage, so the
// key is (message.id, requestId), not the line uuid.
func billed(r record) (ledger.Response, bool) {
	if r.Type != "assistant" || r.UUID == "" || len(r.Message) == 0 {
		return ledger.Response{}, false
	}
	var m assistantMessage
	if json.Unmarshal(r.Message, &m) != nil || noUsage(m.Usage) {
		return ledger.Response{}, false
	}
	var u usage
	if json.Unmarshal(m.Usage, &u) != nil {
		return ledger.Response{}, false
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
	}, true
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

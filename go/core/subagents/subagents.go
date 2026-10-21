// Package subagents reads Claude Code subagent transcripts for the sessions
// list — the <projects>/**/<session>/subagents/**/agent-*.jsonl files
// transcript.py parses for the `subagents` breakdown on /api/sessions rows.
// Read-only: it touches transcript files, never the ledger.
package subagents

import (
	"bytes"
	"encoding/json"
	"io/fs"
	"os"
	"path/filepath"
	"slices"
	"sort"
	"strings"
	"sync"
	"unicode"
	"unicode/utf8"

	"flightdeck/core/pricing"
)

// File is one parsed agent-*.jsonl — the dict _read_subagent_file builds.
// AgentType keeps the raw attributionAgent value; nil marshals as JSON null.
type File struct {
	AgentID   string
	AgentType json.RawMessage
	Model     *string
	Turns     int64
	Input     int64
	CacheRead int64
	Create5m  int64
	Create1h  int64
	Output    int64
	FirstTS   *string
	LastTS    *string
	Dispatch  string
}

// Row is one element of a session row's `subagents`, in FastAPI's key order.
type Row struct {
	AgentID   string          `json:"agent_id"`
	AgentType json.RawMessage `json:"agent_type"`
	Model     *string         `json:"model"`
	Turns     int64           `json:"turns"`
	InputRaw  int64           `json:"input_raw"`
	Output    int64           `json:"output"`
	CacheRead int64           `json:"cache_read"`
	Cost      float64         `json:"cost"`
	FirstTS   *string         `json:"first_ts"`
	LastTS    *string         `json:"last_ts"`
	Dispatch  string          `json:"dispatch"`
}

// Row prices the file's whole run under its last model at its first
// timestamp, with no speed or geo modifiers, as metrics.py does. An unpriced
// model costs 0, not an error.
func (f *File) Row(prices *pricing.Table) Row {
	cost, _ := prices.Cost(pricing.Usage{
		Model: str(f.Model), Input: f.Input, CacheRead: f.CacheRead,
		Create5m: f.Create5m, Create1h: f.Create1h, Out: f.Output,
		TS: str(f.FirstTS)})
	return Row{
		AgentID: f.AgentID, AgentType: f.AgentType, Model: f.Model,
		Turns: f.Turns,
		InputRaw: f.Input + f.CacheRead + f.Create5m + f.Create1h,
		Output:   f.Output, CacheRead: f.CacheRead, Cost: cost,
		FirstTS: f.FirstTS, LastTS: f.LastTS, Dispatch: f.Dispatch}
}

// FilesBySession groups every <session>/subagents/**/agent-*.jsonl under root
// by parent session — the directory above `subagents`; workflow agents nested
// in subagents/workflows/<wf>/ resolve to the same session. One walk for the
// whole page, like transcript.py:subagent_files_by_session; a missing root
// scans empty, as glob does on a path that does not exist.
func FilesBySession(root string) map[string][]string {
	out := map[string][]string{}
	filepath.WalkDir(root, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return nil // an unreadable subtree scans empty, like glob
		}
		if path != root && strings.HasPrefix(d.Name(), ".") {
			if d.IsDir() {
				return filepath.SkipDir
			}
			return nil
		}
		name := d.Name()
		if d.IsDir() || !strings.HasPrefix(name, "agent-") || !strings.HasSuffix(name, ".jsonl") {
			return nil
		}
		parts := strings.Split(path, string(filepath.Separator))
		if i := slices.Index(parts, "subagents"); i >= 1 {
			out[parts[i-1]] = append(out[parts[i-1]], path)
		}
		return nil
	})
	for _, paths := range out {
		sort.Strings(paths)
	}
	return out
}

// Parsed files keyed by path, invalidated by (mtime_ns, size): a finished
// agent's transcript never changes, so a page render re-reads nothing.
var (
	cacheMu sync.Mutex
	cache   = map[string]cachedFile{}
)

type cachedFile struct {
	mtime, size int64
	file        *File
}

// Usage parses each path, in path order, and returns the files that read.
func Usage(paths []string) []*File {
	out := make([]*File, 0, len(paths))
	for _, p := range paths {
		if f := parseFile(p); f != nil {
			out = append(out, f)
		}
	}
	return out
}

func parseFile(path string) *File {
	st, err := os.Stat(path)
	if err != nil {
		return nil
	}
	mtime, size := st.ModTime().UnixNano(), st.Size()
	cacheMu.Lock()
	hit, ok := cache[path]
	cacheMu.Unlock()
	if ok && hit.mtime == mtime && hit.size == size {
		return hit.file
	}
	f := readFile(path)
	if f != nil {
		cacheMu.Lock()
		cache[path] = cachedFile{mtime: mtime, size: size, file: f}
		cacheMu.Unlock()
	}
	return f
}

// record is every field one line may carry; non-string JSON values decode as
// "" through jstr rather than failing the line, as a Python dict would.
type record struct {
	Type             json.RawMessage `json:"type"`
	UUID             json.RawMessage `json:"uuid"`
	RequestID        json.RawMessage `json:"requestId"`
	Timestamp        json.RawMessage `json:"timestamp"`
	AttributionAgent json.RawMessage `json:"attributionAgent"`
	Message          json.RawMessage `json:"message"`
}

type message struct {
	ID    json.RawMessage `json:"id"`
	Model json.RawMessage `json:"model"`
	Usage json.RawMessage `json:"usage"`
}

type usage struct {
	InputTokens  int64 `json:"input_tokens"`
	CacheRead    int64 `json:"cache_read_input_tokens"`
	CacheCreate  int64 `json:"cache_creation_input_tokens"`
	OutputTokens int64 `json:"output_tokens"`
	Ephemeral    *struct {
		M5  *int64 `json:"ephemeral_5m_input_tokens"`
		H1  *int64 `json:"ephemeral_1h_input_tokens"`
	} `json:"cache_creation"`
}

func readFile(path string) *File {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil
	}
	f := &File{AgentID: agentID(path)}
	// One API response is written as one line per content block, each
	// repeating its usage; keep one entry per (message id, request id).
	type key struct{ msg, req string }
	responses := map[key][5]int64{}
	var first, last, model string
	seenTS := false
	var dispatch *string
	for _, raw := range bytes.Split(data, []byte("\n")) {
		raw = bytes.TrimSpace(raw)
		if len(raw) == 0 {
			continue
		}
		var rec record
		if json.Unmarshal(raw, &rec) != nil {
			continue
		}
		if f.AgentType == nil && truthy(rec.AttributionAgent) {
			f.AgentType = bytes.Clone(rec.AttributionAgent)
		}
		if ts := jstr(rec.Timestamp); ts != "" {
			if !seenTS || ts < first {
				first = ts
			}
			if !seenTS || ts > last {
				last = ts
			}
			seenTS = true
		}
		if typ := jstr(rec.Type); typ != "assistant" {
			// The dispatch prompt is the first real user text.
			if dispatch == nil && typ == "user" {
				if txt := userText(rec.Message); txt != "" && !isMetaText(txt) {
					d := head(txt, 300)
					dispatch = &d
				}
			}
			continue
		}
		var m message
		if err := json.Unmarshal(rec.Message, &m); err != nil {
			m = message{}
		}
		if s := jstr(m.Model); s != "" {
			model = s
		}
		var u usage
		if err := json.Unmarshal(m.Usage, &u); err != nil {
			u = usage{}
		}
		var w1h int64
		if u.Ephemeral != nil && u.Ephemeral.H1 != nil {
			w1h = *u.Ephemeral.H1
		}
		var w5m int64
		if u.Ephemeral != nil && u.Ephemeral.M5 != nil {
			w5m = *u.Ephemeral.M5
		} else {
			w5m = u.CacheCreate - w1h // older lines carry only the combined figure
		}
		k := key{msg: jstr(m.ID), req: jstr(rec.RequestID)}
		if k.msg == "" {
			k.msg = jstr(rec.UUID)
		}
		e := [5]int64{u.InputTokens, u.CacheRead, w5m, w1h, u.OutputTokens}
		if prev, ok := responses[k]; !ok || e[4] > prev[4] {
			responses[k] = e
		}
	}
	for _, e := range responses {
		f.Input += e[0]
		f.CacheRead += e[1]
		f.Create5m += e[2]
		f.Create1h += e[3]
		f.Output += e[4]
	}
	f.Turns = int64(len(responses))
	if model != "" {
		f.Model = &model
	}
	if seenTS {
		f.FirstTS, f.LastTS = &first, &last
	}
	if dispatch != nil {
		f.Dispatch = *dispatch
	}
	return f
}

func agentID(path string) string {
	base := filepath.Base(path)
	return strings.TrimSuffix(strings.TrimPrefix(base, "agent-"), ".jsonl")
}

// userText is the dispatch candidate of a user line: the content string, or
// the text blocks of a content list joined with " ".
func userText(raw json.RawMessage) string {
	var m struct {
		Content json.RawMessage `json:"content"`
	}
	if json.Unmarshal(raw, &m) != nil {
		return ""
	}
	c := bytes.TrimSpace(m.Content)
	switch {
	case len(c) == 0:
		return ""
	case c[0] == '"':
		return jstr(c)
	case c[0] == '[':
		var blocks []struct {
			Type string          `json:"type"`
			Text json.RawMessage `json:"text"`
		}
		if json.Unmarshal(c, &blocks) != nil {
			return ""
		}
		parts := make([]string, 0, len(blocks))
		for _, b := range blocks {
			if b.Type == "text" {
				parts = append(parts, jstr(b.Text))
			}
		}
		return strings.Join(parts, " ")
	}
	return ""
}

// Prompts Claude Code injects (not typed by the human) carry these markers.
var metaPrefixes = []string{
	"<local-command-caveat>", "<command-name>", "<command-message>",
	"<command-args>", "<command-stdout>", "<command-stderr>",
	"<system-reminder>", "<ide_opened_file>", "<ide_selection>",
	"<user-prompt-submit-hook>", "Caveat:",
}

func isMetaText(s string) bool {
	s = strings.TrimLeftFunc(s, unicode.IsSpace)
	for _, p := range metaPrefixes {
		if strings.HasPrefix(s, p) {
			return true
		}
	}
	return false
}

// head is txt[:n], counted in runes like Python's str slice.
func head(s string, n int) string {
	if utf8.RuneCountInString(s) <= n {
		return s
	}
	return string([]rune(s)[:n])
}

// jstr is the string form of a JSON field, "" when it is not a string.
func jstr(raw json.RawMessage) string {
	var s string
	if json.Unmarshal(raw, &s) != nil {
		return ""
	}
	return s
}

// truthy mirrors Python's `if obj.get(field)` on a raw JSON value.
func truthy(raw json.RawMessage) bool {
	switch string(bytes.TrimSpace(raw)) {
	case "", "null", `""`, "0", "0.0", "false", "[]", "{}":
		return false
	}
	return true
}

func str(p *string) string {
	if p == nil {
		return ""
	}
	return *p
}

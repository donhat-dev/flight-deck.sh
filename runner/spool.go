package main

import (
	"encoding/json"
	"log"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"
)

// Command is one file in cmd/. Odoo writes it to a dot-prefixed temporary
// name in the same directory and renames it, so a name that starts with "."
// is never read.
type Command struct {
	ID        string      `json:"cmd_id"`
	Seq       int64       `json:"seq"`
	Op        string      `json:"op"`
	SessionID string      `json:"session_id"`
	ExpiresAt string      `json:"expires_at,omitempty"`
	Args      CommandArgs `json:"args"`
	file      string
}

type CommandArgs struct {
	Cwd                string   `json:"cwd,omitempty"`
	Resume             bool     `json:"resume,omitempty"`
	Text               string   `json:"text,omitempty"`
	PermissionMode     string   `json:"permission_mode,omitempty"`
	AllowedTools       []string `json:"allowed_tools,omitempty"`
	Model              string   `json:"model,omitempty"`
	AppendSystemPrompt string   `json:"append_system_prompt,omitempty"`
	// Resume even when an interactive process holds the session. Two writers
	// on one session lose turns, so this is off unless asked for.
	AllowLive bool `json:"allow_live,omitempty"`
	// decide: answer to a can_use_tool request.
	RequestID    string         `json:"request_id,omitempty"`
	Behavior     string         `json:"behavior,omitempty"` // allow | deny
	Scope        string         `json:"scope,omitempty"`    // once | session | always
	Message      string         `json:"message,omitempty"`
	UpdatedInput map[string]any `json:"updated_input,omitempty"`
	// set_mode: the permission mode to switch the running process to.
	Mode string `json:"mode,omitempty"`
	// How hard the model works on a turn: low, medium, high, xhigh, max.
	Effort string `json:"effort,omitempty"`
}

// Event is one line of events.jsonl. Kinds: ack, state, result, permission,
// decision, mode.
type Event struct {
	TS        string `json:"ts"`
	Kind      string `json:"kind"`
	SessionID string `json:"session_id,omitempty"`
	CmdID     string `json:"cmd_id,omitempty"`
	State     string `json:"state,omitempty"`
	Error     string `json:"error,omitempty"`
	Payload   any    `json:"payload,omitempty"`
}

type SessionState struct {
	SessionID string `json:"session_id"`
	PID       int    `json:"pid"`
	State     string `json:"state"`
	Cwd       string `json:"cwd"`
	Since     string `json:"since"`
	Last      string `json:"last"`
}

type Spool struct {
	root string
	mu   sync.Mutex
	seen map[string]bool
}

func OpenSpool(root string) (*Spool, error) {
	for _, d := range []string{"cmd", "done", "state"} {
		if err := os.MkdirAll(filepath.Join(root, d), 0o775); err != nil {
			return nil, err
		}
	}
	// Whatever state a previous runner left describes processes that died
	// with it.
	stale, _ := filepath.Glob(filepath.Join(root, "state", "*.json"))
	for _, p := range stale {
		os.Remove(p)
	}
	return &Spool{root: root, seen: map[string]bool{}}, nil
}

// Pending returns the commands in cmd/, oldest first by file name. The name
// carries Odoo's sequence number, so file order is command order.
func (s *Spool) Pending() []Command {
	entries, err := os.ReadDir(filepath.Join(s.root, "cmd"))
	if err != nil {
		log.Printf("cmd/: %v", err)
		return nil
	}
	var names []string
	for _, e := range entries {
		n := e.Name()
		if e.IsDir() || strings.HasPrefix(n, ".") || !strings.HasSuffix(n, ".json") {
			continue
		}
		names = append(names, n)
	}
	sort.Strings(names)
	var out []Command
	for _, n := range names {
		path := filepath.Join(s.root, "cmd", n)
		raw, err := os.ReadFile(path)
		if err != nil {
			continue
		}
		var c Command
		if err := json.Unmarshal(raw, &c); err != nil {
			log.Printf("%s: bad command: %v", n, err)
			s.Emit(Event{Kind: "ack", Error: "unreadable command: " + err.Error()})
			s.finish(path)
			continue
		}
		c.file = path
		out = append(out, c)
	}
	return out
}

// Seen is true when a command id was already handled by this process or its
// file already sits in done/, which is what a crash between handling and
// moving leaves behind.
func (s *Spool) Seen(c Command) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.seen[c.ID] {
		return true
	}
	_, err := os.Stat(filepath.Join(s.root, "done", filepath.Base(c.file)))
	return err == nil
}

// Done moves the file to done/ and remembers the id.
func (s *Spool) Done(c Command) {
	s.mu.Lock()
	if len(s.seen) > 10000 {
		s.seen = map[string]bool{}
	}
	s.seen[c.ID] = true
	s.mu.Unlock()
	s.finish(c.file)
}

func (s *Spool) finish(path string) {
	if err := os.Rename(path, filepath.Join(s.root, "done", filepath.Base(path))); err != nil {
		log.Printf("done/: %v", err)
	}
}

// Emit appends one line. A single small write with O_APPEND lands whole, so
// a reader tailing the file never sees half a line.
func (s *Spool) Emit(e Event) {
	if e.TS == "" {
		e.TS = time.Now().UTC().Format(time.RFC3339Nano)
	}
	line, err := json.Marshal(e)
	if err != nil {
		return
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	f, err := os.OpenFile(filepath.Join(s.root, "events.jsonl"), os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o664)
	if err != nil {
		log.Printf("events.jsonl: %v", err)
		return
	}
	defer f.Close()
	f.Write(append(line, '\n'))
}

func (s *Spool) SetState(st SessionState) {
	raw, _ := json.Marshal(st)
	dir := filepath.Join(s.root, "state")
	tmp := filepath.Join(dir, "."+st.SessionID+".tmp")
	if err := os.WriteFile(tmp, raw, 0o664); err != nil {
		return
	}
	os.Rename(tmp, filepath.Join(dir, st.SessionID+".json"))
}

func (s *Spool) ClearState(sessionID string) {
	os.Remove(filepath.Join(s.root, "state", sessionID+".json"))
}

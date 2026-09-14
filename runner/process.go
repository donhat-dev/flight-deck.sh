package main

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"syscall"
	"time"
)

// Environment names never passed to a claude process. Inside the Odoo
// container the entrypoint's env carries the database role and password.
var envDeny = []string{"PASSWORD", "PGPASSWORD", "FD_LEDGER_PASSWORD", "FD_LEDGER_USER"}

type session struct {
	id    string
	cwd   string
	cmd   *exec.Cmd
	stdin io.WriteCloser
	state string
	since time.Time
	last  time.Time
	done  chan struct{}
	mu    sync.Mutex
}

type Manager struct {
	claude string
	sp     *Spool
	idle   time.Duration
	// File mode applied to a session's transcript after each turn, so a
	// reader with an ACL entry on the tree can open it; 0 leaves it alone.
	// claude creates the file 0600, and an ACL entry is masked by the group
	// bits, so the default ACL alone never reaches a new file.
	transcriptMode os.FileMode
	// How long a permission request may wait for an answer from Odoo before
	// it is denied. The CLI's turn is parked for the whole wait.
	permTimeout time.Duration
	mu          sync.Mutex
	sessions    map[string]*session
	pmu         sync.Mutex
	pending     map[string]*permRequest
}

func NewManager(claude string, sp *Spool, idle time.Duration, transcriptMode os.FileMode,
	permTimeout time.Duration) *Manager {
	return &Manager{claude: claude, sp: sp, idle: idle, transcriptMode: transcriptMode,
		permTimeout: permTimeout,
		sessions:    map[string]*session{},
		pending:     map[string]*permRequest{}}
}

// transcriptPath is where claude writes this session. A resumed session keeps
// its original file whatever the new cwd, so an existing file wins; a new one
// lands under <config>/projects/<cwd with "/" and "." turned into "-">/.
func transcriptPath(cwd, id string) string {
	root := os.Getenv("CLAUDE_CONFIG_DIR")
	if root == "" {
		home, _ := os.UserHomeDir()
		root = filepath.Join(home, ".claude")
	}
	if found, _ := filepath.Glob(filepath.Join(root, "projects", "*", id+".jsonl")); len(found) > 0 {
		return found[0]
	}
	slug := strings.NewReplacer("/", "-", ".", "-").Replace(cwd)
	return filepath.Join(root, "projects", slug, id+".jsonl")
}

// livePID asks claude which sessions are open interactively and returns the
// pid holding this one, 0 when none. The runner's own stream-json processes
// are listed as interactive too, so they are excluded by pid. An error means
// the check itself failed, and the caller must not read that as "not live".
func (m *Manager) livePID(id string) (int, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	out, err := exec.CommandContext(ctx, m.claude, "agents", "--json").Output()
	if err != nil {
		return 0, fmt.Errorf("claude agents: %w", err)
	}
	var agents []struct {
		SessionID string `json:"sessionId"`
		Kind      string `json:"kind"`
		PID       int    `json:"pid"`
	}
	if err := json.Unmarshal(out, &agents); err != nil {
		return 0, fmt.Errorf("claude agents: %w", err)
	}
	m.mu.Lock()
	own := map[int]bool{}
	for _, s := range m.sessions {
		if s.cmd.Process != nil {
			own[s.cmd.Process.Pid] = true
		}
	}
	m.mu.Unlock()
	for _, a := range agents {
		if a.SessionID == id && a.Kind == "interactive" && a.PID != 0 && !own[a.PID] {
			return a.PID, nil
		}
	}
	return 0, nil
}

func (m *Manager) shareTranscript(s *session) {
	if m.transcriptMode == 0 {
		return
	}
	path := transcriptPath(s.cwd, s.id)
	if err := os.Chmod(path, m.transcriptMode); err != nil && !os.IsNotExist(err) {
		log.Printf("%s chmod: %v", s.id[:8], err)
	}
}

// Handle runs one command and always acknowledges it, error or not, so Odoo
// can close the command row either way.
func (m *Manager) Handle(c Command) {
	if m.sp.Seen(c) {
		m.sp.Done(c)
		return
	}
	var err error
	switch c.Op {
	case "start":
		err = m.start(c)
	case "send":
		err = m.send(c)
	case "interrupt":
		err = m.signal(c.SessionID, syscall.SIGINT)
	case "stop":
		err = m.stop(c.SessionID)
	case "decide":
		err = m.Decide(c)
	case "set_mode":
		err = m.SetMode(c)
	case "set_model":
		err = m.SetModel(c)
	case "set_effort":
		err = m.SetEffort(c)
	default:
		err = fmt.Errorf("unknown op %q", c.Op)
	}
	ev := Event{Kind: "ack", CmdID: c.ID, SessionID: c.SessionID}
	if err != nil {
		ev.Error = err.Error()
		log.Printf("%s %s: %v", c.Op, c.SessionID, err)
	}
	m.sp.Emit(ev)
	m.sp.Done(c)
}

func (m *Manager) get(id string) *session {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.sessions[id]
}

func (m *Manager) start(c Command) error {
	if c.SessionID == "" {
		return errors.New("start needs a session_id")
	}
	if s := m.get(c.SessionID); s != nil {
		m.setState(s, s.state)
		return nil
	}
	if c.Args.Resume && !c.Args.AllowLive {
		pid, err := m.livePID(c.SessionID)
		if err != nil {
			return fmt.Errorf("cannot verify the session is not open elsewhere (%v); refusing", err)
		}
		if pid != 0 {
			return fmt.Errorf("session is open in an interactive process (pid %d); writing to it can lose turns", pid)
		}
	}
	cwd := c.Args.Cwd
	if cwd == "" {
		cwd, _ = os.UserHomeDir()
	}
	if err := os.MkdirAll(cwd, 0o775); err != nil {
		return fmt.Errorf("cwd: %w", err)
	}
	args := []string{
		"-p",
		"--input-format", "stream-json",
		"--output-format", "stream-json",
		"--verbose",
		// Whatever the mode does not settle is asked of this runner over the
		// control channel, and from there of a person in Odoo.
		"--permission-prompt-tool", "stdio",
	}
	if c.Args.Resume {
		args = append(args, "--resume", c.SessionID)
	} else {
		args = append(args, "--session-id", c.SessionID)
	}
	mode := c.Args.PermissionMode
	if mode == "" {
		mode = "dontAsk"
	}
	args = append(args, "--permission-mode", mode)
	if c.Args.Model != "" {
		args = append(args, "--model", c.Args.Model)
	}
	if c.Args.Effort != "" {
		args = append(args, "--effort", c.Args.Effort)
	}
	if len(c.Args.AllowedTools) > 0 {
		args = append(args, "--allowedTools", strings.Join(c.Args.AllowedTools, ","))
	}
	if c.Args.AppendSystemPrompt != "" {
		args = append(args, "--append-system-prompt", c.Args.AppendSystemPrompt)
	}

	cmd := exec.Command(m.claude, args...)
	cmd.Dir = cwd
	cmd.Env = scrubEnv(os.Environ())
	cmd.Stderr = &prefixWriter{prefix: c.SessionID[:8] + " stderr: "}
	stdin, err := cmd.StdinPipe()
	if err != nil {
		return err
	}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return err
	}
	if err := cmd.Start(); err != nil {
		return fmt.Errorf("spawn: %w", err)
	}
	s := &session{id: c.SessionID, cwd: cwd, cmd: cmd, stdin: stdin,
		since: time.Now(), last: time.Now(), done: make(chan struct{})}
	m.mu.Lock()
	m.sessions[s.id] = s
	m.mu.Unlock()
	// A stream-json process waits on stdin from the start; its first
	// system/init frame only comes with the first turn, so spawned is ready.
	m.setState(s, "idle")
	go m.read(s, stdout)
	go m.wait(s)
	return nil
}

// frame is the part of a stream-json line the runner acts on.
type frame struct {
	Type       string  `json:"type"`
	Subtype    string  `json:"subtype"`
	TotalCost  float64 `json:"total_cost_usd"`
	IsError    bool    `json:"is_error"`
	NumTurns   int     `json:"num_turns"`
	DurationMS int     `json:"duration_ms"`
}

func (m *Manager) read(s *session, r io.Reader) {
	sc := bufio.NewScanner(r)
	sc.Buffer(make([]byte, 1<<20), 64<<20)
	for sc.Scan() {
		var f frame
		if err := json.Unmarshal(sc.Bytes(), &f); err != nil {
			continue
		}
		if f.Type == "control_request" {
			m.handleControl(s, sc.Bytes())
			continue
		}
		switch f.Type {
		case "system":
			// The file is created as the turn starts, mode 0600. Nothing can
			// read it until it is shared, and this is the first frame after
			// it appears.
			m.shareTranscript(s)
		case "assistant":
			if s.state != "busy" {
				m.setState(s, "busy")
			}
		case "result":
			s.last = time.Now()
			m.sp.Emit(Event{Kind: "result", SessionID: s.id, Payload: map[string]any{
				"total_cost_usd": f.TotalCost,
				"is_error":       f.IsError,
				"num_turns":      f.NumTurns,
				"duration_ms":    f.DurationMS,
				"subtype":        f.Subtype,
			}})
			m.shareTranscript(s)
			m.setState(s, "idle")
		}
	}
}

func (m *Manager) wait(s *session) {
	err := s.cmd.Wait()
	m.mu.Lock()
	delete(m.sessions, s.id)
	m.mu.Unlock()
	ev := Event{Kind: "state", SessionID: s.id, State: "stopped"}
	if err != nil {
		ev.Error = err.Error()
	}
	s.state = "stopped"
	m.dropPermissions(s)
	m.sp.ClearState(s.id)
	m.sp.Emit(ev)
	close(s.done)
	log.Printf("%s exited: %v", s.id[:8], err)
}

func (m *Manager) send(c Command) error {
	if c.ExpiresAt != "" {
		if t, err := time.Parse(time.RFC3339, c.ExpiresAt); err == nil && time.Now().After(t) {
			return errors.New("expired before the runner saw it")
		}
	}
	s := m.get(c.SessionID)
	if s == nil {
		return errors.New("session is not running")
	}
	line, err := json.Marshal(map[string]any{
		"type": "user",
		"message": map[string]any{
			"role":    "user",
			"content": []map[string]string{{"type": "text", "text": c.Args.Text}},
		},
	})
	if err != nil {
		return err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if _, err := s.stdin.Write(append(line, '\n')); err != nil {
		return fmt.Errorf("stdin: %w", err)
	}
	s.last = time.Now()
	m.setState(s, "busy")
	return nil
}

func (m *Manager) signal(id string, sig syscall.Signal) error {
	s := m.get(id)
	if s == nil {
		return errors.New("session is not running")
	}
	return s.cmd.Process.Signal(sig)
}

// stop closes stdin first, which is how a stream-json session ends on its
// own; signals follow only if the process does not leave.
func (m *Manager) stop(id string) error {
	s := m.get(id)
	if s == nil {
		return nil
	}
	s.mu.Lock()
	s.stdin.Close()
	s.mu.Unlock()
	select {
	case <-s.done:
		return nil
	case <-time.After(5 * time.Second):
	}
	s.cmd.Process.Signal(syscall.SIGTERM)
	select {
	case <-s.done:
		return nil
	case <-time.After(5 * time.Second):
	}
	return s.cmd.Process.Kill()
}

func (m *Manager) StopAll() {
	m.mu.Lock()
	ids := make([]string, 0, len(m.sessions))
	for id := range m.sessions {
		ids = append(ids, id)
	}
	m.mu.Unlock()
	for _, id := range ids {
		m.stop(id)
	}
}

// Reap stops sessions that sat idle longer than the limit, and denies
// permission requests nobody answered.
func (m *Manager) Reap() {
	for range time.Tick(5 * time.Second) {
		m.expirePermissions()
		m.mu.Lock()
		var stale []string
		for id, s := range m.sessions {
			if s.state == "idle" && time.Since(s.last) > m.idle {
				stale = append(stale, id)
			}
		}
		m.mu.Unlock()
		for _, id := range stale {
			log.Printf("%s idle, stopping", id[:8])
			m.stop(id)
		}
	}
}

func (m *Manager) setState(s *session, state string) {
	s.state = state
	// The file appears with the first turn and claude creates it 0600, so the
	// share is retried on every state change until it exists.
	m.shareTranscript(s)
	now := time.Now().UTC().Format(time.RFC3339Nano)
	pid := 0
	if s.cmd.Process != nil {
		pid = s.cmd.Process.Pid
	}
	m.sp.SetState(SessionState{SessionID: s.id, PID: pid, State: state, Cwd: s.cwd,
		Since: s.since.UTC().Format(time.RFC3339Nano), Last: now})
	m.sp.Emit(Event{Kind: "state", SessionID: s.id, State: state, Payload: map[string]any{"pid": pid}})
}

func scrubEnv(env []string) []string {
	out := make([]string, 0, len(env))
	for _, kv := range env {
		name := kv
		if i := strings.IndexByte(kv, '='); i >= 0 {
			name = kv[:i]
		}
		drop := false
		for _, d := range envDeny {
			if name == d {
				drop = true
				break
			}
		}
		if !drop {
			out = append(out, kv)
		}
	}
	return out
}

type prefixWriter struct{ prefix string }

func (w *prefixWriter) Write(p []byte) (int, error) {
	for _, line := range strings.Split(strings.TrimRight(string(p), "\n"), "\n") {
		if line != "" {
			log.Print(w.prefix, line)
		}
	}
	return len(p), nil
}

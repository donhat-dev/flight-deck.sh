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
	id            string
	cwd           string
	cmd           *exec.Cmd
	stdin         io.WriteCloser
	state         string
	since         time.Time
	last          time.Time
	done          chan struct{}
	ready         chan struct{}
	readyOnce     sync.Once
	mu            sync.Mutex
	meta          sync.Mutex
	startTicks    uint64
	exeIdentity   string
	resourceAgent bool
}

func (s *session) runtimeState() string {
	s.meta.Lock()
	defer s.meta.Unlock()
	return s.state
}

func (s *session) idleFor() time.Duration {
	s.meta.Lock()
	defer s.meta.Unlock()
	return time.Since(s.last)
}

type Manager struct {
	claude string
	sp     *Spool
	idle   time.Duration
	// How long a permission request may wait for an answer from Odoo before
	// it is denied. The CLI's turn is parked for the whole wait.
	permTimeout time.Duration
	mu          sync.Mutex
	sessions    map[string]*session
	pmu         sync.Mutex
	pending     map[string]*permRequest
	deltas      *DeltaWriter
	res         *resourceService
}

func NewManager(claude string, sp *Spool, idle time.Duration,
	permTimeout time.Duration) *Manager {
	m := &Manager{claude: claude, sp: sp, idle: idle,
		permTimeout: permTimeout,
		sessions:    map[string]*session{},
		pending:     map[string]*permRequest{},
		deltas:      NewDeltaWriter(sp)}
	m.res = newResourceService(m, sp)
	return m
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

// Handle runs one command and always acknowledges it, error or not, so Odoo
// can close the command row either way.
func (m *Manager) Handle(c Command) {
	if m.sp.Seen(c) {
		m.sp.Done(c)
		return
	}
	var err error
	if s := m.get(c.SessionID); s != nil && s.resourceAgent {
		switch c.Op {
		case "set_mode", "set_model", "set_effort":
			err = fmt.Errorf("the resource agent's profile is fixed")
		case "send":
			if s.runtimeState() == "busy" {
				err = fmt.Errorf("the resource agent is mid-turn")
			}
		}
	}
	if err == nil {
		switch c.Op {
		case "start":
			if c.Args.ResourceAgent {
				err = m.ensureAgent(c)
			} else {
				err = m.start(c)
			}
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
		case "usage":
			// Seconds of CLI time; the command loop must not wait on it. The
			// report arrives as its own `usage` event after this ack.
			go m.reportUsage(c.ID)
		case "resource_scan":
			m.res.ScanNow(c)
		case "resource_apply":
			err = m.res.Apply(c)
		default:
			err = fmt.Errorf("unknown op %q", c.Op)
		}
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
	if known, err := m.res.agentRegistered(c.SessionID); err != nil {
		return err
	} else if known && !c.Args.ResourceAgent {
		return errors.New("a management session must start with its resource profile")
	}
	if s := m.get(c.SessionID); s != nil {
		m.setState(s, s.runtimeState())
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
		// Without this, the CLI only ever emits a full "assistant" frame per
		// completed block; delta.go has nothing to coalesce.
		"--include-partial-messages",
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
	if !c.Args.ResourceAgent {
		if c.Args.Model != "" {
			args = append(args, "--model", c.Args.Model)
		}
		if c.Args.Effort != "" {
			args = append(args, "--effort", c.Args.Effort)
		}
		if len(c.Args.AllowedTools) > 0 {
			args = append(args, "--allowedTools", strings.Join(c.Args.AllowedTools, ","))
		}
	}
	if c.Args.AppendSystemPrompt != "" {
		args = append(args, "--append-system-prompt", c.Args.AppendSystemPrompt)
	}
	if c.Args.ResourceAgent {
		exe, err := os.Executable()
		if err != nil {
			return fmt.Errorf("resource agent helper path: %w", err)
		}
		mcpConfig := map[string]any{
			"mcpServers": map[string]any{
				"flightdeck_resources": map[string]any{
					"type":    "stdio",
					"command": exe,
					"args":    []string{"-resource-mcp", "-resource-sock", m.res.socket, "-resource-token", m.res.tokenPath()},
				},
			},
		}
		raw, err := json.Marshal(mcpConfig)
		if err != nil {
			return err
		}
		confPath := filepath.Join(m.res.dir, "mcp.json")
		if err := writePrivateAtomic(confPath, raw); err != nil {
			return fmt.Errorf("resource agent mcp config: %w", err)
		}
		for i := 0; i+1 < len(args); i++ {
			if args[i] == "--permission-mode" {
				args = append(args[:i], args[i+2:]...)
				i--
			}
		}
		args = append(args, "--permission-mode", "dontAsk",
			"--mcp-config", confPath,
			"--strict-mcp-config",
			"--setting-sources", "",
			"--disable-slash-commands",
			"--tools", "",
			"--allowedTools", "mcp__flightdeck_resources__list_resources,mcp__flightdeck_resources__inspect_resource,mcp__flightdeck_resources__propose_action",
		)
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
		since: time.Now(), last: time.Now(), done: make(chan struct{}),
		ready: make(chan struct{})}
	s.resourceAgent = c.Args.ResourceAgent
	s.startTicks, s.exeIdentity = captureResourceIdentity("/proc", cmd.Process.Pid)
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

// frame is the part of a stream-json line the runner acts on. session_id and
// parent_tool_use_id ride on "assistant" and "stream_event" frames (never on
// "result", which carries its own session_id but this runner already knows
// the session from s.id); event is only present on "stream_event".
type frame struct {
	Type                string           `json:"type"`
	Subtype             string           `json:"subtype"`
	SessionID           string           `json:"session_id"`
	ParentToolUseID     string           `json:"parent_tool_use_id"`
	TotalCost           float64          `json:"total_cost_usd"`
	IsError             bool             `json:"is_error"`
	NumTurns            int              `json:"num_turns"`
	DurationMS          int              `json:"duration_ms"`
	FirstContentFrameMS int              `json:"first_content_frame_ms"`
	Event               *streamEventBody `json:"event"`
}

func (m *Manager) read(s *session, r io.Reader) {
	sc := bufio.NewScanner(r)
	sc.Buffer(make([]byte, 1<<20), 64<<20)
	for sc.Scan() {
		var f frame
		if err := json.Unmarshal(sc.Bytes(), &f); err != nil {
			continue
		}
		s.readyOnce.Do(func() { close(s.ready) })
		if f.Type == "control_request" {
			m.handleControl(s, sc.Bytes())
			continue
		}
		if f.Type == "control_response" {
			m.res.handleControlResponse(sc.Bytes(), s)
			continue
		}
		switch f.Type {
		case "assistant":
			if s.runtimeState() != "busy" {
				m.setState(s, "busy")
			}
			// The authoritative close of whatever block was streaming: see
			// delta.go's doc comment for why this, not content_block_stop.
			m.deltas.OnAssistant(s.id, f.SessionID, f.ParentToolUseID)
		case "system":
			if f.Subtype == "init" {
				s.meta.Lock()
				s.startTicks, s.exeIdentity = captureResourceIdentity("/proc", s.cmd.Process.Pid)
				s.meta.Unlock()
			}
		case "stream_event":
			m.deltas.OnStreamEvent(s.id, f.SessionID, f.ParentToolUseID, f.Event)
		case "result":
			s.meta.Lock()
			s.last = time.Now()
			s.meta.Unlock()
			// Flush before the event that tells Odoo the turn is done, so a
			// reader never sees "idle" ahead of the text that led to it.
			m.deltas.FlushSession(s.id)
			m.sp.Emit(Event{Kind: "result", SessionID: s.id, Payload: map[string]any{
				"total_cost_usd":         f.TotalCost,
				"is_error":               f.IsError,
				"num_turns":              f.NumTurns,
				"duration_ms":            f.DurationMS,
				"subtype":                f.Subtype,
				"first_content_frame_ms": f.FirstContentFrameMS,
			}})
			// The turn is over: nothing left streaming belongs to it.
			m.deltas.DropSession(s.id)
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
	s.meta.Lock()
	s.state = "stopped"
	s.meta.Unlock()
	m.dropPermissions(s)
	m.deltas.DropSession(s.id)
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
	s.meta.Lock()
	s.last = time.Now()
	s.meta.Unlock()
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
			if s.resourceAgent {
				continue
			}
			if s.runtimeState() == "idle" && s.idleFor() > m.idle {
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
	s.meta.Lock()
	s.state = state
	s.meta.Unlock()
	now := time.Now().UTC().Format(time.RFC3339Nano)
	pid := 0
	if s.cmd.Process != nil {
		pid = s.cmd.Process.Pid
	}
	m.sp.SetState(SessionState{SessionID: s.id, PID: pid, State: state, Cwd: s.cwd,
		Since: s.since.UTC().Format(time.RFC3339Nano), Last: now})
	// Flush before the event that tells Odoo the state changed, so a reader
	// never sees e.g. "busy" ahead of the text a stream had already snapshot.
	m.deltas.FlushSession(s.id)
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

package main

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func writeFakeClaude(t *testing.T, dir, body string) string {
	t.Helper()
	path := filepath.Join(dir, "claude")
	if err := os.WriteFile(path, []byte(body), 0o755); err != nil {
		t.Fatal(err)
	}
	return path
}

func fakeClaudeScript(logPath string) string {
	return fmt.Sprintf(`#!/bin/sh
printf 'ARG:%%s\n' "$@" >> %q
if [ "$1" = "agents" ]; then echo '[]'; exit 0; fi
while IFS= read -r line; do
  case "$line" in
    *'"control_request"'*)
      rid=$(printf '%%s' "$line" | sed -n 's/.*"request_id":"\([^"]*\)".*/\1/p')
      printf '{"type":"control_response","response":{"subtype":"success","request_id":"%%s","response":{"mcpServers":[{"name":"srv","status":"connected","type":"stdio"}]}}}\n' "$rid"
      ;;
  esac
done
`, logPath)
}

func fakeClaudeSilent(logPath string) string {
	return fmt.Sprintf(`#!/bin/sh
printf 'ARG:%%s\n' "$@" >> %q
if [ "$1" = "agents" ]; then echo '[]'; exit 0; fi
while IFS= read -r line; do :; done
`, logPath)
}

func testManager(t *testing.T, claude string) *Manager {
	t.Helper()
	sp, err := OpenSpool(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	m := NewManager(claude, sp, time.Hour, time.Second)
	m.res = newResourceService(m, sp)
	return m
}

func TestAgentStartupProfileArgs(t *testing.T) {
	dir := t.TempDir()
	logPath := filepath.Join(dir, "argv.log")
	m := testManager(t, writeFakeClaude(t, dir, fakeClaudeScript(logPath)))
	id := newUUID()
	err := m.ensureAgent(Command{SessionID: id, Args: CommandArgs{
		ResourceAgent: true, Model: "opus", PermissionMode: "bypassPermissions",
		AllowedTools: []string{"Bash"}, Cwd: dir,
	}})
	if err != nil {
		t.Fatalf("ensureAgent: %v", err)
	}
	defer m.stop(id)
	raw, err := os.ReadFile(logPath)
	if err != nil {
		t.Fatal("fake claude never spawned")
	}
	lines := strings.Split(string(raw), "\n")
	have := func(want string) bool {
		for _, l := range lines {
			if l == "ARG:"+want {
				return true
			}
		}
		return false
	}
	for i, l := range lines {
		if l == "ARG:--permission-mode" && i+1 < len(lines) && lines[i+1] != "ARG:dontAsk" {
			t.Fatalf("agent permission mode must be dontAsk\n%s", raw)
		}
	}
	for _, want := range []string{
		"--session-id", id, "dontAsk",
		"--tools", "--strict-mcp-config", "--setting-sources",
		"--disable-slash-commands",
		"mcp__flightdeck_resources__list_resources,mcp__flightdeck_resources__inspect_resource,mcp__flightdeck_resources__propose_action",
		"--mcp-config",
	} {
		if !have(want) {
			t.Fatalf("argv missing %q\n%s", want, raw)
		}
	}
	for _, banned := range []string{"--resume", "opus", "bypassPermissions", "Bash"} {
		if have(banned) {
			t.Fatalf("argv must not contain %q\n%s", banned, raw)
		}
	}
	ids, err := m.res.loadAgentIDs()
	if err != nil || !ids[id] {
		t.Fatal("management UUID must be registered durably")
	}
}

func TestAgentResumeUsesSameUUID(t *testing.T) {
	dir := t.TempDir()
	logPath := filepath.Join(dir, "argv.log")
	m := testManager(t, writeFakeClaude(t, dir, fakeClaudeScript(logPath)))
	id := newUUID()
	if err := m.ensureAgent(Command{SessionID: id, Args: CommandArgs{
		ResourceAgent: true, Resume: true, Cwd: dir,
	}}); err != nil {
		t.Fatalf("ensureAgent resume: %v", err)
	}
	defer m.stop(id)
	raw, _ := os.ReadFile(logPath)
	found := false
	lines := strings.Split(string(raw), "\n")
	for i, l := range lines {
		if l == "ARG:--resume" && i+1 < len(lines) && lines[i+1] == "ARG:"+id {
			found = true
		}
	}
	if !found {
		t.Fatalf("resume must use the same UUID\n%s", raw)
	}
}

func TestAgentHandshakeFailureStopsOwnProcess(t *testing.T) {
	dir := t.TempDir()
	logPath := filepath.Join(dir, "argv.log")
	m := testManager(t, writeFakeClaude(t, dir, fakeClaudeSilent(logPath)))
	id := newUUID()
	err := m.ensureAgent(Command{SessionID: id, Args: CommandArgs{
		ResourceAgent: true, Cwd: dir,
	}})
	if err == nil {
		defer m.stop(id)
		t.Fatal("a CLI that never answers initialize must fail ensureAgent")
	}
	if s := m.get(id); s != nil {
		t.Fatal("the failed session must be stopped, not left half-started")
	}
	ids, _ := m.res.loadAgentIDs()
	if !ids[id] {
		t.Fatal("the profile registration must survive the failed start")
	}
}

func TestNonAgentStartOnManagementUUIDRefused(t *testing.T) {
	dir := t.TempDir()
	m := testManager(t, writeFakeClaude(t, dir, fakeClaudeScript(filepath.Join(dir, "x.log"))))
	id := newUUID()
	if err := m.res.registerAgent(id); err != nil {
		t.Fatal(err)
	}
	if err := m.start(Command{SessionID: id, Args: CommandArgs{Cwd: dir}}); err == nil {
		t.Fatal("a plain start under a management UUID must be refused")
	}
}

func TestLiveNonAgentSessionBlocksAgentReuse(t *testing.T) {
	dir := t.TempDir()
	m := testManager(t, writeFakeClaude(t, dir, fakeClaudeScript(filepath.Join(dir, "x.log"))))
	id := newUUID()
	if err := m.start(Command{SessionID: id, Args: CommandArgs{Cwd: dir}}); err != nil {
		t.Fatalf("start: %v", err)
	}
	defer m.stop(id)
	if err := m.res.registerAgent(id); err != nil {
		t.Fatal(err)
	}
	if err := m.ensureAgent(Command{SessionID: id, Args: CommandArgs{
		ResourceAgent: true, Cwd: dir,
	}}); err == nil {
		t.Fatal("an existing non-agent session under a management UUID must be refused")
	}
}

func TestMalformedAgentRegistryFailsClosed(t *testing.T) {
	dir := t.TempDir()
	m := testManager(t, writeFakeClaude(t, dir, fakeClaudeScript(filepath.Join(dir, "x.log"))))
	if err := os.WriteFile(m.res.agentIDsPath(), []byte("{broken"), 0o600); err != nil {
		t.Fatal(err)
	}
	id := newUUID()
	if err := m.ensureAgent(Command{SessionID: id, Args: CommandArgs{
		ResourceAgent: true, Cwd: dir,
	}}); err == nil {
		t.Fatal("a malformed registry must fail closed")
	}
}

func TestExpiryLapseDuringScanBlocksSignal(t *testing.T) {
	mgr := &Manager{sessions: map[string]*session{}}
	cmd := fakeClaude(t, false)
	registerFixtureSession(mgr, cmd)
	r := testServiceWith(t, mgr)
	snap := r.Scan()
	var row *ResourceRow
	for i := range snap.Processes {
		if snap.Processes[i].PID == cmd.Process.Pid {
			row = &snap.Processes[i]
		}
	}
	if row == nil || !row.Manageable {
		t.Skip("fixture not manageable")
	}
	c := applyStopCommand(row)
	c.ExpiresAt = time.Now().Add(300 * time.Millisecond).UTC().Format(time.RFC3339)
	r.beforeSideEffect = func() { time.Sleep(600 * time.Millisecond) }
	if err := r.Apply(c); err == nil {
		t.Fatal("an action that expired during the scan must fail")
	}
	payload := lastActionEvent(t, r)
	if payload["state"] != "failed" {
		t.Fatalf("expired action emitted %+v", payload)
	}
	if _, err := os.Stat("/proc/" + fmt.Sprint(cmd.Process.Pid)); err != nil {
		t.Fatal("an expired action must not signal")
	}
	r.beforeSideEffect = nil
}

func TestExpiredCommandReplaysRecordedOutcome(t *testing.T) {
	r := testService(t)
	id := newUUID()
	past := time.Now().Add(-time.Hour).UTC().Format(time.RFC3339)
	expMap := map[string]any{
		"key": "k", "pid": float64(10), "start_ticks": "20",
		"namespace": "pid:[1]", "uid": float64(0), "exe_identity": "1:2",
	}
	claim := &resourceClaim{ActionID: id, Kind: "stop_process", Key: "k",
		Expected: expMap, ExpiresAt: past, State: "completed", Result: "done",
		ClaimedAt: past, FinishedAt: past}
	path, _ := r.claimPath(id)
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		t.Fatal(err)
	}
	raw, _ := json.Marshal(claim)
	if err := writePrivateAtomic(path, raw); err != nil {
		t.Fatal(err)
	}
	c := Command{ID: id, ExpiresAt: past, Args: CommandArgs{
		ActionID: id, ActionKind: "stop_process", ResourceKey: "k",
		Expected: map[string]any{
			"key": "k", "pid": float64(10), "start_ticks": "20",
			"namespace": "pid:[1]", "uid": float64(0), "exe_identity": "1:2",
		}}}
	if err := r.Apply(c); err != nil {
		t.Fatalf("a completed claim must replay its outcome even past expiry: %v", err)
	}
	if lastActionEvent(t, r)["state"] != "completed" {
		t.Fatal("replay of a completed claim must report completed")
	}
}

func TestControlCallDeadSession(t *testing.T) {
	r := testService(t)
	s := &session{id: "dead", done: make(chan struct{})}
	close(s.done)
	s.stdin = nopWriter{}
	done := make(chan error, 1)
	go func() {
		_, err := r.controlCall(s, "", map[string]any{"subtype": "mcp_status"})
		done <- err
	}()
	select {
	case err := <-done:
		if err == nil {
			t.Fatal("a dead session must fail the control call")
		}
	case <-time.After(12 * time.Second):
		t.Fatal("control call must not wait for the timeout on a dead session")
	}
}

type nopWriter struct{}

func (nopWriter) Write(p []byte) (int, error) { return len(p), nil }
func (nopWriter) Close() error                { return nil }

package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"
)

func testService(t *testing.T) *resourceService {
	t.Helper()
	return testServiceWith(t, &Manager{sessions: map[string]*session{}})
}

func testServiceWith(t *testing.T, mgr *Manager) *resourceService {
	t.Helper()
	sp, err := OpenSpool(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	return newResourceService(mgr, sp)
}

func fakeClaude(t *testing.T, ignoreTerm bool) *exec.Cmd {
	t.Helper()
	dir := t.TempDir()
	src := `package main

import "time"

func main() {
	time.Sleep(time.Hour)
}
`
	if ignoreTerm {
		src = `package main

import (
	"os/signal"
	"syscall"
	"time"
)

func main() {
	signal.Ignore(syscall.SIGTERM)
	time.Sleep(time.Hour)
}
`
	}
	if err := os.WriteFile(filepath.Join(dir, "main.go"), []byte(src), 0o644); err != nil {
		t.Fatal(err)
	}
	bin := filepath.Join(dir, "claude")
	build := exec.Command("go", "build", "-o", bin, "main.go")
	build.Dir = dir
	build.Env = append(os.Environ(), "CGO_ENABLED=0", "GOFLAGS=-buildvcs=false")
	if out, err := build.CombinedOutput(); err != nil {
		t.Skipf("fixture build: %v %s", err, out)
	}
	cmd := exec.Command(bin)
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		cmd.Process.Kill()
		cmd.Wait()
	})
	return cmd
}

func registerFixtureSession(mgr *Manager, cmd *exec.Cmd) *session {
	start, exe := captureResourceIdentity("/proc", cmd.Process.Pid)
	s := &session{id: "fixture-session", cmd: cmd, state: "idle",
		startTicks: start, exeIdentity: exe, done: make(chan struct{}),
		ready: make(chan struct{})}
	mgr.sessions[s.id] = s
	return s
}

func applyStopCommand(row *ResourceRow) Command {
	id := newUUID()
	return Command{
		ID:        id,
		Op:        "resource_apply",
		ExpiresAt: time.Now().Add(60 * time.Second).UTC().Format(time.RFC3339),
		Args: CommandArgs{
			ActionID:    id,
			ActionKind:  "stop_process",
			ResourceKey: row.Key,
			Expected: map[string]any{
				"key": row.Key, "pid": float64(row.PID),
				"start_ticks":  strconv.FormatUint(row.StartTicks, 10),
				"namespace":    row.Namespace,
				"uid":          float64(row.UID),
				"exe_identity": row.ExeIdentity,
			},
		},
	}
}

func drainEvents(t *testing.T, sp *Spool) []Event {
	raw, err := os.ReadFile(filepath.Join(sp.root, "events.jsonl"))
	if err != nil {
		return nil
	}
	var out []Event
	for _, line := range strings.Split(strings.TrimSpace(string(raw)), "\n") {
		if line == "" {
			continue
		}
		var e Event
		if json.Unmarshal([]byte(line), &e) == nil {
			out = append(out, e)
		}
	}
	return out
}

func lastActionEvent(t *testing.T, r *resourceService) map[string]any {
	events := drainEvents(t, r.sp)
	if len(events) == 0 {
		t.Fatal("no events")
	}
	payload, _ := events[len(events)-1].Payload.(map[string]any)
	return payload
}

func TestValidateApplyArgs(t *testing.T) {
	rowID := newUUID()
	base := Command{ID: rowID, Args: CommandArgs{
		ActionID:    rowID,
		ActionKind:  "stop_process",
		ResourceKey: "boot:pid:[1]:10:20",
		Expected: map[string]any{
			"key": "boot:pid:[1]:10:20", "pid": float64(10), "start_ticks": "20",
			"namespace": "pid:[1]", "uid": float64(1000), "exe_identity": "1:2",
		},
	}}
	if err := validateApplyArgs(base); err != nil {
		t.Fatalf("valid args refused: %v", err)
	}
	bad := base
	bad.Args.ActionID = "../escape"
	if err := validateApplyArgs(bad); err == nil {
		t.Fatal("non-UUID action_id must fail closed")
	}
	bad = base
	bad.Args.ActionID = newUUID()
	if err := validateApplyArgs(bad); err == nil {
		t.Fatal("action_id differing from command id must fail")
	}
	bad = base
	bad.Args.ActionKind = "kill_group"
	if err := validateApplyArgs(bad); err == nil {
		t.Fatal("unknown action kind must be refused")
	}
	bad = base
	bad.Args.Expected["exe_identity"] = ""
	if err := validateApplyArgs(bad); err == nil {
		t.Fatal("incomplete identity must be refused")
	}
	bad = base
	bad.Args.Expected["key"] = "other:key"
	if err := validateApplyArgs(bad); err == nil {
		t.Fatal("expected.key differing from resource_key must fail")
	}
	bad = base
	bad.Args.ActionKind = "disconnect_mcp"
	bad.Args.Expected = map[string]any{"key": "boot:pid:[1]:10:20"}
	if err := validateApplyArgs(bad); err == nil {
		t.Fatal("mcp actions need session_id and server_name")
	}
	bad.Args.SessionID = "s"
	bad.Args.ServerName = "srv"
	if err := validateApplyArgs(bad); err != nil {
		t.Fatalf("valid mcp args refused: %v", err)
	}
}

func TestCommandExpiryFailClosed(t *testing.T) {
	r := testService(t)
	mk := func(expires string) Command {
		id := newUUID()
		return Command{ID: id, ExpiresAt: expires, Args: CommandArgs{
			ActionID: id, ActionKind: "stop_process", ResourceKey: "boot:pid:[1]:10:20",
			Expected: map[string]any{
				"key": "boot:pid:[1]:10:20", "pid": float64(10), "start_ticks": "20",
				"namespace": "pid:[1]", "uid": float64(0), "exe_identity": "1:2",
			},
		}}
	}
	for _, bad := range []string{"", "not a date", "2020-01-01T00:00:00Z",
		time.Now().Add(-time.Second).UTC().Format(time.RFC3339),
		time.Now().Add(time.Hour).UTC().Format(time.RFC3339)} {
		c := mk(bad)
		if err := r.Apply(c); err == nil {
			t.Fatalf("expiry %q must fail closed", bad)
		}
		payload := lastActionEvent(t, r)
		if payload["state"] != "failed" {
			t.Fatalf("expiry %q emitted %+v", bad, payload)
		}
	}
}

func TestClaimExclusiveAndReplay(t *testing.T) {
	r := testService(t)
	id := newUUID()
	expires := time.Now().Add(60 * time.Second).UTC().Format(time.RFC3339)
	expMap := map[string]any{
		"key": "k", "pid": float64(10), "start_ticks": "20",
		"namespace": "pid:[1]", "uid": float64(0), "exe_identity": "1:2",
	}
	claim := &resourceClaim{ActionID: id, Kind: "stop_process", Key: "k",
		Expected: expMap, ExpiresAt: expires,
		State: "claimed", ClaimedAt: time.Now().UTC().Format(time.RFC3339Nano)}
	if ok, err := r.claimAction(claim); !ok || err != nil {
		t.Fatalf("first claim: ok=%v err=%v", ok, err)
	}
	if ok, err := r.claimAction(claim); ok || err != nil {
		t.Fatal("second claim of the same id must lose")
	}
	c := Command{ID: id, ExpiresAt: expires, Args: CommandArgs{
		ActionID: id, ActionKind: "stop_process", ResourceKey: "k",
		Expected: expMap,
	}}
	if err := r.Apply(c); err == nil {
		t.Fatal("incomplete claim must surface an error")
	}
	payload := lastActionEvent(t, r)
	if payload["state"] != "uncertain" {
		t.Fatalf("incomplete claim must report uncertain: %+v", payload)
	}
	c2 := c
	c2.Args.ResourceKey = "different"
	c2.Args.Expected = map[string]any{
		"key": "different", "pid": float64(10), "start_ticks": "20",
		"namespace": "pid:[1]", "uid": float64(0), "exe_identity": "1:2",
	}
	if err := r.Apply(c2); err == nil {
		t.Fatal("same id under a different key must fail")
	}
	payload = lastActionEvent(t, r)
	if payload["state"] != "failed" {
		t.Fatalf("mismatched replay emitted %+v", payload)
	}
	claim.State, claim.Result = "completed", "done"
	path, _ := r.claimPath(id)
	raw, _ := json.Marshal(claim)
	writePrivateAtomic(path, raw)
	if err := r.Apply(c); err != nil {
		t.Fatal(err)
	}
	payload = lastActionEvent(t, r)
	if payload["state"] != "completed" {
		t.Fatal("completed claim must replay completed")
	}
	badID := newUUID()
	p, _ := r.claimPath(badID)
	os.WriteFile(p, []byte("{"), 0o600)
	cb := Command{ID: badID, ExpiresAt: expires, Args: CommandArgs{
		ActionID: badID, ActionKind: "stop_process", ResourceKey: "k",
		Expected: map[string]any{
			"key": "k", "pid": float64(10), "start_ticks": "20",
			"namespace": "pid:[1]", "uid": float64(0), "exe_identity": "1:2",
		}}}
	if err := r.Apply(cb); err == nil {
		t.Fatal("malformed claim must be an error")
	}
	if lastActionEvent(t, r)["state"] != "uncertain" {
		t.Fatal("malformed claim must surface uncertain")
	}
}

func ownSleep(t *testing.T, argv ...string) *exec.Cmd {
	t.Helper()
	cmd := exec.Command(argv[0], argv[1:]...)
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		cmd.Process.Kill()
		cmd.Wait()
	})
	return cmd
}

func TestPidfdSafeNegative(t *testing.T) {
	cmd := ownSleep(t, "sleep", "30")
	fd, err := pidfdOpen(cmd.Process.Pid)
	if err != nil {
		t.Skipf("pidfd unsupported here: %v", err)
	}
	defer closeFD(fd)
	if err := pidfdSendSignal(fd, sigTERM); err != nil {
		t.Fatalf("signal own fixture: %v", err)
	}
	done := make(chan error, 1)
	go func() { done <- cmd.Wait() }()
	select {
	case <-done:
	case <-time.After(5 * time.Second):
		t.Fatal("own fixture did not exit after SIGTERM")
	}
	if err := pidfdSendSignal(fd, sigTERM); err == nil {
		t.Fatal("signalling an exited pidfd must fail")
	}
}

func TestPidfdBoundIdentity(t *testing.T) {
	cmd := ownSleep(t, "sleep", "30")
	pid := cmd.Process.Pid
	fd, err := pidfdOpen(pid)
	if err != nil {
		t.Skipf("pidfd unsupported here: %v", err)
	}
	defer closeFD(fd)
	cmd.Process.Kill()
	cmd.Wait()
	if err := pidfdSendSignal(fd, sigTERM); err == nil {
		t.Fatal("a pidfd bound to a dead process must not signal")
	}
}

func TestVerifyIdentityDropsReusedGeneration(t *testing.T) {
	cmd := ownSleep(t, "sleep", "30")
	row := ResourceProcess{PID: cmd.Process.Pid, UID: os.Geteuid()}
	raw, err := os.ReadFile("/proc/" + strconv.Itoa(cmd.Process.Pid) + "/stat")
	if err != nil {
		t.Skip("proc unavailable")
	}
	stat, _ := parseResourceStat(string(raw))
	row.StartTicks = stat.startTicks
	row.Namespace, _ = os.Readlink("/proc/self/ns/pid")
	info, _ := os.Stat("/proc/" + strconv.Itoa(cmd.Process.Pid) + "/exe")
	row.ExeIdentity = exeIdentity(info)
	boot, _ := os.ReadFile("/proc/sys/kernel/random/boot_id")
	row.Key = resourceKey(strings.TrimSpace(string(boot)), row.Namespace, row.PID, row.StartTicks)
	if !verifyResourceIdentity("/proc", &row) {
		t.Fatal("live own fixture must verify")
	}
	row.StartTicks++
	row.Key = resourceKey(strings.TrimSpace(string(boot)), row.Namespace, row.PID, row.StartTicks)
	if verifyResourceIdentity("/proc", &row) {
		t.Fatal("a different generation must not verify")
	}
	row.StartTicks--
	row.Key = resourceKey(strings.TrimSpace(string(boot)), row.Namespace, row.PID, row.StartTicks)
	row.ExeIdentity = "9:9"
	if verifyResourceIdentity("/proc", &row) {
		t.Fatal("a different exe inode must not verify")
	}
	cmd.Process.Kill()
	cmd.Wait()
	if verifyResourceIdentity("/proc", &row) {
		t.Fatal("an exited process must not verify")
	}
}

func TestSnapshotWriteAndLatest(t *testing.T) {
	r := testService(t)
	snap := r.Scan()
	if snap.SchemaVersion != resourceSchemaVersion {
		t.Fatal("snapshot schema version missing")
	}
	raw, err := os.ReadFile(filepath.Join(r.sp.root, resourceSnapshotFile))
	if err != nil {
		t.Fatal("snapshot file not written")
	}
	var onDisk resourceSnapshot
	if json.Unmarshal(raw, &onDisk) != nil {
		t.Fatal("snapshot is not valid JSON")
	}
	info, _ := os.Stat(filepath.Join(r.sp.root, resourceSnapshotFile))
	if info.Mode().Perm() != 0o600 {
		t.Fatalf("snapshot must be private, got %o", info.Mode())
	}
	if r.Latest().SchemaVersion != resourceSchemaVersion {
		t.Fatal("latest snapshot not tracked")
	}
}

func TestLineageLedgerRoundTrip(t *testing.T) {
	r := testService(t)
	origins := map[string]ResourceOrigin{
		"boot:pid:[1]:5:10": {OwnerKey: "boot:pid:[1]:5:10", OwnerPID: 5, Harness: "claude",
			UID: 1000, Namespace: "pid:[1]", IsRoot: true},
	}
	r.saveLineage(origins)
	r2 := testService(t)
	r2.dir = r.dir
	r2.loadLineage()
	if len(r2.prev) != 1 || !r2.prev["boot:pid:[1]:5:10"].IsRoot {
		t.Fatalf("lineage not retained: %+v", r2.prev)
	}
}

func TestGatewayAuthAndTools(t *testing.T) {
	r := testService(t)
	sock := filepath.Join(t.TempDir(), "g.sock")
	r.socket = sock
	ln, err := net.Listen("unix", sock)
	if err != nil {
		t.Fatal(err)
	}
	defer ln.Close()
	go func() {
		for {
			c, err := ln.Accept()
			if err != nil {
				return
			}
			go r.serveGateway(c)
		}
	}()
	call := func(token, method string, params map[string]any) map[string]any {
		conn, err := net.Dial("unix", sock)
		if err != nil {
			t.Fatal(err)
		}
		defer conn.Close()
		req := map[string]any{"jsonrpc": "2.0", "id": 1, "method": method, "params": params, "token": token}
		raw, _ := json.Marshal(req)
		conn.Write(append(raw, '\n'))
		sc := bufio.NewScanner(conn)
		sc.Buffer(make([]byte, 1<<10), 4<<20)
		if !sc.Scan() {
			t.Fatal("no answer")
		}
		var resp map[string]any
		json.Unmarshal(sc.Bytes(), &resp)
		return resp
	}
	if resp := call("wrong", "tools/list", nil); resp["error"] == nil {
		t.Fatal("a wrong token must be unauthorized")
	}
	if resp := call("", "tools/list", nil); resp["error"] == nil {
		t.Fatal("a missing token must be unauthorized")
	}
	if resp := call(r.token, "shutdown", nil); resp["error"] == nil {
		t.Fatal("unknown methods must error")
	}
	resp := call(r.token, "tools/list", nil)
	if resp["error"] != nil {
		t.Fatalf("tools/list: %v", resp["error"])
	}
	tools := resp["result"].(map[string]any)["tools"].([]any)
	names := map[string]bool{}
	for _, tl := range tools {
		names[tl.(map[string]any)["name"].(string)] = true
	}
	if !names["list_resources"] || !names["inspect_resource"] || !names["propose_action"] {
		t.Fatalf("exactly the three tools: %v", names)
	}
	if names["apply_action"] || names["stop_process"] {
		t.Fatal("the gateway must not offer action tools")
	}
	resp = call(r.token, "tools/call", map[string]any{"name": "unknown_tool", "arguments": map[string]any{}})
	if resp["error"] == nil {
		t.Fatal("unknown tools must error")
	}
	resp = call(r.token, "tools/call", map[string]any{
		"name": "inspect_resource", "arguments": map[string]any{"extra": "x"}})
	if resp["error"] == nil {
		t.Fatal("unknown arguments must error")
	}
	resp = call(r.token, "tools/call", map[string]any{
		"name": "propose_action", "arguments": map[string]any{"action_kind": "nuke",
			"resource_key": "x", "reason": "r"}})
	if resp["error"] == nil {
		t.Fatal("unknown action kind must be refused")
	}
	resp = call(r.token, "tools/call", map[string]any{
		"name": "propose_action", "arguments": map[string]any{
			"action_kind": "stop_process", "resource_key": "no:such:row", "reason": "check"}})
	if resp["error"] == nil {
		t.Fatal("a target absent from the snapshot must be refused")
	}
}

func TestProposeRecordsOnly(t *testing.T) {
	mgr := &Manager{sessions: map[string]*session{}}
	cmd := fakeClaude(t, false)
	registerFixtureSession(mgr, cmd)
	r := testServiceWith(t, mgr)
	snap := r.Scan()
	var target *ResourceRow
	for i := range snap.Processes {
		if snap.Processes[i].Manageable && !snap.Processes[i].Protected {
			target = &snap.Processes[i]
			break
		}
	}
	if target == nil {
		t.Skip("no manageable process to propose against")
	}
	out, err := r.proposeAction("stop_process", target.Key, "operator asked")
	if err != nil {
		t.Fatal(err)
	}
	if out.(map[string]any)["proposal_id"] == "" {
		t.Fatal("no proposal id")
	}
	if out.(map[string]any)["note"] != resourceProposalRecordedMessage {
		t.Fatal("proposal note must be the recorded-only message")
	}
	found := false
	for _, e := range drainEvents(t, r.sp) {
		if e.Kind == "resource_proposal" {
			found = true
		}
	}
	if !found {
		t.Fatal("propose must emit exactly a resource_proposal event")
	}
	if _, err := os.Stat("/proc/" + strconv.Itoa(target.PID)); err != nil {
		t.Fatal("a proposal signalled something it must not have")
	}
}

func TestApplyStopProcessExits(t *testing.T) {
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
		t.Fatalf("fixture not manageable: %+v", row)
	}
	c := applyStopCommand(row)
	if err := r.Apply(c); err != nil {
		t.Fatalf("apply: %v", err)
	}
	done := make(chan error, 1)
	go func() { done <- cmd.Wait() }()
	select {
	case <-done:
	case <-time.After(6 * time.Second):
		t.Fatal("fixture still alive after apply")
	}
	claim, _, _ := r.readClaim(c.ID)
	if claim.State != "completed" {
		t.Fatalf("claim=%+v", claim)
	}
}

func TestApplyStopProcessStillRunning(t *testing.T) {
	mgr := &Manager{sessions: map[string]*session{}}
	cmd := fakeClaude(t, true)
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
		t.Skipf("fixture not manageable: %+v", row)
	}
	c := applyStopCommand(row)
	if err := r.Apply(c); err != nil {
		t.Fatalf("apply: %v", err)
	}
	claim, _, _ := r.readClaim(c.ID)
	if claim.State != "still_running" {
		t.Fatalf("claim=%+v", claim)
	}
}

func TestApplyRejectsStaleIdentity(t *testing.T) {
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
	if row == nil {
		t.Skip("fixture not in inventory")
	}
	c := applyStopCommand(row)
	c.Args.Expected["start_ticks"] = strconv.FormatUint(row.StartTicks+1, 10)
	if err := r.Apply(c); err == nil {
		t.Fatal("a stale generation must fail closed")
	}
	if _, err := os.Stat("/proc/" + strconv.Itoa(cmd.Process.Pid)); err != nil {
		t.Fatal("a refused action must not have signalled")
	}
}

func TestMCPResponseCorrelation(t *testing.T) {
	r := testService(t)
	s := &session{id: "sess-1"}
	watch := &mcpWatch{requestID: "req-1", sessionID: s.id, reply: make(chan map[string]any, 1)}
	r.watches["req-1"] = watch
	r.handleControlResponse([]byte(`{"type":"control_response","response":{"subtype":"success","request_id":"req-1","response":{"mcpServers":[{"name":"srv","status":"connected","type":"stdio","config":{"secret":"x"}}]}}}`), s)
	select {
	case got := <-watch.reply:
		servers := got["mcpServers"].([]any)
		if len(servers) != 1 {
			t.Fatal("server list not routed")
		}
	case <-time.After(time.Second):
		t.Fatal("response not routed to its watch")
	}
	watchB := &mcpWatch{requestID: "req-b", sessionID: s.id, reply: make(chan map[string]any, 1)}
	r.watches["req-b"] = watchB
	other := &session{id: "sess-other"}
	r.handleControlResponse([]byte(`{"type":"control_response","response":{"subtype":"success","request_id":"req-b","response":{}}}`), other)
	select {
	case <-watchB.reply:
		t.Fatal("a response from another session must not satisfy this watch")
	case <-time.After(200 * time.Millisecond):
	}
	r.handleControlResponse([]byte(`{"type":"control_response","response":{"subtype":"success","request_id":"nobody","response":{}}}`), s)
	watch2 := &mcpWatch{requestID: "req-2", sessionID: s.id, reply: make(chan map[string]any, 1)}
	r.watches["req-2"] = watch2
	r.handleControlResponse([]byte(`{"type":"control_response","response":{"subtype":"error","request_id":"req-2","error":"raw payload xyz"}}`), s)
	if got := <-watch2.reply; got["__error"] != "control request failed" {
		t.Fatalf("error frame must surface sanitized: %v", got)
	}
}

func TestMCPStatusPruning(t *testing.T) {
	mgr := &Manager{sessions: map[string]*session{}}
	r := testServiceWith(t, mgr)
	r.mcp["mcp:owner1:c3J2"] = map[string]any{
		"key": "mcp:owner1:c3J2", "owner_key": "owner1", "session_id": "sess-1",
		"name": "srv", "status": "connected",
		"generated_at": time.Now().UTC().Format(time.RFC3339Nano),
	}
	r.mcp["mcp:dead:c3J2"] = map[string]any{
		"key": "mcp:dead:c3J2", "owner_key": "gone", "session_id": "sess-dead",
		"name": "srv", "status": "connected",
		"generated_at": time.Now().UTC().Format(time.RFC3339Nano),
	}
	r.mcp["mcp:old:c3J2"] = map[string]any{
		"key": "mcp:old:c3J2", "owner_key": "owner1", "session_id": "sess-1",
		"name": "srv", "status": "connected",
		"generated_at": time.Now().Add(-time.Hour).UTC().Format(time.RFC3339Nano),
	}
	rows := []ResourceRow{{ResourceProcess: ResourceProcess{Key: "owner1", PID: 5}}}
	managed := map[int]ResourceSessionInfo{5: {SessionID: "sess-1"}}
	r.pruneMCP(rows, managed)
	if _, ok := r.mcp["mcp:dead:c3J2"]; ok {
		t.Fatal("dead owner status must be pruned")
	}
	if _, ok := r.mcp["mcp:old:c3J2"]; ok {
		t.Fatal("stale status must be pruned")
	}
	if _, ok := r.mcp["mcp:owner1:c3J2"]; !ok {
		t.Fatal("live recent status must remain")
	}
}

func TestReapSkipsResourceAgent(t *testing.T) {
	m := &Manager{sessions: map[string]*session{}, idle: time.Nanosecond}
	normal := &session{id: "n", state: "idle", last: time.Now().Add(-time.Hour)}
	agent := &session{id: "a", state: "idle", last: time.Now().Add(-time.Hour), resourceAgent: true}
	m.sessions["n"], m.sessions["a"] = normal, agent
	var stale []string
	for id, s := range m.sessions {
		if s.resourceAgent {
			continue
		}
		if s.runtimeState() == "idle" && s.idleFor() > m.idle {
			stale = append(stale, id)
		}
	}
	if len(stale) != 1 || stale[0] != "n" {
		t.Fatalf("reaper must spare only the agent: %v", stale)
	}
}

func TestRecentActionsLimitAndOrder(t *testing.T) {
	r := testService(t)
	os.MkdirAll(filepath.Join(r.dir, "actions"), 0o700)
	base := time.Now().Add(-time.Hour)
	for i := 0; i < 30; i++ {
		claim := resourceClaim{
			ActionID: fmt.Sprintf("11111111-2222-3333-4444-%012d", 1000+i),
			Kind:     "stop_process", Key: "k", State: "completed",
			ClaimedAt:  base.Add(time.Duration(i) * time.Minute).UTC().Format(time.RFC3339Nano),
			FinishedAt: base.Add(time.Duration(i) * time.Minute).UTC().Format(time.RFC3339Nano),
		}
		path, _ := r.claimPath(claim.ActionID)
		raw, _ := json.Marshal(claim)
		os.WriteFile(path, raw, 0o600)
	}
	got := r.recentActions(resourceActionRows)
	if len(got) != resourceActionRows {
		t.Fatalf("recent actions must cap at %d, got %d", resourceActionRows, len(got))
	}
	if got[0]["action_id"] != fmt.Sprintf("11111111-2222-3333-4444-%012d", 1029) {
		t.Fatalf("newest action must sort first: %v", got[0]["action_id"])
	}
}

func TestConcurrentScanAndControlWatch(t *testing.T) {
	mgr := &Manager{sessions: map[string]*session{}}
	r := testServiceWith(t, mgr)
	done := make(chan struct{})
	go func() {
		for i := 0; i < 5; i++ {
			r.Scan()
		}
		close(done)
	}()
	s := &session{id: "sess-1"}
	watch := &mcpWatch{requestID: "req-c", sessionID: s.id, reply: make(chan map[string]any, 1)}
	r.mu.Lock()
	r.watches["req-c"] = watch
	r.mu.Unlock()
	r.handleControlResponse([]byte(`{"type":"control_response","response":{"subtype":"success","request_id":"req-c","response":{"mcpServers":[]}}}`), s)
	<-done
	select {
	case <-watch.reply:
	case <-time.After(time.Second):
		t.Fatal("response not delivered")
	}
}

func TestUUIDShapeAndUniqueness(t *testing.T) {
	a, b := newUUID(), newUUID()
	if !resourceUUIDShape.MatchString(a) || !resourceUUIDShape.MatchString(b) {
		t.Fatal("newUUID must produce UUIDs")
	}
	if a == b {
		t.Fatal("uuids must differ")
	}
	_ = syscall.SIGTERM
}

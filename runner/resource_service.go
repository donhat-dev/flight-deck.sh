package main

import (
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"math"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"
)

const (
	resourceSchemaVersion = 1
	resourceLineageFile   = "lineage.json"
	resourceSnapshotFile  = "resources.json"
)

type resourceSnapshot struct {
	SchemaVersion int              `json:"schema_version"`
	Available     bool             `json:"available"`
	Complete      bool             `json:"complete"`
	Error         string           `json:"error,omitempty"`
	BootID        string           `json:"boot_id"`
	Namespace     string           `json:"namespace"`
	TakenAt       string           `json:"taken_at"`
	Processes     []ResourceRow    `json:"processes"`
	MCPServers    []map[string]any `json:"mcp_servers"`
	Warnings      []string         `json:"warnings"`
}

type mcpWatch struct {
	requestID string
	sessionID string
	ownerKey  string
	reply     chan map[string]any
}

type resourceService struct {
	mgr  *Manager
	sp   *Spool
	proc string
	hz   float64

	scanMu           sync.Mutex
	mu               sync.Mutex
	beforeSideEffect func()
	dir              string
	socket           string
	token            string
	tokenErr         error
	prev             map[string]ResourceOrigin
	latest           *resourceSnapshot
	watches          map[string]*mcpWatch
	mcp              map[string]map[string]any
}

func newResourceService(mgr *Manager, sp *Spool) *resourceService {
	r := &resourceService{
		mgr:     mgr,
		sp:      sp,
		proc:    "/proc",
		hz:      measureCLKTCK(),
		dir:     filepath.Join(sp.root, "resources"),
		socket:  filepath.Join(sp.root, "resources", "agent.sock"),
		watches: map[string]*mcpWatch{},
		mcp:     map[string]map[string]any{},
		prev:    map[string]ResourceOrigin{},
	}
	if err := os.MkdirAll(r.dir, 0o700); err != nil {
		log.Printf("resources dir: %v", err)
	}
	r.loadLineage()
	r.mintToken()
	return r
}

func measureCLKTCK() float64 {
	out, err := exec.Command("getconf", "CLK_TCK").Output()
	if err != nil {
		return 0
	}
	hz, err := strconv.ParseFloat(strings.TrimSpace(string(out)), 64)
	if err != nil || hz <= 0 || math.IsNaN(hz) || math.IsInf(hz, 0) {
		return 0
	}
	return hz
}

func (r *resourceService) Run() {
	go func() {
		r.Scan()
		for range time.Tick(resourcePollInterval) {
			r.Scan()
		}
	}()
}

func (r *resourceService) Scan() *resourceSnapshot {
	r.scanMu.Lock()
	snap := r.scanLocked(true)
	r.scanMu.Unlock()
	r.mgr.mu.Lock()
	var mgmt *session
	for _, s := range r.mgr.sessions {
		if s.resourceAgent {
			mgmt = s
		}
	}
	r.mgr.mu.Unlock()
	if mgmt != nil {
		go func() {
			owner, err := r.sessionOwnerKey(mgmt)
			if err == nil {
				r.requestMCPStatus(mgmt, owner)
			}
		}()
	}
	return snap
}

func (r *resourceService) ScanProcesses() *resourceSnapshot {
	r.scanMu.Lock()
	defer r.scanMu.Unlock()
	return r.scanLocked(false)
}

func (r *resourceService) scanLocked(withMCP bool) *resourceSnapshot {
	snap, err := r.collect(withMCP)
	if err != nil {
		snap = &resourceSnapshot{
			SchemaVersion: resourceSchemaVersion,
			Available:     false, Complete: false,
			Error:     err.Error(),
			TakenAt:   time.Now().UTC().Format(time.RFC3339Nano),
			Processes: []ResourceRow{}, MCPServers: []map[string]any{},
		}
	}
	r.mu.Lock()
	r.latest = snap
	r.mu.Unlock()
	r.writeSnapshot(snap)
	return snap
}

func (r *resourceService) Latest() *resourceSnapshot {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.latest == nil {
		return &resourceSnapshot{SchemaVersion: resourceSchemaVersion, Error: "no scan has run yet",
			Processes: []ResourceRow{}, MCPServers: []map[string]any{}}
	}
	return r.latest
}

func (r *resourceService) collect(withMCP bool) (*resourceSnapshot, error) {
	proc, err := collectResourceProcesses(r.proc, os.Geteuid(), r.hz)
	if err != nil {
		return nil, err
	}
	managed := r.mgr.managedResourceSessions()
	protected := r.protectedPIDs(proc.Processes)
	r.mu.Lock()
	prev := r.prev
	r.mu.Unlock()
	rows, retained := classifyResourceProcesses(proc.Processes, prev, managed, protected)
	kept := make([]ResourceRow, 0, len(rows))
	for i := range rows {
		row := &rows[i]
		readResourceFDs(filepath.Join(r.proc, strconv.Itoa(row.PID)), &row.ResourceProcess)
		if !verifyResourceIdentity(r.proc, &row.ResourceProcess) {
			delete(retained, row.Key)
			continue
		}
		kept = append(kept, *row)
	}
	annotateResourceStdio(kept)
	if withMCP {
		for i := range kept {
			if !kept[i].Managed {
				continue
			}
			if s := r.mgr.get(kept[i].SessionID); s != nil && s.stdin != nil && !s.resourceAgent {
				if _, err := r.requestMCPStatus(s, kept[i].Key); err != nil {
					proc.Warnings = append(proc.Warnings,
						fmt.Sprintf("mcp status unavailable for session %s", kept[i].SessionID[:8]))
				}
			}
		}
	}
	r.pruneMCP(kept, managed)
	snap := &resourceSnapshot{
		SchemaVersion: resourceSchemaVersion,
		Available:     true, Complete: true,
		BootID: proc.BootID, Namespace: proc.Namespace, TakenAt: proc.TakenAt,
		Processes:  kept,
		MCPServers: r.mcpServers(),
		Warnings:   proc.Warnings,
	}
	r.mu.Lock()
	r.prev = retained
	r.mu.Unlock()
	r.saveLineage(retained)
	return snap, nil
}

func (r *resourceService) protectedPIDs(processes []ResourceProcess) map[int]string {
	protected := map[int]string{os.Getpid(): "runner"}
	byPID := map[int]ResourceProcess{}
	for _, p := range processes {
		byPID[p.PID] = p
	}
	cur, seen := os.Getpid(), map[int]bool{}
	for {
		p, ok := byPID[cur]
		if !ok || p.ParentPID <= 1 || seen[p.ParentPID] {
			break
		}
		seen[p.ParentPID] = true
		protected[p.ParentPID] = "runner"
		cur = p.ParentPID
	}
	return protected
}

func verifyResourceIdentity(root string, row *ResourceProcess) bool {
	dir := filepath.Join(root, strconv.Itoa(row.PID))
	raw, err := os.ReadFile(filepath.Join(dir, "stat"))
	if err != nil {
		return false
	}
	stat, err := parseResourceStat(string(raw))
	if err != nil || stat.pid != row.PID || stat.startTicks != row.StartTicks {
		return false
	}
	raw, err = os.ReadFile(filepath.Join(dir, "status"))
	if err != nil {
		return false
	}
	uid, _, err := parseResourceStatus(string(raw))
	if err != nil || uid != row.UID {
		return false
	}
	pidns, err := os.Readlink(filepath.Join(dir, "ns", "pid"))
	if err != nil || pidns != row.Namespace {
		return false
	}
	info, err := os.Stat(filepath.Join(dir, "exe"))
	if err != nil {
		return false
	}
	if exeIdentity(info) != row.ExeIdentity {
		return false
	}
	boot, err := os.ReadFile(filepath.Join(root, "sys", "kernel", "random", "boot_id"))
	if err != nil {
		return false
	}
	return row.Key == resourceKey(strings.TrimSpace(string(boot)), pidns, row.PID, row.StartTicks)
}

func stripFDData(p ResourceProcess) ResourceProcess {
	p.FDCount, p.SocketCount, p.Sockets, p.FDLinks = nil, nil, nil, nil
	return p
}

func (r *resourceService) loadLineage() {
	raw, err := os.ReadFile(filepath.Join(r.dir, resourceLineageFile))
	if err != nil {
		return
	}
	var data struct {
		Origins map[string]ResourceOrigin `json:"origins"`
	}
	if err := json.Unmarshal(raw, &data); err != nil || data.Origins == nil {
		return
	}
	r.prev = data.Origins
}

func (r *resourceService) saveLineage(origins map[string]ResourceOrigin) {
	raw, err := json.Marshal(map[string]any{"origins": origins})
	if err != nil {
		return
	}
	writePrivateAtomic(filepath.Join(r.dir, resourceLineageFile), raw)
}

func (r *resourceService) writeSnapshot(snap *resourceSnapshot) {
	raw, err := json.Marshal(snap)
	if err != nil {
		return
	}
	writePrivateAtomic(filepath.Join(r.sp.root, resourceSnapshotFile), raw)
}

func writePrivateAtomic(path string, raw []byte) error {
	dir := filepath.Dir(path)
	tmp, err := os.CreateTemp(dir, "."+filepath.Base(path)+"-*.tmp")
	if err != nil {
		return err
	}
	name := tmp.Name()
	if _, err := tmp.Write(raw); err != nil {
		tmp.Close()
		os.Remove(name)
		return err
	}
	if err := tmp.Chmod(0o600); err != nil {
		tmp.Close()
		os.Remove(name)
		return err
	}
	if err := tmp.Sync(); err != nil {
		tmp.Close()
		os.Remove(name)
		return err
	}
	if err := tmp.Close(); err != nil {
		os.Remove(name)
		return err
	}
	return os.Rename(name, path)
}

func (m *Manager) managedResourceSessions() map[int]ResourceSessionInfo {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := map[int]ResourceSessionInfo{}
	for id, s := range m.sessions {
		if s.cmd == nil || s.cmd.Process == nil {
			continue
		}
		s.meta.Lock()
		out[s.cmd.Process.Pid] = ResourceSessionInfo{
			SessionID:     id,
			State:         s.state,
			StartTicks:    s.startTicks,
			ExeIdentity:   s.exeIdentity,
			ResourceAgent: s.resourceAgent,
		}
		s.meta.Unlock()
	}
	return out
}

func captureResourceIdentity(root string, pid int) (start uint64, exe string) {
	raw, err := os.ReadFile(filepath.Join(root, strconv.Itoa(pid), "stat"))
	if err == nil {
		if stat, err := parseResourceStat(string(raw)); err == nil {
			start = stat.startTicks
		}
	}
	info, err := os.Stat(filepath.Join(root, strconv.Itoa(pid), "exe"))
	if err == nil {
		exe = exeIdentity(info)
	}
	return start, exe
}

func exeIdentity(info os.FileInfo) string {
	st, ok := info.Sys().(*syscall.Stat_t)
	if !ok {
		return ""
	}
	return fmt.Sprintf("%d:%d", st.Dev, st.Ino)
}

func (r *resourceService) mcpServers() []map[string]any {
	r.mu.Lock()
	defer r.mu.Unlock()
	out := make([]map[string]any, 0, len(r.mcp))
	for _, row := range r.mcp {
		out = append(out, row)
	}
	return out
}

func (r *resourceService) pruneMCP(rows []ResourceRow, managed map[int]ResourceSessionInfo) {
	aliveOwners := map[string]bool{}
	aliveSessions := map[string]bool{}
	for i := range rows {
		aliveOwners[rows[i].Key] = true
		if rows[i].Managed && rows[i].SessionID != "" {
			aliveSessions[rows[i].SessionID] = true
		}
	}
	for _, info := range managed {
		aliveSessions[info.SessionID] = true
	}
	cutoff := time.Now().Add(-resourceSnapshotTTL)
	r.mu.Lock()
	defer r.mu.Unlock()
	for key, row := range r.mcp {
		owner, _ := row["owner_key"].(string)
		sessionID, _ := row["session_id"].(string)
		stamp, _ := row["generated_at"].(string)
		at, err := time.Parse(time.RFC3339Nano, stamp)
		stale := err != nil || at.Before(cutoff)
		if stale || !aliveOwners[owner] || !aliveSessions[sessionID] {
			delete(r.mcp, key)
		}
	}
}

func sanitizeControlError(err error) string {
	if err == nil {
		return ""
	}
	text := err.Error()
	if strings.Contains(text, "timed out") {
		return "control request timed out"
	}
	return "control request failed"
}

func (r *resourceService) requestMCPStatus(s *session, ownerKey string) (map[string]any, error) {
	if s == nil {
		return nil, errors.New("session is not running")
	}
	resp, err := r.controlCall(s, ownerKey, map[string]any{"subtype": "mcp_status"})
	if err != nil {
		r.markMCPUnknown(s.id, ownerKey, sanitizeControlError(err))
		return nil, err
	}
	rawServers, present := resp["mcpServers"]
	servers, ok := rawServers.([]any)
	if present && !ok {
		r.markMCPUnknown(s.id, ownerKey, "malformed status")
		return nil, errors.New("mcp status response is malformed")
	}
	r.mu.Lock()
	for key, row := range r.mcp {
		if row["session_id"] == s.id {
			delete(r.mcp, key)
		}
	}
	for _, raw := range servers {
		entry, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		name, _ := entry["name"].(string)
		if name == "" {
			continue
		}
		status, _ := entry["status"].(string)
		stype, _ := entry["type"].(string)
		r.mcp[resourceMCPKey(ownerKey, name)] = map[string]any{
			"key":          resourceMCPKey(ownerKey, name),
			"owner_key":    ownerKey,
			"session_id":   s.id,
			"name":         name,
			"status":       status,
			"type":         stype,
			"protected":    s.resourceAgent,
			"management":   s.resourceAgent,
			"generated_at": time.Now().UTC().Format(time.RFC3339Nano),
		}
	}
	r.mu.Unlock()
	return resp, nil
}

func (r *resourceService) markMCPUnknown(sessionID, ownerKey, reason string) {
	r.mu.Lock()
	defer r.mu.Unlock()
	for key, row := range r.mcp {
		if row["session_id"] == sessionID {
			delete(r.mcp, key)
		}
	}
	if ownerKey == "" {
		return
	}
	r.mcp["mcpstatus:"+sessionID] = map[string]any{
		"key":          "mcpstatus:" + sessionID,
		"owner_key":    ownerKey,
		"session_id":   sessionID,
		"name":         "(status unavailable)",
		"status":       "unknown",
		"type":         "",
		"protected":    true,
		"management":   false,
		"generated_at": time.Now().UTC().Format(time.RFC3339Nano),
	}
}

func (r *resourceService) controlCall(s *session, ownerKey string, request map[string]any) (map[string]any, error) {
	requestID := fmt.Sprintf("fd_mcp_%d", time.Now().UnixNano())
	watch := &mcpWatch{requestID: requestID, sessionID: s.id, ownerKey: ownerKey,
		reply: make(chan map[string]any, 1)}
	r.mu.Lock()
	r.watches[requestID] = watch
	r.mu.Unlock()
	defer func() {
		r.mu.Lock()
		delete(r.watches, requestID)
		r.mu.Unlock()
	}()
	req := map[string]any{
		"type":       "control_request",
		"request_id": requestID,
		"request":    request,
	}
	if err := r.mgr.writeLine(s, req); err != nil {
		return nil, err
	}
	select {
	case resp := <-watch.reply:
		if errText, _ := resp["__error"].(string); errText != "" {
			return nil, errors.New(errText)
		}
		return resp, nil
	case <-s.done:
		return nil, errors.New("session died during the control request")
	case <-time.After(10 * time.Second):
		return nil, errors.New("control request timed out")
	}
}

func (r *resourceService) handleControlResponse(raw []byte, s *session) {
	var resp controlResponse
	if err := json.Unmarshal(raw, &resp); err != nil {
		return
	}
	r.mu.Lock()
	watch := r.watches[resp.Response.RequestID]
	r.mu.Unlock()
	if watch == nil || (s != nil && watch.sessionID != "" && s.id != watch.sessionID) {
		return
	}
	payload := map[string]any{}
	if resp.Response.Error != "" || resp.Response.Subtype != "success" {
		payload["__error"] = "control request failed"
	} else {
		payload = resp.Response.Response
	}
	select {
	case watch.reply <- payload:
	default:
	}
}

func (r *resourceService) mcpAction(s *session, subtype, serverName string) (string, error) {
	if s == nil {
		return "", errors.New("session is not running")
	}
	request := map[string]any{"subtype": subtype, "serverName": serverName}
	if subtype == "mcp_toggle" {
		request["enabled"] = false
	}
	if _, err := r.controlCall(s, "", request); err != nil {
		return "failed", errors.New(sanitizeControlError(err))
	}
	ownerKey, err := r.sessionOwnerKey(s)
	if err != nil {
		return "uncertain", errors.New("could not verify the post-action status")
	}
	time.Sleep(500 * time.Millisecond)
	if _, err := r.requestMCPStatus(s, ownerKey); err != nil {
		return "uncertain", errors.New("post-action status is unavailable")
	}
	r.mu.Lock()
	row := r.mcp[resourceMCPKey(ownerKey, serverName)]
	r.mu.Unlock()
	status, _ := row["status"].(string)
	if subtype == "mcp_toggle" && status == "disabled" {
		return "completed", nil
	}
	if subtype == "mcp_reconnect" && status == "connected" {
		return "completed", nil
	}
	return "uncertain", fmt.Errorf("post-action status is %q", status)
}

func resourceMCPKey(ownerKey, name string) string {
	return "mcp:" + ownerKey + ":" + base64.RawURLEncoding.EncodeToString([]byte(name))
}

func (r *resourceService) sessionOwnerKey(s *session) (string, error) {
	if s.cmd == nil || s.cmd.Process == nil {
		return "", errors.New("session has no process")
	}
	proc, err := readResourceProcess(r.proc, s.cmd.Process.Pid, r.bootID(), r.namespace(), r.hz, r.uptime(), false)
	if err != nil {
		return "", err
	}
	if proc.UID != os.Geteuid() {
		return "", errors.New("session process is not ours")
	}
	s.meta.Lock()
	start, exe := s.startTicks, s.exeIdentity
	s.meta.Unlock()
	if start != 0 && proc.StartTicks != start {
		return "", errors.New("session generation changed")
	}
	if exe != "" && proc.ExeIdentity != exe {
		return "", errors.New("session process executable changed")
	}
	return proc.Key, nil
}

func (r *resourceService) bootID() string {
	raw, err := os.ReadFile(filepath.Join(r.proc, "sys", "kernel", "random", "boot_id"))
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(raw))
}

func (r *resourceService) namespace() string {
	ns, err := os.Readlink(filepath.Join(r.proc, "self", "ns", "pid"))
	if err != nil {
		return ""
	}
	return ns
}

func (r *resourceService) uptime() float64 {
	raw, err := os.ReadFile(filepath.Join(r.proc, "uptime"))
	if err != nil {
		return -1
	}
	fields := strings.Fields(string(raw))
	if len(fields) == 0 {
		return -1
	}
	v, err := strconv.ParseFloat(fields[0], 64)
	if err != nil {
		return -1
	}
	return v
}

func (r *resourceService) ScanNow(c Command) {
	snap := r.Scan()
	payload := map[string]any{
		"available": snap.Available,
		"complete":  snap.Complete,
		"processes": len(snap.Processes),
		"taken_at":  snap.TakenAt,
	}
	if snap.Error != "" {
		payload["error"] = snap.Error
	}
	r.sp.Emit(Event{Kind: "resource_scan", CmdID: c.ID, Payload: payload})
}

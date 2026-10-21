package main

import (
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
)

var (
	resourceUUIDShape   = regexp.MustCompile(`^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$`)
	resourceActionKinds = map[string]bool{
		"stop_process": true, "disconnect_mcp": true, "reconnect_mcp": true,
	}
)

type resourceClaim struct {
	ActionID   string         `json:"action_id"`
	Kind       string         `json:"kind"`
	Key        string         `json:"resource_key"`
	State      string         `json:"state"`
	Result     string         `json:"result,omitempty"`
	Expected   map[string]any `json:"expected"`
	SessionID  string         `json:"session_id,omitempty"`
	ServerName string         `json:"server_name,omitempty"`
	ExpiresAt  string         `json:"expires_at"`
	ClaimedAt  string         `json:"claimed_at"`
	FinishedAt string         `json:"finished_at,omitempty"`
}

func (r *resourceService) claimPath(actionID string) (string, error) {
	if !resourceUUIDShape.MatchString(actionID) {
		return "", errors.New("action_id is not a UUID")
	}
	return filepath.Join(r.dir, "actions", actionID+".json"), nil
}

func (r *resourceService) readClaim(actionID string) (*resourceClaim, bool, error) {
	path, err := r.claimPath(actionID)
	if err != nil {
		return nil, false, err
	}
	raw, err := os.ReadFile(path)
	if os.IsNotExist(err) {
		return nil, false, nil
	}
	if err != nil {
		return nil, false, err
	}
	var claim resourceClaim
	if json.Unmarshal(raw, &claim) != nil || claim.ActionID != actionID {
		return nil, true, errors.New("unreadable action claim")
	}
	return &claim, true, nil
}

func claimMatches(c Command, claim *resourceClaim) bool {
	if claim.Kind != c.Args.ActionKind || claim.Key != c.Args.ResourceKey ||
		claim.SessionID != c.Args.SessionID || claim.ServerName != c.Args.ServerName {
		return false
	}
	if claim.ExpiresAt != c.ExpiresAt {
		return false
	}
	old, _ := json.Marshal(claim.Expected)
	new, _ := json.Marshal(c.Args.Expected)
	return string(old) == string(new)
}

func (r *resourceService) claimAction(claim *resourceClaim) (bool, error) {
	path, err := r.claimPath(claim.ActionID)
	if err != nil {
		return false, err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return false, err
	}
	raw, err := json.Marshal(claim)
	if err != nil {
		return false, err
	}
	f, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if os.IsExist(err) {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	if _, err := f.Write(raw); err != nil {
		f.Close()
		return false, fmt.Errorf("claim write failed: %w", err)
	}
	if err := f.Sync(); err != nil {
		f.Close()
		return false, fmt.Errorf("claim sync failed: %w", err)
	}
	if err := f.Close(); err != nil {
		return false, fmt.Errorf("claim close failed: %w", err)
	}
	return true, nil
}

func (r *resourceService) finishClaim(claim *resourceClaim, state, result string) (string, string) {
	claim.State, claim.Result = state, result
	claim.FinishedAt = time.Now().UTC().Format(time.RFC3339Nano)
	path, err := r.claimPath(claim.ActionID)
	if err != nil {
		return "uncertain", "claim result could not be persisted"
	}
	raw, err := json.Marshal(claim)
	if err != nil {
		return "uncertain", "claim result could not be persisted"
	}
	if err := writePrivateAtomic(path, raw); err != nil {
		return "uncertain", "claim result could not be persisted"
	}
	return state, result
}

func (r *resourceService) recentActions(limit int) []map[string]any {
	entries, err := os.ReadDir(filepath.Join(r.dir, "actions"))
	if err != nil {
		return []map[string]any{}
	}
	type observedClaim struct {
		claim resourceClaim
		at    time.Time
	}
	claims := []observedClaim{}
	for _, e := range entries {
		if e.IsDir() || filepath.Ext(e.Name()) != ".json" || strings.HasPrefix(e.Name(), ".") {
			continue
		}
		id := strings.TrimSuffix(e.Name(), ".json")
		if !resourceUUIDShape.MatchString(id) {
			continue
		}
		claim, exists, err := r.readClaim(id)
		if err != nil || !exists {
			continue
		}
		stamp := claim.FinishedAt
		if stamp == "" {
			stamp = claim.ClaimedAt
		}
		at, err := time.Parse(time.RFC3339Nano, stamp)
		if err != nil {
			continue
		}
		claims = append(claims, observedClaim{*claim, at})
	}
	sort.Slice(claims, func(i, j int) bool {
		if claims[i].at.Equal(claims[j].at) {
			return claims[i].claim.ActionID < claims[j].claim.ActionID
		}
		return claims[i].at.After(claims[j].at)
	})
	out := []map[string]any{}
	for _, item := range claims {
		if len(out) >= limit {
			break
		}
		claim := item.claim
		out = append(out, map[string]any{
			"action_id":   claim.ActionID,
			"target":      claim.Key,
			"action_kind": claim.Kind,
			"state":       claim.State,
			"result":      claim.Result,
			"finished_at": claim.FinishedAt,
		})
	}
	return out
}

func (r *resourceService) emitAction(c Command, actionID, state, result, errText string) {
	payload := map[string]any{"action_id": actionID, "state": state}
	if result != "" {
		payload["result"] = result
	}
	if errText != "" {
		payload["error"] = errText
	}
	r.sp.Emit(Event{Kind: "resource_action", CmdID: c.ID, Payload: payload})
}

func commandExpiry(c Command) (time.Time, error) {
	if c.ExpiresAt == "" {
		return time.Time{}, errors.New("resource_apply needs an expiry")
	}
	due, err := time.Parse(time.RFC3339, c.ExpiresAt)
	if err != nil {
		return time.Time{}, errors.New("expiry is not an RFC3339 timestamp")
	}
	now := time.Now()
	if !due.After(now) {
		return due, errors.New("action expired")
	}
	if due.After(now.Add(resourceActionTTL)) {
		return due, errors.New("expiry exceeds the action TTL")
	}
	return due, nil
}

func validateApplyArgs(c Command) error {
	args := c.Args
	if !resourceActionKinds[args.ActionKind] {
		return fmt.Errorf("unknown resource action %q", args.ActionKind)
	}
	if !resourceUUIDShape.MatchString(args.ActionID) || args.ActionID != c.ID {
		return errors.New("action_id must be the command's own UUID")
	}
	if args.ResourceKey == "" {
		return errors.New("resource_key is required")
	}
	exp := args.Expected
	if exp == nil {
		return errors.New("expected identity is required")
	}
	if args.ActionKind == "stop_process" {
		expKey, _ := exp["key"].(string)
		if expKey == "" || expKey != args.ResourceKey {
			return errors.New("expected identity does not match resource_key")
		}
		if _, err := parseExpected(exp); err != nil {
			return err
		}
	} else {
		expKey, _ := exp["key"].(string)
		if expKey != "" && expKey != args.ResourceKey {
			return errors.New("expected identity does not match resource_key")
		}
		if args.SessionID == "" || args.ServerName == "" {
			return errors.New("mcp actions need session_id and server_name")
		}
	}
	return nil
}

func (r *resourceService) Apply(c Command) error {
	args := c.Args
	fail := func(state, text string) error {
		r.emitAction(c, args.ActionID, state, "", text)
		if state == "failed" || state == "uncertain" {
			return errors.New(text)
		}
		return nil
	}
	if err := validateApplyArgs(c); err != nil {
		return fail("failed", err.Error())
	}
	claim, exists, err := r.readClaim(args.ActionID)
	if err != nil {
		return fail("uncertain", err.Error())
	}
	if exists {
		if !claimMatches(c, claim) {
			return fail("failed", "action_id is claimed for a different action")
		}
		switch claim.State {
		case "completed", "still_running":
			r.emitAction(c, claim.ActionID, claim.State, claim.Result, "")
			return nil
		case "failed":
			r.emitAction(c, claim.ActionID, "failed", claim.Result, "")
			return errors.New(claim.Result)
		default:
			r.emitAction(c, claim.ActionID, "uncertain", claim.Result, "")
			return errors.New("action claim is incomplete; outcome is uncertain")
		}
	}
	if _, err := commandExpiry(c); err != nil {
		return fail("failed", err.Error())
	}
	fresh := &resourceClaim{
		ActionID: args.ActionID, Kind: args.ActionKind, Key: args.ResourceKey,
		Expected: args.Expected, SessionID: args.SessionID, ServerName: args.ServerName,
		ExpiresAt: c.ExpiresAt,
		State:     "claimed",
		ClaimedAt: time.Now().UTC().Format(time.RFC3339Nano),
	}
	taken, err := r.claimAction(fresh)
	if err != nil {
		return fail("failed", err.Error())
	}
	if !taken {
		return r.Apply(c)
	}
	state, result := r.execute(c, fresh)
	state, result = r.finishClaim(fresh, state, result)
	if state == "failed" {
		r.emitAction(c, fresh.ActionID, "failed", "", result)
		return errors.New(result)
	}
	r.emitAction(c, fresh.ActionID, state, result, "")
	return nil
}

type expectedIdentity struct {
	key        string
	pid        int
	startTicks uint64
	namespace  string
	uid        int
	exe        string
}

func parseExpected(exp map[string]any) (expectedIdentity, error) {
	out := expectedIdentity{}
	out.key, _ = exp["key"].(string)
	pid, ok := exp["pid"].(float64)
	if !ok || math.IsNaN(pid) || math.IsInf(pid, 0) || pid < 1 ||
		pid > math.MaxInt32 || pid != math.Trunc(pid) {
		return out, errors.New("expected pid is invalid")
	}
	out.pid = int(pid)
	start, ok := exp["start_ticks"].(string)
	if !ok || start == "" {
		return out, errors.New("expected start_ticks is invalid")
	}
	ticks, err := strconv.ParseUint(start, 10, 64)
	if err != nil || strconv.FormatUint(ticks, 10) != start {
		return out, errors.New("expected start_ticks is invalid")
	}
	out.startTicks = ticks
	out.namespace, _ = exp["namespace"].(string)
	uid, ok := exp["uid"].(float64)
	if !ok || math.IsNaN(uid) || math.IsInf(uid, 0) || uid < 0 ||
		uid > math.MaxUint32-1 || uid != math.Trunc(uid) {
		return out, errors.New("expected uid is invalid")
	}
	out.uid = int(uid)
	out.exe, _ = exp["exe_identity"].(string)
	if out.key == "" || out.namespace == "" || out.exe == "" {
		return out, errors.New("expected identity is incomplete")
	}
	return out, nil
}

func (r *resourceService) execute(c Command, claim *resourceClaim) (string, string) {
	switch claim.Kind {
	case "stop_process":
		return r.executeStop(c, claim)
	case "disconnect_mcp", "reconnect_mcp":
		return r.executeMCP(c, claim)
	}
	return "failed", "unknown action kind"
}

func expiryStillValid(c Command) error {
	due, err := time.Parse(time.RFC3339, c.ExpiresAt)
	if err != nil {
		return errors.New("expiry is not an RFC3339 timestamp")
	}
	if !due.After(time.Now()) {
		return errors.New("action expired")
	}
	return nil
}

func (r *resourceService) executeStop(c Command, claim *resourceClaim) (string, string) {
	exp, err := parseExpected(claim.Expected)
	if err != nil {
		return "failed", err.Error()
	}
	snap := r.Scan()
	if !snap.Complete {
		return "uncertain", "a fresh scan could not verify the target"
	}
	var row *ResourceRow
	for i := range snap.Processes {
		if snap.Processes[i].Key == exp.key {
			row = &snap.Processes[i]
			break
		}
	}
	if row == nil {
		return "failed", "the confirmed process is no longer in the inventory"
	}
	if !row.Manageable || row.Protected {
		return "failed", "the target is protected or not manageable"
	}
	if row.PID != exp.pid || row.StartTicks != exp.startTicks ||
		row.Namespace != exp.namespace || row.UID != exp.uid || row.ExeIdentity != exp.exe {
		return "failed", "the process identity changed since the confirmation"
	}
	pidfd, err := pidfdOpen(row.PID)
	if err != nil {
		return "failed", fmt.Sprintf("pidfd_open: %v", err)
	}
	defer closeFD(pidfd)
	if !verifyResourceIdentity(r.proc, &row.ResourceProcess) {
		return "failed", "the process changed while it was being bound"
	}
	if r.beforeSideEffect != nil {
		r.beforeSideEffect()
	}
	if err := expiryStillValid(c); err != nil {
		return "failed", err.Error()
	}
	if err := pidfdSendSignal(pidfd, sigTERM); err != nil {
		return "failed", fmt.Sprintf("pidfd_send_signal: %v", err)
	}
	deadline := time.Now().Add(resourceStopWait)
	for {
		gone, pollErr := pidfdPollExit(pidfd, deadline)
		if pollErr == nil && gone {
			return "completed", "process exited after SIGTERM"
		}
		if pollErr != nil {
			return "uncertain", "pidfd poll failed after the signal"
		}
		if resourceProcessGone(r.proc, row.PID, row.StartTicks) {
			return "completed", "process exited after SIGTERM"
		}
		if time.Now().After(deadline) {
			return "still_running", "SIGTERM was delivered but the process is still running"
		}
		time.Sleep(50 * time.Millisecond)
	}
}

func resourceProcessGone(root string, pid int, start uint64) bool {
	raw, err := os.ReadFile(filepath.Join(root, strconv.Itoa(pid), "stat"))
	if err != nil {
		return errors.Is(err, os.ErrNotExist)
	}
	stat, err := parseResourceStat(string(raw))
	if err != nil {
		return false
	}
	if stat.pid != pid || stat.startTicks != start {
		return true
	}
	return stat.state == "Z"
}

func (r *resourceService) executeMCP(c Command, claim *resourceClaim) (string, string) {
	session := r.mgr.get(claim.SessionID)
	if session == nil {
		return "failed", "the owning session is not running"
	}
	if session.resourceAgent {
		return "failed", "management agent servers cannot be acted on"
	}
	owner, err := r.sessionOwnerKey(session)
	if err != nil || !strings.HasPrefix(claim.Key, "mcp:"+owner+":") {
		return "failed", "the session generation changed since the confirmation"
	}
	status, err := r.requestMCPStatus(session, owner)
	if err != nil {
		return "uncertain", "the owning session's status could not be refreshed"
	}
	servers, _ := status["mcpServers"].([]any)
	found := false
	for _, s := range servers {
		row, ok := s.(map[string]any)
		if !ok {
			return "uncertain", "the owning session's status was malformed"
		}
		key := "mcp:" + owner + ":" + base64.RawURLEncoding.EncodeToString(
			[]byte(fmt.Sprint(row["name"])))
		if key == claim.Key && row["name"] == claim.ServerName {
			found = true
		}
	}
	if !found {
		return "failed", "the confirmed MCP server is no longer in the session's status"
	}
	if r.beforeSideEffect != nil {
		r.beforeSideEffect()
	}
	if err := expiryStillValid(c); err != nil {
		return "failed", err.Error()
	}
	subtype := "mcp_toggle"
	if claim.Kind == "reconnect_mcp" {
		subtype = "mcp_reconnect"
	}
	state, err := r.mcpAction(session, subtype, claim.ServerName)
	if err != nil {
		return state, sanitizeControlError(err)
	}
	return "completed", "control request acknowledged and status verified"
}

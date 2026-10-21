package main

import (
	"bufio"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"net"
	"os"
	"strconv"
	"strings"
	"time"
)

const (
	gatewayMaxRequest  = 64 * 1024
	gatewayMaxResponse = 4 << 20
	resourceActionRows = 20
)

var gatewayTools = resourceMCPToolDefinitions()

func (r *resourceService) RunGateway() {
	if r.tokenErr != nil {
		log.Printf("resource gateway: no token (%v); not serving", r.tokenErr)
		return
	}
	os.Remove(r.socket)
	ln, err := net.Listen("unix", r.socket)
	if err != nil {
		log.Printf("resource gateway: %v", err)
		return
	}
	os.Chmod(r.socket, 0o600)
	for {
		conn, err := ln.Accept()
		if err != nil {
			log.Printf("resource gateway accept: %v", err)
			return
		}
		go r.serveGateway(conn)
	}
}

func (r *resourceService) serveGateway(conn net.Conn) {
	defer conn.Close()
	conn.SetDeadline(time.Now().Add(30 * time.Second))
	sc := bufio.NewScanner(conn)
	sc.Buffer(make([]byte, 1<<10), gatewayMaxRequest)
	w := bufio.NewWriter(conn)
	defer w.Flush()
	for sc.Scan() {
		line := sc.Bytes()
		var req struct {
			JSONRPC string         `json:"jsonrpc"`
			ID      any            `json:"id"`
			Token   string         `json:"token"`
			Method  string         `json:"method"`
			Params  map[string]any `json:"params"`
		}
		if err := json.Unmarshal(line, &req); err != nil {
			r.gatewayWrite(w, nil, nil, "parse error")
			continue
		}
		if req.Token != r.token {
			r.gatewayWrite(w, req.ID, nil, "unauthorized")
			continue
		}
		result, errText := r.gatewayCall(req.Method, req.Params)
		r.gatewayWrite(w, req.ID, result, errText)
	}
}

func (r *resourceService) gatewayWrite(w *bufio.Writer, id any, result any, errText string) {
	resp := map[string]any{"jsonrpc": "2.0", "id": id}
	if errText != "" {
		resp["error"] = map[string]any{"code": -32000, "message": errText}
	} else {
		resp["result"] = result
	}
	raw, _ := json.Marshal(resp)
	if len(raw) > gatewayMaxResponse {
		raw, _ = json.Marshal(map[string]any{"jsonrpc": "2.0", "id": id,
			"error": map[string]any{"code": -32000, "message": "response too large"}})
	}
	w.Write(append(raw, '\n'))
	w.Flush()
}

func (r *resourceService) gatewayCall(method string, params map[string]any) (any, string) {
	switch method {
	case "initialize":
		return map[string]any{
			"protocolVersion": "2025-06-18",
			"capabilities":    map[string]any{"tools": map[string]any{}},
			"serverInfo":      map[string]any{"name": "flightdeck-resources", "version": "1"},
		}, ""
	case "ping":
		return map[string]any{}, ""
	case "tools/list":
		return map[string]any{"tools": gatewayTools}, ""
	case "tools/call":
		name, _ := params["name"].(string)
		args, _ := params["arguments"].(map[string]any)
		if args == nil {
			args = map[string]any{}
		}
		out, err := r.gatewayTool(name, args)
		if err != nil {
			return nil, err.Error()
		}
		raw, _ := json.Marshal(out)
		return map[string]any{
			"content": []map[string]any{{"type": "text", "text": string(raw)}},
		}, ""
	default:
		if strings.HasPrefix(method, "notifications/") {
			return map[string]any{}, ""
		}
		return nil, fmt.Sprintf("unknown method %q", method)
	}
}

func (r *resourceService) gatewayTool(name string, args map[string]any) (any, error) {
	switch name {
	case "list_resources":
		if len(args) != 0 {
			return nil, errors.New("list_resources takes no arguments")
		}
		snap := r.ScanProcesses()
		return map[string]any{
			"snapshot":       compactSnapshot(snap),
			"recent_actions": r.recentActions(resourceActionRows),
		}, nil
	case "inspect_resource":
		if err := onlyArgs(args, "resource_key"); err != nil {
			return nil, err
		}
		key, _ := args["resource_key"].(string)
		if len(key) < 1 || len(key) > 1024 {
			return nil, errors.New("inspect_resource needs a resource_key of 1..1024 chars")
		}
		return r.inspectResource(key)
	case "propose_action":
		if err := onlyArgs(args, "resource_key", "action_kind", "reason"); err != nil {
			return nil, err
		}
		kind, _ := args["action_kind"].(string)
		key, _ := args["resource_key"].(string)
		reason, _ := args["reason"].(string)
		return r.proposeAction(kind, key, reason)
	default:
		return nil, fmt.Errorf("unknown tool %q", name)
	}
}

var compactRowFields = map[string]bool{
	"key": true, "pid": true, "comm": true, "state": true,
	"harness": true, "role": true, "age_seconds": true, "rss_bytes": true,
	"manageable": true, "protected": true, "orphan": true,
	"protection_reason": true, "socket_count": true, "stdio_peer_key": true,
	"session_id": true, "harness_evidence": true, "managed": true,
}

func compactRow(row ResourceRow) map[string]any {
	raw, _ := json.Marshal(row)
	var full map[string]any
	json.Unmarshal(raw, &full)
	compact := map[string]any{}
	for field := range compactRowFields {
		if v, ok := full[field]; ok {
			compact[field] = v
		}
	}
	return compact
}

func compactSnapshot(snap *resourceSnapshot) map[string]any {
	roots := []map[string]any{}
	children := map[string][]string{}
	for _, row := range snap.Processes {
		if row.Role == "child" {
			owner := row.OwnerKey
			if owner == "" {
				owner = "unattributed"
			}
			children[owner] = append(children[owner], row.Key)
			continue
		}
		roots = append(roots, compactRow(row))
	}
	return map[string]any{
		"schema_version": snap.SchemaVersion,
		"available":      snap.Available,
		"complete":       snap.Complete,
		"error":          snap.Error,
		"boot_id":        snap.BootID,
		"taken_at":       snap.TakenAt,
		"processes":      roots,
		"children":       children,
		"counts": map[string]int{
			"rows": len(snap.Processes), "roots": len(roots),
		},
		"mcp_servers": snap.MCPServers,
		"warnings":    snap.Warnings,
		"compacted":   true,
	}
}

func onlyArgs(args map[string]any, allowed ...string) error {
	set := map[string]bool{}
	for _, name := range allowed {
		set[name] = true
	}
	for key := range args {
		if !set[key] {
			return fmt.Errorf("unknown argument %q", key)
		}
	}
	return nil
}

func (r *resourceService) inspectResource(key string) (any, error) {
	snap := r.ScanProcesses()
	if !snap.Complete {
		return nil, errors.New("resource inventory is unavailable")
	}
	for _, row := range snap.Processes {
		if row.Key == key {
			return map[string]any{"resource": row}, nil
		}
	}
	for _, row := range snap.MCPServers {
		if row["key"] == key {
			return map[string]any{"mcp_server": row}, nil
		}
	}
	return nil, fmt.Errorf("no live resource under key %q", key)
}

func (r *resourceService) proposeAction(kind, key, reason string) (any, error) {
	if !resourceActionKinds[kind] {
		return nil, fmt.Errorf("unknown action kind %q", kind)
	}
	if len(key) < 1 || len(key) > 1024 {
		return nil, errors.New("resource_key must be 1..1024 chars")
	}
	if len(reason) < 1 || len(reason) > 1000 {
		return nil, errors.New("reason must be 1..1000 chars")
	}
	snap := r.ScanProcesses()
	if !snap.Complete {
		return nil, errors.New("resource inventory is unavailable")
	}
	target := false
	protected := false
	sessionID, serverName := "", ""
	var expected map[string]any
	if kind == "stop_process" {
		for _, row := range snap.Processes {
			if row.Key == key {
				target = true
				protected = row.Protected || !row.Manageable
				expected = map[string]any{
					"key":          row.Key,
					"pid":          row.PID,
					"start_ticks":  strconvStart(row.StartTicks),
					"namespace":    row.Namespace,
					"uid":          row.UID,
					"exe_identity": row.ExeIdentity,
				}
			}
		}
	} else {
		for _, row := range snap.MCPServers {
			if row["key"] == key {
				target = true
				protected, _ = row["protected"].(bool)
				sessionID, _ = row["session_id"].(string)
				serverName, _ = row["name"].(string)
				expected = map[string]any{
					"key":         key,
					"session_id":  sessionID,
					"server_name": serverName,
				}
			}
		}
	}
	if !target {
		return nil, fmt.Errorf("no live resource under key %q", key)
	}
	if protected {
		return nil, errors.New("that resource is protected; it cannot be acted on")
	}
	proposalID := newUUID()
	payload := map[string]any{
		"proposal_id":  proposalID,
		"action_kind":  kind,
		"resource_key": key,
		"expected":     expected,
		"reason":       reason,
	}
	if sessionID != "" {
		payload["session_id"] = sessionID
	}
	if serverName != "" {
		payload["server_name"] = serverName
	}
	r.sp.Emit(Event{Kind: "resource_proposal", Payload: payload})
	return map[string]any{
		"proposal_id": proposalID,
		"recorded":    true,
		"note":        resourceProposalRecordedMessage,
	}, nil
}

func strconvStart(ticks uint64) string {
	return strconv.FormatUint(ticks, 10)
}

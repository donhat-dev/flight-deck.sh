package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"time"
)

// The control channel rides the same stdin/stdout as the turns. The CLI asks
// the host for a permission decision with a control_request of subtype
// can_use_tool and waits; the host answers with a control_response. The host
// changes the permission mode or interrupts the turn with control_requests of
// its own, which the CLI acknowledges the same way. This is the protocol the
// Agent SDK speaks, enabled by --permission-prompt-tool stdio.

type permRequest struct {
	ID          string
	ToolName    string
	Input       map[string]any
	Suggestions []map[string]any
	Since       time.Time
	owner       *session
}

type controlRequest struct {
	Type      string `json:"type"`
	RequestID string `json:"request_id"`
	Request   struct {
		Subtype               string           `json:"subtype"`
		ToolName              string           `json:"tool_name"`
		Input                 map[string]any   `json:"input"`
		ToolUseID             string           `json:"tool_use_id"`
		PermissionSuggestions []map[string]any `json:"permission_suggestions"`
		BlockedPath           string           `json:"blocked_path"`
		Title                 string           `json:"title"`
		Description           string           `json:"description"`
	} `json:"request"`
}

type controlResponse struct {
	Type     string `json:"type"`
	Response struct {
		Subtype   string         `json:"subtype"`
		RequestID string         `json:"request_id"`
		Response  map[string]any `json:"response"`
		Error     string         `json:"error"`
	} `json:"response"`
}

func (m *Manager) writeLine(s *session, v any) error {
	line, err := json.Marshal(v)
	if err != nil {
		return err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	_, err = s.stdin.Write(append(line, '\n'))
	return err
}

func (m *Manager) respond(s *session, requestID string, payload map[string]any, errText string) {
	var resp controlResponse
	resp.Type = "control_response"
	resp.Response.RequestID = requestID
	if errText != "" {
		resp.Response.Subtype = "error"
		resp.Response.Error = errText
	} else {
		resp.Response.Subtype = "success"
		resp.Response.Response = payload
	}
	if err := m.writeLine(s, resp); err != nil {
		log.Printf("%s control_response: %v", s.id[:8], err)
	}
}

// ask records a permission request and tells Odoo about it. The CLI's turn
// stays parked until a `decide` command answers, or the timeout does.
func (m *Manager) ask(s *session, req controlRequest) {
	p := &permRequest{
		ID:          req.RequestID,
		ToolName:    req.Request.ToolName,
		Input:       req.Request.Input,
		Suggestions: req.Request.PermissionSuggestions,
		Since:       time.Now(),
		owner:       s,
	}
	m.pmu.Lock()
	m.pending[p.ID] = p
	m.pmu.Unlock()
	m.shareTranscript(s)
	m.sp.Emit(Event{Kind: "permission", SessionID: s.id, Payload: map[string]any{
		"request_id":   p.ID,
		"tool_name":    p.ToolName,
		"tool_use_id":  req.Request.ToolUseID,
		"input":        p.Input,
		"title":        req.Request.Title,
		"description":  req.Request.Description,
		"can_remember": len(keepable(p.Suggestions)) > 0,
		"blocked_path": req.Request.BlockedPath,
	}})
	log.Printf("%s waiting on %s (%s)", s.id[:8], p.ToolName, p.ID)
}

// handleControl reads one control_request from the CLI. Only can_use_tool
// needs an answer from a person; the rest are acknowledged so the CLI is
// never left waiting on a subtype this runner does not implement.
func (m *Manager) handleControl(s *session, raw []byte) {
	var req controlRequest
	if err := json.Unmarshal(raw, &req); err != nil {
		log.Printf("%s bad control_request: %v", s.id[:8], err)
		return
	}
	switch req.Request.Subtype {
	case "can_use_tool":
		m.ask(s, req)
	default:
		m.respond(s, req.RequestID, nil,
			fmt.Sprintf("fd-runner does not implement %q", req.Request.Subtype))
	}
}

// keepable is what "do not ask me again" may carry: the rule for the call, and
// the directory it reaches outside the working one. A call blocked by its path
// keeps being asked when only the rule is remembered, because the two are
// separate gates.
//
// setMode is deliberately dropped. The CLI offers it as a shortcut, but a
// person answering for one command has not asked for every later edit to go
// through unasked; the mode selector is where that choice is made.
func keepable(suggestions []map[string]any) []map[string]any {
	var out []map[string]any
	for _, s := range suggestions {
		switch t, _ := s["type"].(string); t {
		case "addRules", "addDirectories":
			out = append(out, s)
		}
	}
	return out
}

// remember copies those suggestions with the destination the person chose:
// "session" lasts as long as the process, "localSettings" is written to the
// project's settings file and survives it.
func remember(suggestions []map[string]any, destination string) []map[string]any {
	var out []map[string]any
	for _, s := range keepable(suggestions) {
		copied := map[string]any{}
		for k, v := range s {
			copied[k] = v
		}
		copied["destination"] = destination
		out = append(out, copied)
	}
	return out
}

// Decide answers one waiting permission request.
func (m *Manager) Decide(c Command) error {
	if c.Args.RequestID == "" {
		return errors.New("decide needs a request_id")
	}
	m.pmu.Lock()
	p := m.pending[c.Args.RequestID]
	delete(m.pending, c.Args.RequestID)
	m.pmu.Unlock()
	if p == nil {
		return errors.New("no permission request is waiting under that id")
	}
	m.answer(p, c.Args.Behavior, c.Args.Scope, c.Args.Message, c.Args.UpdatedInput)
	return nil
}

func (m *Manager) answer(p *permRequest, behavior, scope, message string, updated map[string]any) {
	payload := map[string]any{}
	if behavior == "allow" {
		input := updated
		if input == nil {
			input = p.Input
		}
		payload["behavior"] = "allow"
		// An allow with no updatedInput was rejected as invalid by older CLI
		// versions, so the original input is always echoed back.
		payload["updatedInput"] = input
		switch scope {
		case "session":
			if rules := remember(p.Suggestions, "session"); len(rules) > 0 {
				payload["updatedPermissions"] = rules
			}
		case "always":
			if rules := remember(p.Suggestions, "localSettings"); len(rules) > 0 {
				payload["updatedPermissions"] = rules
			}
		}
	} else {
		if message == "" {
			message = "Denied in Odoo."
		}
		payload["behavior"] = "deny"
		payload["message"] = message
	}
	m.respond(p.owner, p.ID, payload, "")
	m.sp.Emit(Event{Kind: "decision", SessionID: p.owner.id, Payload: map[string]any{
		"request_id": p.ID,
		"behavior":   payload["behavior"],
		"scope":      scope,
		"remembered": payload["updatedPermissions"] != nil,
		"waited_ms":  time.Since(p.Since).Milliseconds(),
	}})
}

// SetMode switches the permission mode of a running process, the way the
// terminal does between turns.
func (m *Manager) SetMode(c Command) error {
	s := m.get(c.SessionID)
	if s == nil {
		return errors.New("session is not running")
	}
	if c.Args.Mode == "" {
		return errors.New("set_mode needs a mode")
	}
	req := map[string]any{
		"type":       "control_request",
		"request_id": fmt.Sprintf("fd_%d", time.Now().UnixNano()),
		"request":    map[string]any{"subtype": "set_permission_mode", "mode": c.Args.Mode},
	}
	if err := m.writeLine(s, req); err != nil {
		return err
	}
	m.sp.Emit(Event{Kind: "mode", SessionID: s.id, Payload: map[string]any{"mode": c.Args.Mode}})
	return nil
}

// SetModel switches the model of a running process. An empty name means the
// account's own default, which the protocol carries as null.
func (m *Manager) SetModel(c Command) error {
	s := m.get(c.SessionID)
	if s == nil {
		return errors.New("session is not running")
	}
	var model any
	if c.Args.Model != "" {
		model = c.Args.Model
	}
	req := map[string]any{
		"type":       "control_request",
		"request_id": fmt.Sprintf("fd_%d", time.Now().UnixNano()),
		"request":    map[string]any{"subtype": "set_model", "model": model},
	}
	if err := m.writeLine(s, req); err != nil {
		return err
	}
	m.sp.Emit(Event{Kind: "model", SessionID: s.id, Payload: map[string]any{"model": c.Args.Model}})
	return nil
}

// SetEffort changes how hard the model works. The control channel carries no
// subtype for it, so it goes the way the terminal does it: the CLI's own
// command, sent as a turn. It costs one turn and the CLI answers in the
// transcript.
func (m *Manager) SetEffort(c Command) error {
	if c.Args.Effort == "" {
		return errors.New("set_effort needs a level")
	}
	s := m.get(c.SessionID)
	if s == nil {
		return errors.New("session is not running")
	}
	line := map[string]any{
		"type": "user",
		"message": map[string]any{
			"role":    "user",
			"content": []map[string]string{{"type": "text", "text": "/effort " + c.Args.Effort}},
		},
	}
	if err := m.writeLine(s, line); err != nil {
		return err
	}
	m.sp.Emit(Event{Kind: "effort", SessionID: s.id, Payload: map[string]any{"effort": c.Args.Effort}})
	return nil
}

// expirePermissions denies whatever nobody answered in time. A parked turn
// holds the process for as long as the request waits, so this is the only
// thing that keeps a forgotten approval from parking it for good.
func (m *Manager) expirePermissions() {
	m.pmu.Lock()
	var stale []*permRequest
	for id, p := range m.pending {
		if time.Since(p.Since) > m.permTimeout {
			stale = append(stale, p)
			delete(m.pending, id)
		}
	}
	m.pmu.Unlock()
	for _, p := range stale {
		log.Printf("%s permission %s expired", p.owner.id[:8], p.ID)
		m.answer(p, "deny", "", "Nobody answered this request in Odoo in time.", nil)
	}
}

// dropPermissions forgets what a dead process was waiting on.
func (m *Manager) dropPermissions(s *session) {
	m.pmu.Lock()
	defer m.pmu.Unlock()
	for id, p := range m.pending {
		if p.owner == s {
			delete(m.pending, id)
			m.sp.Emit(Event{Kind: "decision", SessionID: s.id, Payload: map[string]any{
				"request_id": id,
				"behavior":   "deny",
				"reason":     "the process stopped before this was answered",
			}})
		}
	}
}

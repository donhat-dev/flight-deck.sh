package runner

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"
)

// TestSpoolContractSamples writes one of each file type the spool protocol
// defines into the directory FD_SPOOL_SAMPLES names, so the conformance suite
// (api/conformance) can validate them against api/schemas/spool. Skipped when
// the variable is unset; `make contracts` sets it to a dir under the worktree
// (which is also the build container's /src bind mount).
func TestSpoolContractSamples(t *testing.T) {
	out := os.Getenv("FD_SPOOL_SAMPLES")
	if out == "" {
		t.Skip("FD_SPOOL_SAMPLES unset; `make contracts` sets it")
	}
	spool := filepath.Join(t.TempDir(), "spool")
	sp, err := OpenSpool(spool)
	if err != nil {
		t.Fatal(err)
	}
	sid := "11111111-2222-3333-4444-555555555555"
	ts := time.Now().UTC().Format(time.RFC3339Nano)

	// A command file as Odoo writes one (the runner only reads cmd/, but the
	// wire shape is the runner's own Command struct).
	tools := []string{"Bash", "Read"}
	cmd := Command{
		ID: "9f1c2a3b-0000-4000-8000-aaaaaaaaaaaa", Seq: 1, Op: "start",
		SessionID: sid, ExpiresAt: time.Now().UTC().Add(time.Hour).Format(time.RFC3339),
		Args: CommandArgs{
			Cwd: "/p", Resume: true, AllowLive: true, Text: "hello",
			PermissionMode: "acceptEdits", AllowedTools: []string{"Bash"},
			Model: "claude-opus-5-5", Name: "fixture", Effort: "high",
			JSONSchema: "{}", MaxBudgetUSD: 1.5, MaxTurns: 10, Tools: &tools,
		},
	}
	raw, err := json.MarshalIndent(cmd, "", "  ")
	if err != nil {
		t.Fatal(err)
	}
	cmdFile := filepath.Join(spool, "cmd", "000000000001-"+cmd.ID+".json")
	if err := os.WriteFile(cmdFile, raw, 0o664); err != nil {
		t.Fatal(err)
	}

	// Events: one per kind the protocol defines, plus a refusal and a denial.
	sp.Emit(Event{Kind: "ack", CmdID: cmd.ID, SessionID: sid})
	sp.Emit(Event{Kind: "ack", CmdID: "deadbeef", SessionID: sid, Error: "send refused: expired"})
	sp.Emit(Event{Kind: "state", SessionID: sid, State: "busy", Payload: map[string]any{"pid": 4242}})
	sp.Emit(Event{Kind: "state", SessionID: sid, State: "idle", Payload: map[string]any{"pid": 4242}})
	sp.Emit(Event{Kind: "result", SessionID: sid, Payload: map[string]any{
		"result": "done", "total_cost_usd": 0.012, "is_error": false,
		"num_turns": 1, "duration_ms": 1500, "subtype": "success",
		"first_content_frame_ms": 120, "command": true,
		"usage": resultUsage{InputTokens: 3, OutputTokens: 5, CacheReadInputTokens: 100},
	}})
	sp.Emit(Event{Kind: "permission", SessionID: sid, Payload: map[string]any{
		"request_id": "r1", "tool_name": "Bash", "tool_use_id": "tu1",
		"input": map[string]any{"command": "ls"}, "title": "run ls",
		"description": "list files", "can_remember": true, "blocked_path": "",
	}})
	sp.Emit(Event{Kind: "decision", SessionID: sid, Payload: map[string]any{
		"request_id": "r1", "behavior": "allow", "scope": "once",
		"remembered": false, "waited_ms": 40,
	}})
	sp.Emit(Event{Kind: "decision", SessionID: sid, Payload: map[string]any{
		"request_id": "r2", "behavior": "deny", "reason": "permission timed out",
	}})
	sp.Emit(Event{Kind: "mode", SessionID: sid, Payload: map[string]any{"mode": "plan"}})
	sp.Emit(Event{Kind: "model", SessionID: sid, Payload: map[string]any{"model": "claude-opus-5-5"}})
	sp.Emit(Event{Kind: "effort", SessionID: sid, Payload: map[string]any{"effort": "high"}})
	sp.Emit(Event{Kind: "usage", CmdID: "c-usage", Payload: map[string]any{
		"text": "5h: 12%", "captured_at": time.Now().UTC().Format(time.RFC3339),
	}})

	// A live state file.
	sp.SetState(SessionState{SessionID: sid, PID: 4242, State: "busy",
		Cwd: "/p", Since: ts, Last: ts})

	// Deltas through the real writer path (accumulate, then flush on drop).
	dw := NewDeltaWriter(sp)
	dw.append(sid, streamKey{sessionID: "frame-sess"}, "hello ")
	dw.append(sid, streamKey{sessionID: "frame-sess"}, "world")
	dw.append(sid, streamKey{sessionID: "sub-sess", parent: "tu-sub"}, "subagent text")
	dw.DropSession(sid)

	// Copy the four file types under the names the conformance suite expects.
	copies := map[string]string{
		"command.json":  filepath.Join(spool, "cmd", filepath.Base(cmdFile)),
		"state.json":    filepath.Join(spool, "state", sid+".json"),
		"events.jsonl":  filepath.Join(spool, "events.jsonl"),
		"deltas.jsonl":  filepath.Join(spool, "deltas.jsonl"),
	}
	if err := os.MkdirAll(out, 0o775); err != nil {
		t.Fatal(err)
	}
	for name, src := range copies {
		b, err := os.ReadFile(src)
		if err != nil {
			t.Fatalf("expected %s was not produced: %v", name, err)
		}
		if err := os.WriteFile(filepath.Join(out, name), b, 0o664); err != nil {
			t.Fatal(err)
		}
	}
}

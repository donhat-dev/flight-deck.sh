package ingest

// The busy-time and turn cases of python/tests/test_ingest_dedupe_activity.py,
// with the same inputs and the same expected seconds.

import (
	"encoding/json"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"reflect"
	"testing"
	"time"

	"flightdeck/core/activity"
	"flightdeck/core/ledger"
)

func tsAt(seconds float64) string {
	base := time.Date(2026, 9, 10, 10, 0, 0, 0, time.UTC)
	return base.Add(time.Duration(seconds * float64(time.Second))).UTC().Format("2006-01-02T15:04:05.999Z")
}

type aOpt struct {
	session, req, tool, stop, agent, prompt string
	blocks                                  []string
}

// aLine is Python's _assistant: one response line with usage.
func aLine(uuid string, secs float64, msgID string, out int, o aOpt) string {
	blocks := o.blocks
	if blocks == nil {
		blocks = []string{"tool_use"}
	}
	tool := o.tool
	if tool == "" {
		tool = "Bash"
	}
	var content []any
	for _, b := range blocks {
		if b == "tool_use" {
			content = append(content, map[string]any{"type": "tool_use", "id": "t-" + uuid,
				"name": tool, "input": map[string]any{}})
		} else {
			content = append(content, map[string]any{"type": b, "text": "text " + uuid})
		}
	}
	session := o.session
	if session == "" {
		session = "s1"
	}
	req := o.req
	if req == "" {
		req = "r1"
	}
	obj := map[string]any{
		"type": "assistant", "uuid": uuid, "sessionId": session, "cwd": "/p",
		"timestamp": tsAt(secs), "requestId": req,
		"message": map[string]any{
			"id": msgID, "model": "claude-opus-5", "content": content,
			"stop_reason": nil,
			"usage": map[string]any{
				"input_tokens": 1, "cache_read_input_tokens": 1000, "output_tokens": out,
				"service_tier": "standard", "speed": "standard", "inference_geo": "global",
				"cache_creation": map[string]any{"ephemeral_5m_input_tokens": 0,
					"ephemeral_1h_input_tokens": 50}}},
	}
	if o.stop != "" {
		obj["message"].(map[string]any)["stop_reason"] = o.stop
	}
	if o.agent != "" {
		obj["agentId"] = o.agent
		obj["isSidechain"] = true
	}
	if o.prompt != "" {
		obj["promptId"] = o.prompt
	}
	b, _ := json.Marshal(obj)
	return string(b) + "\n"
}

// pLine is Python's _prompt.
func pLine(uuid string, secs float64, text, session, promptID string) string {
	b, _ := json.Marshal(map[string]any{
		"type": "user", "uuid": uuid, "sessionId": session, "cwd": "/p",
		"timestamp": tsAt(secs), "promptId": promptID, "turnOrigin": "human",
		"message": map[string]any{"role": "user", "content": text}})
	return string(b) + "\n"
}

// rLine is Python's _result: a tool_result user line.
func rLine(uuid string, secs float64, session, promptID, agent string) string {
	obj := map[string]any{
		"type": "user", "uuid": uuid, "sessionId": session, "timestamp": tsAt(secs),
		"promptId": promptID,
		"message": map[string]any{"role": "user", "content": []any{
			map[string]any{"type": "tool_result", "tool_use_id": "x", "content": "ok"}}}}
	if agent != "" {
		obj["agentId"] = agent
		obj["isSidechain"] = true
	}
	b, _ := json.Marshal(obj)
	return string(b) + "\n"
}

func busy(t *testing.T, s *ledger.Store, clause string, args ...any) float64 {
	t.Helper()
	var v float64
	if err := s.DB.QueryRow(`SELECT COALESCE(SUM(busy_seconds),0) FROM agent_activity`+clause,
		args...).Scan(&v); err != nil {
		t.Fatal(err)
	}
	return v
}

func nearSecs(t *testing.T, name string, got, want float64) {
	t.Helper()
	if math.Abs(got-want) > 1e-9 {
		t.Errorf("%s = %v, want %v", name, got, want)
	}
}

// billedRows returns (uuid, prompt_id) per response row, oldest first.
func billedRows(t *testing.T, s *ledger.Store) [][2]string {
	t.Helper()
	rows, err := s.DB.Query(`SELECT uuid, COALESCE(prompt_id,'') FROM responses ORDER BY ts`)
	if err != nil {
		t.Fatal(err)
	}
	defer rows.Close()
	var out [][2]string
	for rows.Next() {
		var u, p string
		if err := rows.Scan(&u, &p); err != nil {
			t.Fatal(err)
		}
		out = append(out, [2]string{u, p})
	}
	if err := rows.Err(); err != nil {
		t.Fatal(err)
	}
	return out
}

func TestAResumedSessionCopyAddsNoBilledRow(t *testing.T) {
	s, in, root := setup(t)
	f1 := filepath.Join(root, "-p", "s1.jsonl")
	write(t, f1, pLine("h1", 0, "fix the bug", "s1", "p1"),
		aLine("a1", 10, "msg_1", 100, aOpt{stop: "end_turn"}))
	mustSweep(t, in, root)
	old := time.Now().Add(-time.Hour)
	if err := os.Chtimes(f1, old, old); err != nil {
		t.Fatal(err)
	}
	// s2 resumes s1: history copied with new uuids and the new session id.
	f2 := filepath.Join(root, "-p", "s2.jsonl")
	write(t, f2,
		pLine("h1-copy", 0, "fix the bug", "s2", "p1"),
		aLine("a1-copy", 10, "msg_1", 100, aOpt{session: "s2", stop: "end_turn"}),
		pLine("h2", 4000, "next step", "s2", "p2"),
		aLine("a3", 4060, "msg_2", 50, aOpt{session: "s2", stop: "end_turn"}))
	mustSweep(t, in, root)
	got := billedRows(t, s)
	if len(got) != 2 || got[0][0] != "a1" || got[1][0] != "a3" {
		t.Fatalf("billed %v", got)
	}
	// The copied turn stays with the session that did the work.
	var sid string
	if err := s.DB.QueryRow(`SELECT session_id FROM turns WHERE turn_id='p1'`).Scan(&sid); err != nil {
		t.Fatal(err)
	}
	if sid != "s1" {
		t.Fatalf("turn p1 owned by %s", sid)
	}
	// Copied history is not counted again: s2 counts only its own turn.
	nearSecs(t, "s2 busy", busy(t, s, ` WHERE session_id=?`, "s2"), 60)
}

func TestATurnRecordsItsPromptEndAndBilledRows(t *testing.T) {
	s, in, root := setup(t)
	write(t, filepath.Join(root, "-p", "s1.jsonl"),
		pLine("h1", 0, "add a chart", "s1", "p1"),
		aLine("a1", 10, "msg_1", 20, aOpt{}),
		rLine("r1", 20, "s1", "p1", ""),
		aLine("a2", 30, "msg_2", 40, aOpt{blocks: []string{"text"}, stop: "end_turn"}))
	mustSweep(t, in, root)
	var head, ended, final string
	if err := s.DB.QueryRow(`SELECT prompt_head, ended_at, final_head FROM turns WHERE turn_id='p1'`).
		Scan(&head, &ended, &final); err != nil {
		t.Fatal(err)
	}
	if head != "add a chart" || ended != tsAt(30) || final != "text a2" {
		t.Fatalf("turn p1: head %q ended %q final %q", head, ended, final)
	}
	for _, r := range billedRows(t, s) {
		if r[1] != "p1" {
			t.Fatalf("row %s stamped %q", r[0], r[1])
		}
	}
}

func TestMachineLinesDoNotOpenATurn(t *testing.T) {
	base := activity.Line{Type: "user", SessionID: "s1", Timestamp: "2026-09-10T10:00:00.000Z",
		PromptID: "x"}
	for _, text := range []string{"<command-name>/usage</command-name>", "[Request interrupted by user]",
		"Caveat: The messages below",
		"This session is being continued from a previous conversation that ran out of context."} {
		l := base
		l.Message = json.RawMessage(fmt.Sprintf(`{"content":%q}`, text))
		if activity.IsHumanPrompt(l) {
			t.Errorf("machine line opened a turn: %q", text)
		}
	}
	l := base
	l.Message = json.RawMessage(`{"content":"hello"}`)
	if !activity.IsHumanPrompt(l) {
		t.Error("a real prompt was not seen as human")
	}
	l.TurnOrigin = json.RawMessage(`"task_notification"`)
	if activity.IsHumanPrompt(l) {
		t.Error("a task notification opened a turn")
	}
}

func TestAPromptBehindIDEContextOpensATurn(t *testing.T) {
	s, in, root := setup(t)
	ide := activity.Line{Type: "user", UUID: "h1", SessionID: "s1", CWD: "/p", PromptID: "p1",
		Timestamp: tsAt(0), Message: json.RawMessage(`{"role":"user","content":[
			{"type":"text","text":"<ide_opened_file>The user opened a.py</ide_opened_file>"},
			{"type":"text","text":"why does this fail?"}]}`)}
	if !activity.IsHumanPrompt(ide) {
		t.Fatal("prompt behind ide context not seen as human")
	}
	onlyIDE := ide
	onlyIDE.Message = json.RawMessage(`{"content":[{"type":"text","text":"<ide_opened_file>The user opened a.py</ide_opened_file>"}]}`)
	if activity.IsHumanPrompt(onlyIDE) {
		t.Fatal("an ide-only line opened a turn")
	}
	b, _ := json.Marshal(map[string]any{
		"type": "user", "uuid": "h1", "sessionId": "s1", "cwd": "/p", "promptId": "p1",
		"timestamp": tsAt(0), "message": map[string]any{"role": "user", "content": []any{
			map[string]any{"type": "text", "text": "<ide_opened_file>The user opened a.py</ide_opened_file>"},
			map[string]any{"type": "text", "text": "why does this fail?"}}}})
	write(t, filepath.Join(root, "-p", "s1.jsonl"), string(b)+"\n",
		aLine("a1", 30, "msg_1", 1, aOpt{blocks: []string{"text"}, stop: "end_turn"}))
	mustSweep(t, in, root)
	var head string
	if err := s.DB.QueryRow(`SELECT prompt_head FROM turns WHERE turn_id='p1'`).Scan(&head); err != nil {
		t.Fatal(err)
	}
	if head != "why does this fail?" {
		t.Fatalf("prompt_head %q", head)
	}
	nearSecs(t, "busy p1", busy(t, s, ` WHERE prompt_id=?`, "p1"), 30)
}

func TestARescanRefreshesTheTurnAndItsBilledRows(t *testing.T) {
	s, in, root := setup(t)
	write(t, filepath.Join(root, "-p", "s1.jsonl"),
		pLine("h1", 0, "<ide_selection>x</ide_selection>\nrename it", "s1", "p1"),
		aLine("a1", 30, "msg_1", 1, aOpt{blocks: []string{"text"}, stop: "end_turn"}))
	mustSweep(t, in, root)
	// State left by an older ingest: the tag in the head, the row unstamped.
	if _, err := s.DB.Exec(`UPDATE turns SET prompt_head='<ide_selection>x' WHERE turn_id='p1';
		UPDATE responses SET prompt_id=NULL; DELETE FROM files`); err != nil {
		t.Fatal(err)
	}
	mustSweep(t, in, root)
	var head string
	if err := s.DB.QueryRow(`SELECT prompt_head FROM turns WHERE turn_id='p1'`).Scan(&head); err != nil {
		t.Fatal(err)
	}
	if head != "rename it" {
		t.Fatalf("prompt_head %q", head)
	}
	for _, r := range billedRows(t, s) {
		if r[1] != "p1" {
			t.Fatalf("row %s stamped %q", r[0], r[1])
		}
	}
}

func TestAPromptSentBeforeTheReplyJoinsTheOpenTurn(t *testing.T) {
	s, in, root := setup(t)
	write(t, filepath.Join(root, "-p", "s1.jsonl"),
		pLine("h1", 0, "convert the tools to English", "s1", "p1"),
		pLine("h2", 14, "and keep the old names as aliases", "s1", "p2"),
		aLine("a1", 20, "msg_1", 1, aOpt{}),
		rLine("r1", 30, "s1", "p2", ""),
		aLine("a2", 40, "msg_2", 1, aOpt{blocks: []string{"text"}, stop: "end_turn"}),
		// After the reply, a new prompt is a new turn.
		pLine("h3", 100, "now commit", "s1", "p3"),
		aLine("a3", 110, "msg_3", 1, aOpt{blocks: []string{"text"}, stop: "end_turn"}))
	mustSweep(t, in, root)
	heads := turnHeads(t, s)
	want := map[string]string{
		"p1": "convert the tools to English\n\nand keep the old names as aliases",
		"p3": "now commit"}
	if !reflect.DeepEqual(heads, want) {
		t.Fatalf("turns %v", heads)
	}
	for uuid, pid := range map[string]string{"a1": "p1", "a2": "p1", "a3": "p3"} {
		var got string
		if err := s.DB.QueryRow(`SELECT COALESCE(prompt_id,'') FROM responses WHERE uuid=?`, uuid).
			Scan(&got); err != nil {
			t.Fatal(err)
		}
		if got != pid {
			t.Fatalf("%s stamped %q", uuid, got)
		}
	}
	nearSecs(t, "busy p1", busy(t, s, ` WHERE prompt_id=?`, "p1"), 40)
	// A full re-read gives the same turn, not a doubled head.
	if _, err := s.DB.Exec(`DELETE FROM files`); err != nil {
		t.Fatal(err)
	}
	mustSweep(t, in, root)
	if got := turnHeads(t, s); !reflect.DeepEqual(got, heads) {
		t.Fatalf("after rescan %v", got)
	}
}

func turnHeads(t *testing.T, s *ledger.Store) map[string]string {
	t.Helper()
	rows, err := s.DB.Query(`SELECT turn_id, prompt_head FROM turns`)
	if err != nil {
		t.Fatal(err)
	}
	defer rows.Close()
	out := map[string]string{}
	for rows.Next() {
		var id string
		var head *string
		if err := rows.Scan(&id, &head); err != nil {
			t.Fatal(err)
		}
		if head != nil {
			out[id] = *head
		}
	}
	if err := rows.Err(); err != nil {
		t.Fatal(err)
	}
	return out
}

func TestAReplayedStretchKeepsItsTurnAndAddsNoTime(t *testing.T) {
	s, in, root := setup(t)
	// The CLI re-appended turn p1's lines (same uuids, old timestamps) inside p2.
	first := []string{pLine("h1", 0, "first goal", "s1", "p1"),
		aLine("a1", 60, "msg_1", 1, aOpt{blocks: []string{"text"}, stop: "end_turn"})}
	write(t, filepath.Join(root, "-p", "s1.jsonl"), append(append(append([]string{}, first...),
		pLine("h2", 3600, "second goal", "s1", "p2")),
		append(first,
			aLine("a2", 3660, "msg_2", 1, aOpt{blocks: []string{"text"}, stop: "end_turn"}))...)...)
	mustSweep(t, in, root)
	for uuid, pid := range map[string]string{"a1": "p1", "a2": "p2"} {
		var got string
		if err := s.DB.QueryRow(`SELECT COALESCE(prompt_id,'') FROM responses WHERE uuid=?`, uuid).
			Scan(&got); err != nil {
			t.Fatal(err)
		}
		if got != pid {
			t.Fatalf("%s stamped %q", uuid, got)
		}
	}
	nearSecs(t, "busy p1", busy(t, s, ` WHERE prompt_id=?`, "p1"), 60)
	nearSecs(t, "busy p2", busy(t, s, ` WHERE prompt_id=?`, "p2"), 60)
}

func TestBusyTimeRunsFromPromptToEndTurn(t *testing.T) {
	s, in, root := setup(t)
	write(t, filepath.Join(root, "-p", "s1.jsonl"),
		pLine("h1", 0, "fix the bug", "s1", "p1"),
		aLine("a1", 60, "msg_1", 1, aOpt{}),
		rLine("r1", 120, "s1", "p1", ""),
		aLine("a2", 180, "msg_2", 1, aOpt{blocks: []string{"text"}, stop: "end_turn"}),
		// Idle after end_turn is not work, however long.
		pLine("h2", 7200, "again", "s1", "p2"),
		aLine("a3", 7230, "msg_3", 1, aOpt{blocks: []string{"text"}, stop: "end_turn"}))
	mustSweep(t, in, root)
	nearSecs(t, "busy p1", busy(t, s, ` WHERE prompt_id=?`, "p1"), 180)
	nearSecs(t, "busy p2", busy(t, s, ` WHERE prompt_id=?`, "p2"), 30)
}

func TestALongGapInsideATurnIsCapped(t *testing.T) {
	t.Setenv("TOKEN_AUDIT_AGENT_IDLE_CAP_MIN", "15")
	s, in, root := setup(t)
	write(t, filepath.Join(root, "-p", "s1.jsonl"),
		pLine("h1", 0, "fix the bug", "s1", "p1"),
		aLine("a1", 60, "msg_1", 1, aOpt{}),
		rLine("r1", 60+3600, "s1", "p1", ""), // an hour-long tool call, or someone away
		aLine("a2", 3700, "msg_2", 1, aOpt{blocks: []string{"text"}, stop: "end_turn"}))
	mustSweep(t, in, root)
	nearSecs(t, "busy", busy(t, s, ""), 60+900+40)
}

func TestWaitingForAnAnswerIsNotBusyTime(t *testing.T) {
	s, in, root := setup(t)
	write(t, filepath.Join(root, "-p", "s1.jsonl"),
		pLine("h1", 0, "fix the bug", "s1", "p1"),
		aLine("a1", 30, "msg_1", 1, aOpt{tool: "AskUserQuestion"}),
		rLine("r1", 330, "s1", "p1", ""), // five minutes to answer
		aLine("a2", 360, "msg_2", 1, aOpt{blocks: []string{"text"}, stop: "end_turn"}))
	mustSweep(t, in, root)
	nearSecs(t, "busy", busy(t, s, ""), 30+30)
}

func TestSubagentsAddTheirOwnTimeToTheParentTurn(t *testing.T) {
	s, in, root := setup(t)
	write(t, filepath.Join(root, "-p", "s1.jsonl"),
		pLine("h1", 0, "fix the bug", "s1", "p1"),
		aLine("a1", 10, "msg_1", 1, aOpt{tool: "Agent"}),
		rLine("r1", 610, "s1", "p1", ""),
		aLine("a2", 620, "msg_2", 1, aOpt{blocks: []string{"text"}, stop: "end_turn"}))
	for n, sub := range []string{"sub1", "sub2"} {
		dispatch, _ := json.Marshal(map[string]any{
			"type": "user", "uuid": fmt.Sprintf("d%d", n), "sessionId": "s1",
			"timestamp": tsAt(12), "promptId": "p1", "agentId": sub, "isSidechain": true,
			"message": map[string]any{"role": "user", "content": "dispatch"}})
		write(t, filepath.Join(root, "-p", "s1", "subagents", "agent-"+sub+".jsonl"),
			string(dispatch)+"\n",
			aLine(fmt.Sprintf("x%d", n), 300, fmt.Sprintf("msg_s%d", n), 1,
				aOpt{agent: sub, prompt: "p1"}),
			aLine(fmt.Sprintf("y%d", n), 600, fmt.Sprintf("msg_t%d", n), 1,
				aOpt{agent: sub, prompt: "p1", blocks: []string{"text"}, stop: "end_turn"}))
	}
	mustSweep(t, in, root)
	nearSecs(t, "main busy", busy(t, s, ` WHERE agent_id=?`, ""), 620) // the main agent waits, and counts
	nearSecs(t, "sub1 busy", busy(t, s, ` WHERE agent_id=?`, "sub1"), 588)
	nearSecs(t, "sub2 busy", busy(t, s, ` WHERE agent_id=?`, "sub2"), 588)
	nearSecs(t, "busy p1", busy(t, s, ` WHERE prompt_id=?`, "p1"), 620+2*588)
	rows, err := s.DB.Query(`SELECT prompt_id, agent_id FROM responses WHERE agent_id IS NOT NULL`)
	if err != nil {
		t.Fatal(err)
	}
	defer rows.Close()
	got := map[string]bool{}
	for rows.Next() {
		var p, a string
		if err := rows.Scan(&p, &a); err != nil {
			t.Fatal(err)
		}
		got[p+"/"+a] = true
	}
	if !got["p1/sub1"] || !got["p1/sub2"] || len(got) != 2 {
		t.Fatalf("subagent stamps %v", got)
	}
}

func TestBusyTimeIsSplitAtUTCMidnight(t *testing.T) {
	s, in, root := setup(t)
	late := `{"type":"user","uuid":"h1","sessionId":"s1","promptId":"p1",` +
		`"turnOrigin":"human","timestamp":"2026-09-10T23:59:00.000Z",` +
		`"message":{"content":"late work"}}` + "\n"
	end := aLine("a1", 0, "msg_1", 1, aOpt{blocks: []string{"text"}, stop: "end_turn"})
	var obj map[string]any
	if err := json.Unmarshal([]byte(end), &obj); err != nil {
		t.Fatal(err)
	}
	obj["timestamp"] = "2026-09-11T00:01:00.000Z"
	b, _ := json.Marshal(obj)
	write(t, filepath.Join(root, "-p", "s1.jsonl"), late, string(b)+"\n")
	mustSweep(t, in, root)
	rows, err := s.DB.Query(`SELECT day, SUM(busy_seconds) FROM agent_activity GROUP BY day`)
	if err != nil {
		t.Fatal(err)
	}
	defer rows.Close()
	byDay := map[string]float64{}
	for rows.Next() {
		var d string
		var sec float64
		if err := rows.Scan(&d, &sec); err != nil {
			t.Fatal(err)
		}
		byDay[d] = sec
	}
	nearSecs(t, "2026-09-10", byDay["2026-09-10"], 60)
	nearSecs(t, "2026-09-11", byDay["2026-09-11"], 60)
	if len(byDay) != 2 {
		t.Fatalf("days %v", byDay)
	}
}

// Two ingest passes split inside an open turn: the tracker state persisted on
// the files row lets the second pass continue the turn.
func TestAnIncrementalReadContinuesTheOpenTurn(t *testing.T) {
	s, in, root := setup(t)
	f := filepath.Join(root, "-p", "s1.jsonl")
	write(t, f, pLine("h1", 0, "fix the bug", "s1", "p1"), aLine("a1", 60, "msg_1", 1, aOpt{}))
	mustSweep(t, in, root)
	write(t, f, rLine("r1", 120, "s1", "p1", ""),
		aLine("a2", 180, "msg_2", 1, aOpt{blocks: []string{"text"}, stop: "end_turn"}))
	mustSweep(t, in, root)
	nearSecs(t, "busy", busy(t, s, ""), 180)
}

func TestAFullRescanDoesNotDoubleTheBusyTime(t *testing.T) {
	s, in, root := setup(t)
	write(t, filepath.Join(root, "-p", "s1.jsonl"),
		pLine("h1", 0, "fix the bug", "s1", "p1"),
		aLine("a1", 90, "msg_1", 1, aOpt{blocks: []string{"text"}, stop: "end_turn"}))
	mustSweep(t, in, root)
	if _, err := s.DB.Exec(`DELETE FROM files`); err != nil {
		t.Fatal(err)
	}
	mustSweep(t, in, root)
	nearSecs(t, "busy", busy(t, s, ""), 90)
}

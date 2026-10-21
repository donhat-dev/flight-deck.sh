package subagents

import (
	"encoding/json"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"flightdeck/core/pricing"
)

func write(t *testing.T, path string, lines ...string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	body := ""
	if len(lines) > 0 {
		body = strings.Join(lines, "\n") + "\n"
	}
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
}

func userLine(uuid, ts, text string) string {
	return fmt.Sprintf(`{"type":"user","uuid":%q,"timestamp":%q,`+
		`"message":{"role":"user","content":%q}}`, uuid, ts, text)
}

// assistantLine writes one content block of an API response: msg is
// message.id, req the requestId — the dedupe key is the pair.
func assistantLine(uuid, msg, req, model, ts, usage string) string {
	return fmt.Sprintf(`{"type":"assistant","uuid":%q,"timestamp":%q,"requestId":%q,`+
		`"message":{"id":%q,"model":%q,"content":[{"type":"text","text":"x"}],`+
		`"usage":%s}}`, uuid, ts, req, msg, model, usage)
}

const simpleUsage = `{"input_tokens":100,"cache_read_input_tokens":50,` +
	`"cache_creation":{"ephemeral_5m_input_tokens":20,"ephemeral_1h_input_tokens":10},` +
	`"output_tokens":30}`

func TestFilesBySessionGroupsFlatAndWorkflowFiles(t *testing.T) {
	root := t.TempDir()
	line := userLine("u", "2026-10-20T10:00:00.000Z", "go")
	b := filepath.Join(root, "-p", "s1", "subagents", "agent-b.jsonl")
	a := filepath.Join(root, "-p", "s1", "subagents", "agent-a.jsonl")
	c := filepath.Join(root, "-p", "s1", "subagents", "workflows", "wf1", "agent-c.jsonl")
	z := filepath.Join(root, "-p", "s2", "subagents", "agent-z.jsonl")
	for _, p := range []string{b, a, c, z} {
		write(t, p, line)
	}
	write(t, filepath.Join(root, "-p", "s1.jsonl"), line) // the session itself
	write(t, filepath.Join(root, "-p", "s1", "agent-loose.jsonl"), line)

	got := FilesBySession(root)
	if want := []string{a, b, c}; strings.Join(got["s1"], "|") != strings.Join(want, "|") {
		t.Errorf("s1 files %v, want %v", got["s1"], want)
	}
	if len(got["s2"]) != 1 || got["s2"][0] != z {
		t.Errorf("s2 files %v", got["s2"])
	}
	if _, ok := got["-p"]; ok {
		t.Errorf("a loose agent-*.jsonl outside subagents/ matched")
	}
	if len(FilesBySession(filepath.Join(root, "missing"))) != 0 {
		t.Errorf("a missing root did not scan empty")
	}
}

func TestParseReadsTypeDispatchTokensAndSpan(t *testing.T) {
	root := t.TempDir()
	p := filepath.Join(root, "s1", "subagents", "agent-exp.jsonl")
	write(t, p,
		`{"type":"summary","summary":"explore","attributionAgent":"Explore",`+
			`"timestamp":"2026-10-20T10:00:00.000Z"}`,
		userLine("u0", "2026-10-20T10:00:01.000Z", "survey the repo"),
		userLine("u1", "2026-10-20T10:00:02.000Z", "ignored: dispatch is the first user text"),
		assistantLine("a1", "m1", "r1", "claude-haiku-4-5", "2026-10-20T10:00:03.000Z", simpleUsage),
		assistantLine("a2", "m2", "r2", "claude-opus-5-5", "2026-10-20T10:00:04.000Z", simpleUsage),
	)
	f := Usage([]string{p})[0]
	var agentType string
	json.Unmarshal(f.AgentType, &agentType)
	if f.AgentID != "exp" || agentType != "Explore" {
		t.Errorf("agent %q type %s", f.AgentID, f.AgentType)
	}
	if *f.Model != "claude-opus-5-5" {
		t.Errorf("model %v — tokens aggregate under the LAST model", f.Model)
	}
	if f.Turns != 2 || f.Input != 200 || f.CacheRead != 100 ||
		f.Create5m != 40 || f.Create1h != 20 || f.Output != 60 {
		t.Errorf("usage %+v", f)
	}
	if *f.FirstTS != "2026-10-20T10:00:00.000Z" || *f.LastTS != "2026-10-20T10:00:04.000Z" {
		t.Errorf("span %v..%v", f.FirstTS, f.LastTS)
	}
	if f.Dispatch != "survey the repo" {
		t.Errorf("dispatch %q", f.Dispatch)
	}
}

// Duplicate lines of one response repeat its usage; the entry with the
// largest output wins, and a different requestId is a different response.
func TestParseDedupesResponses(t *testing.T) {
	root := t.TempDir()
	p := filepath.Join(root, "s1", "subagents", "agent-a.jsonl")
	write(t, p,
		userLine("u0", "2026-10-20T10:00:00.000Z", "work"),
		assistantLine("a1", "m1", "r1", "claude-opus-5-5", "2026-10-20T10:00:01.000Z",
			`{"input_tokens":10,"output_tokens":30}`),
		assistantLine("a2", "m1", "r1", "claude-opus-5-5", "2026-10-20T10:00:02.000Z",
			`{"input_tokens":10,"output_tokens":45}`),
		assistantLine("a3", "m1", "r2", "claude-opus-5-5", "2026-10-20T10:00:03.000Z",
			`{"input_tokens":5,"output_tokens":10}`),
		assistantLine("a4", "m1", "r2", "claude-opus-5-5", "2026-10-20T10:00:04.000Z",
			`{"input_tokens":5,"output_tokens":8}`), // smaller: does not replace
		// no message.id: falls back to the line uuid
		`{"type":"assistant","uuid":"u9","timestamp":"2026-10-20T10:00:05.000Z",`+
			`"requestId":"r3","message":{"usage":{"input_tokens":1,"output_tokens":2}}}`,
	)
	f := Usage([]string{p})[0]
	if f.Turns != 3 || f.Output != 45+10+2 || f.Input != 10+5+1 {
		t.Errorf("turns %d output %d input %d", f.Turns, f.Output, f.Input)
	}
}

func TestDispatchStaysEmptyWithoutAUserText(t *testing.T) {
	root := t.TempDir()
	p := filepath.Join(root, "s1", "subagents", "agent-a.jsonl")
	write(t, p,
		userLine("u0", "2026-10-20T10:00:00.000Z", "<system-reminder>be brief</system-reminder>"),
		`{"type":"user","uuid":"u1","timestamp":"2026-10-20T10:00:01.000Z",`+
			`"message":{"role":"user","content":[{"type":"tool_result","content":"out"}]}}`,
		assistantLine("a1", "m1", "r1", "claude-opus-5-5", "2026-10-20T10:00:02.000Z",
			`{"input_tokens":1,"output_tokens":2}`),
	)
	f := Usage([]string{p})[0]
	if f.Dispatch != "" {
		t.Errorf("dispatch %q, want empty", f.Dispatch)
	}
}

// Dispatch joins a user content list's text blocks and caps at 300 runes.
func TestDispatchFromBlockListIsCapped(t *testing.T) {
	root := t.TempDir()
	p := filepath.Join(root, "s1", "subagents", "agent-a.jsonl")
	long := strings.Repeat("x", 400)
	write(t, p,
		`{"type":"user","uuid":"u0","timestamp":"2026-10-20T10:00:00.000Z",`+
			`"message":{"role":"user","content":[{"type":"text","text":"first"},`+
			`{"type":"tool_result","content":"skip"},{"type":"text","text":"second"}]}}`,
	)
	f := Usage([]string{p})[0]
	if f.Dispatch != "first second" {
		t.Fatalf("dispatch %q", f.Dispatch)
	}
	write(t, p, `{"type":"user","uuid":"u0","timestamp":"2026-10-20T10:00:00.000Z",`+
		fmt.Sprintf(`"message":{"role":"user","content":%q}}`, long))
	f = Usage([]string{p})[0]
	if len(f.Dispatch) != 300 {
		t.Errorf("dispatch len %d, want 300", len(f.Dispatch))
	}
}

// The cache key is (mtime, size): appending a line re-parses the file.
func TestCacheInvalidatesOnChange(t *testing.T) {
	root := t.TempDir()
	p := filepath.Join(root, "s1", "subagents", "agent-a.jsonl")
	line := assistantLine("a1", "m1", "r1", "m", "2026-10-20T10:00:00.000Z",
		`{"input_tokens":1,"output_tokens":2}`)
	write(t, p, line)
	if Usage([]string{p})[0].Turns != 1 {
		t.Fatal("first parse")
	}
	f, _ := os.OpenFile(p, os.O_APPEND|os.O_WRONLY, 0)
	f.WriteString(assistantLine("a2", "m2", "r2", "m", "2026-10-20T10:00:01.000Z",
		`{"input_tokens":1,"output_tokens":2}`) + "\n")
	f.Close()
	if Usage([]string{p})[0].Turns != 2 {
		t.Fatal("the grown file kept the cached parse")
	}
}

// metrics.py prices the summed usage under the file's last model at first_ts,
// no speed/geo modifiers; unpriced is 0.
func TestRowPricesLikeMetricsPy(t *testing.T) {
	prices, err := pricing.New(pricing.Defaults())
	if err != nil {
		t.Fatal(err)
	}
	first, last := "2026-10-20T10:00:00.000Z", "2026-10-20T10:05:00.000Z"
	model := "claude-opus-5-5"
	f := &File{AgentID: "a", AgentType: json.RawMessage(`"Explore"`), Model: &model,
		Turns: 1, Input: 100, CacheRead: 50, Create5m: 20, Create1h: 10, Output: 30,
		FirstTS: &first, LastTS: &last, Dispatch: "go"}
	row := f.Row(prices)
	// $4/$20 in/out, cache read 0.05x, writes 1.25x/2x of input.
	want := float64(100*4+30*20+50*0.05*4+20*1.25*4+10*2*4) / 1e6
	if math.Abs(row.Cost-want) > 1e-12 {
		t.Errorf("cost %v, want %v", row.Cost, want)
	}
	if row.InputRaw != 180 {
		t.Errorf("input_raw %d", row.InputRaw)
	}
	unpriced := "no-such-model"
	f.Model = &unpriced
	if got := f.Row(prices).Cost; got != 0 {
		t.Errorf("unpriced cost %v", got)
	}
}

// The wire shape: FastAPI's keys, in FastAPI's order, nulls where Python has None.
func TestRowJSONShape(t *testing.T) {
	row := Row{AgentID: "a", Turns: 2, InputRaw: 3, Output: 4, CacheRead: 1}
	out, _ := json.Marshal(row)
	want := `{"agent_id":"a","agent_type":null,"model":null,"turns":2,` +
		`"input_raw":3,"output":4,"cache_read":1,"cost":0,"first_ts":null,` +
		`"last_ts":null,"dispatch":""}`
	if string(out) != want {
		t.Errorf("%s\nwant %s", out, want)
	}
}

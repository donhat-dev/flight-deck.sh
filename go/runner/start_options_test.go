package runner

import (
	"encoding/json"
	"reflect"
	"testing"
)

func TestOptionArgs(t *testing.T) {
	none := []string{}
	cases := []struct {
		name string
		args CommandArgs
		want []string
	}{
		{"nothing set", CommandArgs{}, nil},
		{"all set", CommandArgs{Name: "Zalo chat", JSONSchema: `{"type":"object"}`, MaxBudgetUSD: 0.5,
			MaxTurns: 8, Tools: &[]string{"Read", "Grep"}},
			[]string{"--name", "Zalo chat", "--json-schema", `{"type":"object"}`, "--max-budget-usd", "0.5",
				"--max-turns", "8", "--tools", "Read,Grep"}},
		{"empty tool list allows none", CommandArgs{Tools: &none}, []string{"--tools", ""}},
	}
	for _, c := range cases {
		if got := optionArgs(c.args); !reflect.DeepEqual(got, c.want) {
			t.Errorf("%s: got %q, want %q", c.name, got, c.want)
		}
	}
}

func TestToolsKeepAbsentApartFromEmpty(t *testing.T) {
	var absent, empty CommandArgs
	json.Unmarshal([]byte(`{}`), &absent)
	json.Unmarshal([]byte(`{"tools": []}`), &empty)
	if absent.Tools != nil {
		t.Errorf("absent tools should stay nil")
	}
	if empty.Tools == nil || len(*empty.Tools) != 0 {
		t.Errorf("empty tools should be an empty list, got %v", empty.Tools)
	}
}

// The line is a result frame as the CLI wrote it on 2026-10-13 with --json-schema.
func TestAnswerPayloadFromAResultFrame(t *testing.T) {
	line := `{"type":"result","subtype":"success","is_error":false,"num_turns":2,"result":"{\"sum\":4}",` +
		`"structured_output":{"sum":4},"total_cost_usd":0.0017,` +
		`"usage":{"input_tokens":2,"cache_creation_input_tokens":7818,"cache_read_input_tokens":956,"output_tokens":251,` +
		`"output_tokens_details":{"thinking_tokens":0}}}`
	var f frame
	if err := json.Unmarshal([]byte(line), &f); err != nil {
		t.Fatal(err)
	}
	raw, _ := json.Marshal(answerPayload(f))
	var got map[string]any
	json.Unmarshal(raw, &got)
	want := map[string]any{
		"result":            `{"sum":4}`,
		"structured_output": map[string]any{"sum": float64(4)},
		"usage": map[string]any{"input_tokens": float64(2), "output_tokens": float64(251),
			"cache_creation_input_tokens": float64(7818), "cache_read_input_tokens": float64(956)},
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("got %v\nwant %v", got, want)
	}
}

// /effort is sent as a turn and ends with its own result frame (probe
// 2026-10-13); that frame is marked, the prompt's next one is not.
func TestCommandTurnsMarkTheirOwnResults(t *testing.T) {
	var q turnQueue
	q.push(true)
	q.push(false)
	if !q.pop() {
		t.Errorf("first result after /effort is the command's")
	}
	if q.pop() {
		t.Errorf("the next result is the prompt's")
	}
	if q.pop() {
		t.Errorf("an empty queue counts as a prompt's")
	}
}

// /effort sent while a prompt is still running: the running prompt's result
// comes first and is still its answer.
func TestEffortDuringABusyTurn(t *testing.T) {
	var q turnQueue
	q.push(false) // prompt A, in flight
	q.push(true)  // /effort
	q.push(false) // prompt B
	got := []bool{q.pop(), q.pop(), q.pop()}
	if !reflect.DeepEqual(got, []bool{false, true, false}) {
		t.Errorf("got %v", got)
	}
}

func TestDropLastTakesBackAFailedWrite(t *testing.T) {
	var q turnQueue
	q.push(false)
	q.push(true)
	q.dropLast()
	if q.pop() || q.pop() {
		t.Errorf("only the prompt should remain, and then nothing")
	}
}

func TestAnswerPayloadWithoutSchema(t *testing.T) {
	var f frame
	json.Unmarshal([]byte(`{"type":"result","result":"4","structured_output":null}`), &f)
	got := answerPayload(f)
	if _, ok := got["structured_output"]; ok {
		t.Errorf("no schema: no structured_output, got %v", got)
	}
	if got["result"] != "4" {
		t.Errorf("result: got %v", got["result"])
	}
}

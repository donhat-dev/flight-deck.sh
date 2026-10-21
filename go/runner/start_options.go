package runner

import (
	"encoding/json"
	"strconv"
	"strings"
	"sync"
)

// optionArgs turns a start command's per-session options into CLI flags.
// They hold for the whole process: a different schema, tool set or budget
// needs another session, which is why Odoo keys sessions by them.
func optionArgs(a CommandArgs) []string {
	var args []string
	if a.Name != "" {
		args = append(args, "--name", a.Name)
	}
	if a.JSONSchema != "" {
		args = append(args, "--json-schema", a.JSONSchema)
	}
	if a.MaxBudgetUSD > 0 {
		args = append(args, "--max-budget-usd", strconv.FormatFloat(a.MaxBudgetUSD, 'f', -1, 64))
	}
	if a.MaxTurns > 0 {
		// Not in `claude --help`, but accepted in print mode (CLI reference).
		args = append(args, "--max-turns", strconv.Itoa(a.MaxTurns))
	}
	if a.Tools != nil {
		args = append(args, "--tools", strings.Join(*a.Tools, ","))
	}
	return args
}

// turnQueue holds, oldest first, whether each turn written to a process is
// the runner's own command. The CLI reads stdin in order and ends each line
// with one result frame, so the head of the queue owns the next result.
// The command loop pushes; the stdout reader pops.
type turnQueue struct {
	mu    sync.Mutex
	kinds []bool
}

func (q *turnQueue) push(command bool) {
	q.mu.Lock()
	q.kinds = append(q.kinds, command)
	q.mu.Unlock()
}

// dropLast takes back a push whose write failed. Only the command loop
// pushes, so the last entry is that push.
func (q *turnQueue) dropLast() {
	q.mu.Lock()
	if n := len(q.kinds); n > 0 {
		q.kinds = q.kinds[:n-1]
	}
	q.mu.Unlock()
}

// pop reports whether the result frame now read ends a command turn. An empty
// queue (a turn this runner did not write) counts as a prompt's.
func (q *turnQueue) pop() bool {
	q.mu.Lock()
	defer q.mu.Unlock()
	if len(q.kinds) == 0 {
		return false
	}
	command := q.kinds[0]
	q.kinds = q.kinds[1:]
	return command
}

// resultUsage is the token count of a turn, without the per-model detail.
type resultUsage struct {
	InputTokens              int `json:"input_tokens"`
	OutputTokens             int `json:"output_tokens"`
	CacheCreationInputTokens int `json:"cache_creation_input_tokens"`
	CacheReadInputTokens     int `json:"cache_read_input_tokens"`
}

// answerPayload is what a result frame says about the answer itself, added
// to the result event so Odoo can hand it on without reading the transcript.
func answerPayload(f frame) map[string]any {
	out := map[string]any{"result": f.Result}
	if len(f.StructuredOutput) > 0 && string(f.StructuredOutput) != "null" {
		out["structured_output"] = json.RawMessage(f.StructuredOutput)
	}
	if f.Usage != nil {
		out["usage"] = f.Usage
	}
	return out
}

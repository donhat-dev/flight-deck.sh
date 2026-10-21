package runner

import (
	"context"
	"log"
	"os"
	"os/exec"
	"sync"
	"time"
)

// The subscription quota is only reported by the CLI's own /usage command, so
// the runner, which owns every claude process, is the one that asks. It does
// not parse the report: the text goes to Odoo as it came, and the one parser
// lives next to the rest of the reading there.

const usageTimeout = 60 * time.Second

var usageMu sync.Mutex

// reportUsage runs `claude -p /usage` once and emits a `usage` event with the
// raw text. A poll already in flight makes this one a no-op: two reports a
// second apart say the same thing.
func (m *Manager) reportUsage(cmdID string) {
	if !usageMu.TryLock() {
		return
	}
	defer usageMu.Unlock()
	ctx, cancel := context.WithTimeout(context.Background(), usageTimeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, m.claude, "-p", "/usage")
	cmd.Dir, _ = os.UserHomeDir()
	cmd.Env = scrubEnv(os.Environ())
	started := time.Now().UTC()
	out, err := cmd.Output()
	payload := map[string]any{
		"text":        string(out),
		"captured_at": started.Format(time.RFC3339),
	}
	ev := Event{Kind: "usage", CmdID: cmdID, Payload: payload}
	if err != nil {
		ev.Error = err.Error()
		log.Printf("usage: %v", err)
	}
	m.sp.Emit(ev)
}

// UsageLoop reports the quota every `every`, the first time right away.
func (m *Manager) UsageLoop(every time.Duration) {
	m.reportUsage("")
	tick := time.NewTicker(every)
	defer tick.Stop()
	for range tick.C {
		m.reportUsage("")
	}
}

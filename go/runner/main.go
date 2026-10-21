// fd-runner keeps Claude Code processes alive for Odoo.
//
// Odoo drops one JSON file per command into <spool>/cmd; the runner starts,
// feeds, interrupts or stops a `claude -p` process for the named session and
// reports back through <spool>/events.jsonl and <spool>/state/<session>.json.
// The transcript never passes through here: claude writes its own JSONL and
// Odoo reads that file directly.
//
// One runner per spool directory. Two runners sharing a cmd/ directory would
// race to move the same file, so each zone gets its own tree.
package runner

import (
	"errors"
	"flag"
	"log"
	"os"
	"os/signal"
	"syscall"
	"time"
)

// Main is the runner's entry point, kept out of package main so tests can call
// it: it returns the process exit code instead of exiting.
func Main(args []string) int {
	fs := flag.NewFlagSet("fd-runner", flag.ContinueOnError)
	spool := fs.String("spool", "", "zone directory holding cmd/, done/, state/ and events.jsonl")
	claude := fs.String("claude", "claude", "path to the claude binary")
	idle := fs.Duration("idle", 15*time.Minute, "stop a session after this long without a turn")
	poll := fs.Duration("poll", 200*time.Millisecond, "how often cmd/ is scanned")
	permTimeout := fs.Duration("permission-timeout", 10*time.Minute, "deny a permission request nobody answered after this long")
	usageEvery := fs.Duration("usage-every", 0, "report the subscription quota (claude -p /usage) this often; 0 reports only when Odoo asks")
	if err := fs.Parse(args); err != nil {
		if errors.Is(err, flag.ErrHelp) {
			return 0
		}
		return 2
	}
	if *spool == "" {
		log.Print("-spool is required")
		return 2
	}
	log.SetFlags(log.Ldate | log.Ltime | log.Lmicroseconds)

	sp, err := OpenSpool(*spool)
	if err != nil {
		log.Printf("spool: %v", err)
		return 1
	}
	mgr := NewManager(*claude, sp, *idle, *permTimeout)
	go mgr.Reap()
	if *usageEvery > 0 {
		go mgr.UsageLoop(*usageEvery)
	}

	sigs := make(chan os.Signal, 1)
	signal.Notify(sigs, syscall.SIGINT, syscall.SIGTERM)
	defer signal.Stop(sigs)
	tick := time.NewTicker(*poll)
	defer tick.Stop()
	log.Printf("fd-runner watching %s", *spool)
	for {
		select {
		case <-tick.C:
			for _, c := range sp.Pending() {
				mgr.Handle(c)
			}
		case <-sigs:
			log.Print("stopping every session")
			mgr.StopAll()
			return 0
		}
	}
}

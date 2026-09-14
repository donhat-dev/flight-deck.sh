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
package main

import (
	"flag"
	"log"
	"os"
	"os/signal"
	"syscall"
	"time"
)

func main() {
	spool := flag.String("spool", "", "zone directory holding cmd/, done/, state/ and events.jsonl")
	claude := flag.String("claude", "claude", "path to the claude binary")
	idle := flag.Duration("idle", 15*time.Minute, "stop a session after this long without a turn")
	poll := flag.Duration("poll", 200*time.Millisecond, "how often cmd/ is scanned")
	mode := flag.Uint("transcript-mode", 0, "chmod each session's transcript to this octal mode after a turn, e.g. 0640; 0 leaves it")
	permTimeout := flag.Duration("permission-timeout", 10*time.Minute, "deny a permission request nobody answered after this long")
	flag.Parse()
	if *spool == "" {
		log.Fatal("-spool is required")
	}
	log.SetFlags(log.Ldate | log.Ltime | log.Lmicroseconds)

	sp, err := OpenSpool(*spool)
	if err != nil {
		log.Fatalf("spool: %v", err)
	}
	mgr := NewManager(*claude, sp, *idle, os.FileMode(*mode), *permTimeout)
	go mgr.Reap()

	sigs := make(chan os.Signal, 1)
	signal.Notify(sigs, syscall.SIGINT, syscall.SIGTERM)
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
			return
		}
	}
}

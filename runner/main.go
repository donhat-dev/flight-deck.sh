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
	permTimeout := flag.Duration("permission-timeout", 10*time.Minute, "deny a permission request nobody answered after this long")
	usageEvery := flag.Duration("usage-every", 0, "report the subscription quota (claude -p /usage) this often; 0 reports only when Odoo asks")
	resourceMCP := flag.Bool("resource-mcp", false, "run as the management agent's MCP helper instead of the runner")
	resourceSock := flag.String("resource-sock", "", "unix socket of the runner's resource gateway (helper mode)")
	resourceToken := flag.String("resource-token", "", "path of the gateway token file (helper mode)")
	flag.Parse()
	if *resourceMCP {
		if *resourceSock == "" || *resourceToken == "" {
			log.Fatal("-resource-mcp needs -resource-sock and -resource-token")
		}
		runResourceHelper(*resourceSock, *resourceToken)
		return
	}
	if *spool == "" {
		log.Fatal("-spool is required")
	}
	log.SetFlags(log.Ldate | log.Ltime | log.Lmicroseconds)

	sp, err := OpenSpool(*spool)
	if err != nil {
		log.Fatalf("spool: %v", err)
	}
	mgr := NewManager(*claude, sp, *idle, *permTimeout)
	mgr.res.Run()
	go mgr.res.RunGateway()
	go mgr.Reap()
	if *usageEvery > 0 {
		go mgr.UsageLoop(*usageEvery)
	}

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

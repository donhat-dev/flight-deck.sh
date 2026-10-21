package main

import (
	"flag"
	"os"
	"path/filepath"
	"strconv"

	"flightdeck/core/ingest"
	"flightdeck/core/ledger"
)

type paths struct{ db, projects string }

func addPaths(fs *flag.FlagSet) *paths {
	p := &paths{}
	fs.StringVar(&p.db, "db", envOr("FD_DB", filepath.Join(homeDir(), ".flightdeck", "fd", "ledger.db")),
		"ledger database file")
	fs.StringVar(&p.projects, "projects", envOr("FD_PROJECTS", defaultProjects()),
		"Claude Code projects directory")
	return p
}

func defaultProjects() string {
	if d := os.Getenv("CLAUDE_CONFIG_DIR"); d != "" {
		return filepath.Join(d, "projects")
	}
	return filepath.Join(homeDir(), ".claude", "projects")
}

func homeDir() string {
	h, _ := os.UserHomeDir()
	return h
}

func envOr(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}

func envFloat(k string, def float64) float64 {
	if v, err := strconv.ParseFloat(os.Getenv(k), 64); err == nil {
		return v
	}
	return def
}

// ingestAll brings the ledger up to date before an answer.
func ingestAll(s *ledger.Store, projects string) (ingest.Stats, error) {
	if st, err := os.Stat(projects); err != nil || !st.IsDir() {
		return ingest.Stats{}, refuse("projects directory %s not found", projects)
	}
	return (&ingest.Ingester{Store: s}).Sweep(projects)
}

func validRange(r string) error {
	switch r {
	case "today", "7d", "30d", "all":
		return nil
	}
	return refuse("--range must be today, 7d, 30d or all, not %q", r)
}

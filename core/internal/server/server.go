// Package server is `fd serve`: the spend API the React UI reads, over the same
// ledger the CLI uses, kept current by a file watcher and a periodic sweep.
package server

import (
	"encoding/json"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"sync"
	"time"

	"fdcore/internal/ingest"
	"fdcore/internal/ledger"
	"fdcore/internal/metrics"
	"fdcore/internal/pricing"
)

type Server struct {
	Store        *ledger.Store
	Projects     string
	Subscription float64
	UIDir        string
	Version      string
	Now          func() time.Time
	Ready        chan struct{} // closed once the watches are in place; tests wait on it
	mu           sync.Mutex    // one ingest pass at a time, from the watcher or the sweep
}

func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /api/summary", s.cells(func(w http.ResponseWriter, _ *http.Request, p *pricing.Table, c []metrics.Cell) {
		writeJSON(w, 200, metrics.Summarize(p, c, s.Subscription))
	}))
	mux.HandleFunc("GET /api/daily", s.cells(func(w http.ResponseWriter, _ *http.Request, p *pricing.Table, c []metrics.Cell) {
		writeJSON(w, 200, metrics.Daily(p, c))
	}))
	mux.HandleFunc("GET /api/by-model", s.cells(func(w http.ResponseWriter, _ *http.Request, p *pricing.Table, c []metrics.Cell) {
		writeJSON(w, 200, metrics.ByModel(p, c))
	}))
	mux.HandleFunc("GET /api/sessions", s.cells(func(w http.ResponseWriter, r *http.Request, p *pricing.Table, c []metrics.Cell) {
		rows, err := metrics.Sessions(s.Store.DB, p, c, intParam(r, "limit", 100), intParam(r, "offset", 0))
		if err != nil {
			writeJSON(w, 500, map[string]string{"error": err.Error()})
			return
		}
		writeJSON(w, 200, rows)
	}))
	mux.HandleFunc("GET /api/health", func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, 200, map[string]any{"ok": true, "version": s.Version, "projects": s.Projects})
	})
	// The UI calls endpoints fd does not have yet; a JSON 404 fails them cleanly
	// instead of handing back index.html.
	mux.HandleFunc("/api/", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, 404, map[string]string{"error": "not served by fd yet: " + r.URL.Path})
	})
	if s.UIDir != "" {
		mux.Handle("/", spa(s.UIDir))
	}
	return mux
}

// cells reads the price table and the grain for the request's ?range= (missing
// or unknown = all, as FastAPI does). The table is read per request, so an
// `fd pricing` edit shows without a restart.
func (s *Server) cells(h func(http.ResponseWriter, *http.Request, *pricing.Table, []metrics.Cell)) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		p, err := ledger.PriceTable(s.Store.DB)
		if err != nil {
			writeJSON(w, 500, map[string]string{"error": err.Error()})
			return
		}
		c, err := metrics.Grain(s.Store.DB, metrics.Since(r.URL.Query().Get("range"), s.Now()))
		if err != nil {
			writeJSON(w, 500, map[string]string{"error": err.Error()})
			return
		}
		h(w, r, p, c)
	}
}

func intParam(r *http.Request, k string, def int) int {
	if v, err := strconv.Atoi(r.URL.Query().Get(k)); err == nil {
		return v
	}
	return def
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(v)
}

// spa serves the built UI; unknown paths get index.html so client routes load.
func spa(dir string) http.Handler {
	files := http.FileServer(http.Dir(dir))
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		p := filepath.Join(dir, filepath.Clean("/"+r.URL.Path))
		if st, err := os.Stat(p); err != nil || (st.IsDir() && r.URL.Path != "/") {
			http.ServeFile(w, r, filepath.Join(dir, "index.html"))
			return
		}
		files.ServeHTTP(w, r)
	})
}

func (s *Server) IngestPaths(paths []string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	in := ingest.Ingester{Store: s.Store}
	for _, p := range ingest.Order(paths) {
		if _, err := in.File(p); err != nil {
			log.Printf("ingest %s: %v", p, err)
		}
	}
}

func (s *Server) SweepNow() {
	s.mu.Lock()
	defer s.mu.Unlock()
	st, err := (&ingest.Ingester{Store: s.Store}).Sweep(s.Projects)
	if err != nil {
		log.Printf("sweep: %v", err)
		return
	}
	if st.NewRows > 0 {
		log.Printf("sweep: %d new responses from %d files", st.NewRows, st.Files)
	}
}

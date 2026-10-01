package main

import (
	"context"
	"errors"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"fdcore/internal/ledger"
	"fdcore/internal/server"
)

func init() {
	commands["serve"] = command{"keep the ledger current and serve the spend API (and the UI)", cmdServe}
}

func cmdServe(args []string) (output, error) {
	fs := newFlags("serve")
	p := addPaths(fs)
	addr := fs.String("addr", envOr("FD_ADDR", "127.0.0.1:8011"), "listen address; the Python backend holds :8010")
	ui := fs.String("ui", "", "directory of the built React UI (frontend/dist); empty serves the API only")
	sub := fs.Float64("subscription-usd", envFloat("FD_SUBSCRIPTION_USD", 0),
		"monthly subscription price, for saved_vs_subscription")
	debounce := fs.Duration("debounce", 2*time.Second, "wait after a transcript write before reading it")
	sweepEvery := fs.Duration("sweep", 5*time.Minute, "re-check every transcript this often, in case an event was missed")
	if err := parse(fs, args); err != nil {
		return output{}, err
	}
	if st, err := os.Stat(p.projects); err != nil || !st.IsDir() {
		return output{}, refuse("projects directory %s not found", p.projects)
	}
	s, err := ledger.Open(p.db)
	if err != nil {
		return output{}, err
	}
	defer s.Close()
	srv := &server.Server{Store: s, Projects: p.projects, Subscription: *sub, UIDir: *ui,
		Version: version, Now: time.Now}
	log.SetOutput(os.Stderr)
	srv.SweepNow()
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	go func() {
		err := srv.Watch(ctx, *debounce, *sweepEvery)
		if err == nil {
			return
		}
		log.Printf("watch unavailable (%v); sweeping every %s instead", err, *sweepEvery)
		t := time.NewTicker(*sweepEvery)
		defer t.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-t.C:
				srv.SweepNow()
			}
		}
	}()
	hs := &http.Server{Addr: *addr, Handler: srv.Handler(), ReadHeaderTimeout: 10 * time.Second}
	go func() {
		<-ctx.Done()
		hs.Shutdown(context.Background())
	}()
	log.Printf("fd %s serving %s from %s", version, *addr, p.db)
	if err := hs.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		return output{}, refuse("listen %s: %v", *addr, err)
	}
	return output{Data: map[string]string{"stopped": *addr}}, nil
}

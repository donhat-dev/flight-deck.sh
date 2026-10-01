package server

import (
	"context"
	"io/fs"
	"log"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/fsnotify/fsnotify"
)

// Watch keeps the ledger current until ctx ends. inotify is not recursive, so
// every directory is watched and new ones are added as they appear; a new
// directory also triggers a sweep, because files can land in it before its
// watch exists. Only Write and Create count: the Python watcher once reacted to
// its own reads and re-ingested in a loop.
func (s *Server) Watch(ctx context.Context, debounce, sweepEvery time.Duration) error {
	w, err := fsnotify.NewWatcher()
	if err != nil {
		return err
	}
	defer w.Close()
	if err := watchTree(w, s.Projects); err != nil {
		return err
	}
	if s.Ready != nil {
		close(s.Ready)
	}
	pending := map[string]bool{}
	flush := time.NewTimer(debounce)
	flush.Stop()
	sweep := time.NewTicker(sweepEvery)
	defer sweep.Stop()
	for {
		select {
		case <-ctx.Done():
			return nil
		case ev, ok := <-w.Events:
			if !ok {
				return nil
			}
			if !ev.Has(fsnotify.Write) && !ev.Has(fsnotify.Create) {
				continue
			}
			if st, err := os.Stat(ev.Name); err == nil && st.IsDir() {
				if err := watchTree(w, ev.Name); err != nil {
					log.Printf("watch %s: %v", ev.Name, err)
				}
				s.SweepNow()
				continue
			}
			if strings.HasSuffix(ev.Name, ".jsonl") {
				pending[ev.Name] = true
				flush.Reset(debounce)
			}
		case err, ok := <-w.Errors:
			if !ok {
				return nil
			}
			log.Printf("watch: %v", err)
		case <-flush.C:
			paths := make([]string, 0, len(pending))
			for p := range pending {
				paths = append(paths, p)
			}
			pending = map[string]bool{}
			s.IngestPaths(paths)
		case <-sweep.C:
			s.SweepNow()
		}
	}
}

func watchTree(w *fsnotify.Watcher, root string) error {
	return filepath.WalkDir(root, func(p string, d fs.DirEntry, err error) error {
		if err != nil {
			if p == root {
				return err
			}
			return nil
		}
		if !d.IsDir() {
			return nil
		}
		if p != root && strings.HasPrefix(d.Name(), ".") {
			return filepath.SkipDir
		}
		return w.Add(p)
	})
}

package ingest

import (
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

type Stats struct{ Files, NewRows int }

type Transcript struct {
	Path    string
	BornNS  int64 // birth time, or MtimeNS where the filesystem has none
	MtimeNS int64
}

// Sweep reads every transcript under root in creation order. On a cold read the
// order decides attribution: a resumed or forked session copies its parent's
// responses into a new file, and the first session to bring a response keeps
// it. Modification order is not enough, because the parent may still be running.
func (in *Ingester) Sweep(root string) (Stats, error) {
	var st Stats
	files, err := List(root)
	if err != nil {
		return st, err
	}
	for _, f := range files {
		n, err := in.File(f.Path)
		if err != nil {
			return st, fmt.Errorf("%s: %w", f.Path, err)
		}
		st.Files++
		st.NewRows += n
	}
	return st, nil
}

// List finds *.jsonl under root, skipping names that start with "." as
// Python's glob("**/*.jsonl") does, ordered by birth time, then mtime, then path.
func List(root string) ([]Transcript, error) {
	var out []Transcript
	err := filepath.WalkDir(root, func(p string, d fs.DirEntry, err error) error {
		if err != nil {
			if p == root {
				return err
			}
			return nil // an unreadable subtree is skipped, the sweep goes on
		}
		if p != root && strings.HasPrefix(d.Name(), ".") {
			if d.IsDir() {
				return filepath.SkipDir
			}
			return nil
		}
		if d.IsDir() || !strings.HasSuffix(d.Name(), ".jsonl") {
			return nil
		}
		info, err := d.Info()
		if err != nil {
			return nil
		}
		mtime := info.ModTime().UnixNano()
		out = append(out, Transcript{Path: p, BornNS: bornNS(p, mtime), MtimeNS: mtime})
		return nil
	})
	sortByCreation(out)
	return out, err
}

// Order puts a batch of paths (the watcher's) in the same creation order as
// List. A path that cannot be stat'ed goes last; File skips it.
func Order(paths []string) []string {
	ts := make([]Transcript, 0, len(paths))
	var gone []string
	for _, p := range paths {
		info, err := os.Stat(p)
		if err != nil {
			gone = append(gone, p)
			continue
		}
		mtime := info.ModTime().UnixNano()
		ts = append(ts, Transcript{Path: p, BornNS: bornNS(p, mtime), MtimeNS: mtime})
	}
	sortByCreation(ts)
	out := make([]string, 0, len(paths))
	for _, t := range ts {
		out = append(out, t.Path)
	}
	return append(out, gone...)
}

func sortByCreation(ts []Transcript) {
	sort.Slice(ts, func(i, j int) bool {
		if ts[i].BornNS != ts[j].BornNS {
			return ts[i].BornNS < ts[j].BornNS
		}
		if ts[i].MtimeNS != ts[j].MtimeNS {
			return ts[i].MtimeNS < ts[j].MtimeNS
		}
		return ts[i].Path < ts[j].Path
	})
}

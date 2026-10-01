package ingest

import (
	"fmt"
	"io/fs"
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
	sort.Slice(out, func(i, j int) bool {
		if out[i].BornNS != out[j].BornNS {
			return out[i].BornNS < out[j].BornNS
		}
		if out[i].MtimeNS != out[j].MtimeNS {
			return out[i].MtimeNS < out[j].MtimeNS
		}
		return out[i].Path < out[j].Path
	})
	return out, err
}

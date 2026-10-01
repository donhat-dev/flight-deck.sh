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
	MtimeNS int64
}

// Sweep reads every transcript under root, oldest first. On a cold read the
// order decides attribution: a resumed session copies its parent's responses,
// and the first session to bring a response keeps it.
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
// Python's glob("**/*.jsonl") does, ordered by mtime and then path.
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
		out = append(out, Transcript{p, info.ModTime().UnixNano()})
		return nil
	})
	sort.Slice(out, func(i, j int) bool {
		if out[i].MtimeNS != out[j].MtimeNS {
			return out[i].MtimeNS < out[j].MtimeNS
		}
		return out[i].Path < out[j].Path
	})
	return out, err
}

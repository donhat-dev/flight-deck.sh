// Package archtest checks the layer rules of ML-4 (docs/briefs/
// 2026-10-21-monorepo-l3.md): go/core stays free of HTTP and of every layer
// above it, server does not reach the runner, the runner stays standalone,
// and nothing imports a cmd package. It parses imports with go/parser over
// every .go file under go/ except this directory, so it runs as an ordinary
// `go test` package inside `make go-test`.
//
// Since ML-7 each layer is its own module under go/go.work. A second test
// checks the module boundaries the import rules imply: a module's go.mod may
// only require internal modules on its allowed edge, and every cross-module
// import in its sources must be declared there (the workspace resolves
// imports without a require line, so imports alone would not notice).
package archtest

import (
	"go/parser"
	"go/token"
	"io/fs"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"
)

// layerOf maps a .go file's directory (relative to go/) to its layer.
// "" means the file is outside the checked layers (archtest itself, stray
// top-level files); those still get the global cmd rule.
func layerOf(dir string) string {
	switch {
	case dir == "core" || strings.HasPrefix(dir, "core/"):
		return "core"
	case dir == "server" || strings.HasPrefix(dir, "server/"):
		return "server"
	case dir == "runner" || strings.HasPrefix(dir, "runner/"):
		return "runner"
	case dir == "cmd" || strings.HasPrefix(dir, "cmd/"):
		return "cmd"
	}
	return ""
}

// bannedImport reports why `path` is forbidden in `layer`, or "".
func bannedImport(layer, path string) string {
	if path == "flightdeck/cmd" || strings.HasPrefix(path, "flightdeck/cmd/") {
		return "nothing imports flightdeck/cmd/..."
	}
	switch layer {
	case "core":
		switch {
		case path == "net/http":
			return "core must not import net/http"
		case path == "flightdeck/server" || strings.HasPrefix(path, "flightdeck/server/"):
			return "core must not import server"
		case path == "flightdeck/runner" || strings.HasPrefix(path, "flightdeck/runner/"):
			return "core must not import runner"
		}
	case "server":
		if path == "flightdeck/runner" || strings.HasPrefix(path, "flightdeck/runner/") {
			return "server must not import runner"
		}
	case "runner":
		if path == "flightdeck/core" || strings.HasPrefix(path, "flightdeck/core/") ||
			path == "flightdeck/server" || strings.HasPrefix(path, "flightdeck/server/") {
			return "runner must not import core or server"
		}
	}
	return ""
}

func TestImportLayers(t *testing.T) {
	// go test runs with the package dir (go/archtest) as cwd. Abs first:
	// walking ".." would SkipDir the root itself (name starts with ".").
	root, err := filepath.Abs("..")
	if err != nil {
		t.Fatalf("resolve go/ dir: %v", err)
	}
	var violations []string
	err = filepath.WalkDir(root, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.IsDir() {
			name := d.Name()
			if name == "archtest" || strings.HasPrefix(name, ".") || name == "bin" || name == "vendor" {
				return filepath.SkipDir
			}
			return nil
		}
		if !strings.HasSuffix(path, ".go") {
			return nil
		}
		fset := token.NewFileSet()
		f, err := parser.ParseFile(fset, path, nil, parser.ImportsOnly)
		if err != nil {
			t.Errorf("parse %s: %v", path, err)
			return nil
		}
		rel, err := filepath.Rel(root, path)
		if err != nil {
			return err
		}
		layer := layerOf(filepath.ToSlash(filepath.Dir(rel)))
		for _, imp := range f.Imports {
			p := strings.Trim(imp.Path.Value, `"`)
			if why := bannedImport(layer, p); why != "" {
				violations = append(violations, rel+": imports "+p+" ("+why+")")
			}
		}
		return nil
	})
	if err != nil {
		t.Fatalf("walk go/: %v", err)
	}
	for _, v := range violations {
		t.Error(v)
	}
}

// allowedEdges lists the internal modules each module's go.mod may require —
// the module-level form of the import rules in bannedImport. archtest parses
// sources and may not link against the code it checks.
var allowedEdges = map[string][]string{
	"core":     {},
	"server":   {"flightdeck/core"},
	"runner":   {},
	"cmd":      {"flightdeck/core", "flightdeck/server", "flightdeck/runner"},
	"archtest": {},
}

// internalModule returns "flightdeck/<mod>" for a flightdeck/* import path,
// or "" for anything else.
func internalModule(path string) string {
	rest, ok := strings.CutPrefix(path, "flightdeck/")
	if !ok {
		return ""
	}
	mod, _, _ := strings.Cut(rest, "/")
	return "flightdeck/" + mod
}

// requires parses the internal (`flightdeck/*`) modules a go.mod requires,
// and its `module` line. Plain line parsing is enough: go.mod require entries
// are `path version` at line start or inside a require ( ... ) block.
func requires(path string) (module string, reqs map[string]bool, err error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return "", nil, err
	}
	reqs = map[string]bool{}
	for _, line := range strings.Split(string(data), "\n") {
		fields := strings.Fields(line)
		if len(fields) == 0 || strings.HasPrefix(fields[0], "//") {
			continue
		}
		if fields[0] == "module" && len(fields) > 1 {
			module = fields[1]
			continue
		}
		if fields[0] == "require" {
			fields = fields[1:]
		}
		if len(fields) >= 2 && fields[1] != "=>" && strings.HasPrefix(fields[0], "flightdeck/") {
			reqs[fields[0]] = true
		}
	}
	return module, reqs, nil
}

func TestModuleBoundaries(t *testing.T) {
	root, err := filepath.Abs("..")
	if err != nil {
		t.Fatalf("resolve go/ dir: %v", err)
	}

	// Which internal modules each module's sources import, keyed by the
	// top-level dir (one module per dir).
	imported := map[string]map[string]bool{}
	err = filepath.WalkDir(root, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.IsDir() {
			if strings.HasPrefix(d.Name(), ".") || d.Name() == "bin" || d.Name() == "vendor" {
				return filepath.SkipDir
			}
			return nil
		}
		if !strings.HasSuffix(path, ".go") {
			return nil
		}
		rel, err := filepath.Rel(root, path)
		if err != nil {
			return err
		}
		owner, _, _ := strings.Cut(filepath.ToSlash(rel), "/")
		if _, ok := allowedEdges[owner]; !ok {
			return nil
		}
		fset := token.NewFileSet()
		f, err := parser.ParseFile(fset, path, nil, parser.ImportsOnly)
		if err != nil {
			t.Errorf("parse %s: %v", path, err)
			return nil
		}
		for _, imp := range f.Imports {
			p := strings.Trim(imp.Path.Value, `"`)
			if m := internalModule(p); m != "" && m != "flightdeck/"+owner {
				if imported[owner] == nil {
					imported[owner] = map[string]bool{}
				}
				imported[owner][m] = true
			}
		}
		return nil
	})
	if err != nil {
		t.Fatalf("walk go/: %v", err)
	}

	dirs := make([]string, 0, len(allowedEdges))
	for dir := range allowedEdges {
		dirs = append(dirs, dir)
	}
	sort.Strings(dirs)
	for _, dir := range dirs {
		modPath := filepath.Join(root, dir, "go.mod")
		module, reqs, err := requires(modPath)
		if err != nil {
			t.Errorf("%s: %v", modPath, err)
			continue
		}
		if want := "flightdeck/" + dir; module != want {
			t.Errorf("%s: module is %q, want %q", modPath, module, want)
		}
		allowed := map[string]bool{}
		for _, m := range allowedEdges[dir] {
			allowed[m] = true
		}
		for req := range reqs {
			if !allowed[req] {
				t.Errorf("%s: requires %s, not an allowed edge", modPath, req)
			}
		}
		for m := range imported[dir] {
			if !reqs[m] {
				t.Errorf("%s/%s imports %s but %s does not require it", dir, dir, m, modPath)
			}
		}
	}
}

// Package archtest checks the layer rules of ML-4 (docs/briefs/
// 2026-10-21-monorepo-l3.md): go/core stays free of HTTP and of every layer
// above it, server does not reach the runner, the runner stays standalone,
// and nothing imports a cmd package. It parses imports with go/parser over
// every .go file under go/ except this directory, so it runs as an ordinary
// `go test ./...` package inside `make go-test`.
package archtest

import (
	"go/parser"
	"go/token"
	"io/fs"
	"path/filepath"
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

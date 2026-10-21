package runner

import (
	"os"
	"path/filepath"
	"testing"
)

func TestMainHelpReturnsWithoutExiting(t *testing.T) {
	if got := Main([]string{"-h"}); got != 0 && got != 2 {
		t.Errorf("-h: got %d, want 0 or 2", got)
	}
}

func TestMainUnknownFlag(t *testing.T) {
	if got := Main([]string{"-nope"}); got != 2 {
		t.Errorf("unknown flag: got %d, want 2", got)
	}
}

func TestMainMissingSpool(t *testing.T) {
	if got := Main(nil); got == 0 {
		t.Errorf("no -spool: got 0, want non-zero")
	}
	// A spool path that cannot be created: a directory under a regular file.
	f := filepath.Join(t.TempDir(), "afile")
	if err := os.WriteFile(f, []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	if got := Main([]string{"-spool", filepath.Join(f, "sub")}); got == 0 {
		t.Errorf("uncreatable spool: got 0, want non-zero")
	}
}

// Main holds no global state: a second call in the same process parses and
// reports its own errors instead of reusing the first call's flags.
func TestMainTwiceInOneProcess(t *testing.T) {
	Main([]string{"-h"})
	if got := Main(nil); got == 0 {
		t.Errorf("second call: got 0, want non-zero")
	}
}

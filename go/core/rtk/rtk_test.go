package rtk

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

const sample = `RTK Token Savings (Global Scope)
Total commands:    1155
Input tokens:      809.2K
Tokens saved:      562.1K (69.5%)
`

// fakeRTK puts an executable `rtk` script on PATH ahead of everything else.
func fakeRTK(t *testing.T, body string) {
	t.Helper()
	dir := t.TempDir()
	path := filepath.Join(dir, "rtk")
	if err := os.WriteFile(path, []byte("#!/bin/sh\n"+body+"\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", dir+string(os.PathListSeparator)+os.Getenv("PATH"))
}

func TestParseGainExtractsTheCounters(t *testing.T) {
	g := ParseGain(sample)
	if g.TokensSaved != 562100 || g.Commands != 1155 {
		t.Fatalf("%+v", g)
	}
}

func TestParseGainReadsEveryNumberShape(t *testing.T) {
	for text, want := range map[string]int{
		"Tokens saved: 2,345": 2345,
		"Tokens saved: 12K":   12000,
		"Tokens saved: 1.5M":  1500000,
		"Tokens saved: 0":     0,
	} {
		if g := ParseGain(text); g.TokensSaved != want {
			t.Errorf("%q: %d, want %d", text, g.TokensSaved, want)
		}
	}
}

func TestParseGainMissingCountersAreZero(t *testing.T) {
	if g := ParseGain("nothing here"); g != (Gain{}) {
		t.Fatalf("%+v", g)
	}
}

func TestSavingsUSDPricesSavedTokens(t *testing.T) {
	fakeRTK(t, "cat <<'EOF'\n"+sample+"EOF")
	if got, want := SavingsUSD(), 562100.0*5.0/1e6; got != want {
		t.Fatalf("%v, want %v", got, want)
	}
}

func TestSavingsUSDWithoutTheBinaryIsZero(t *testing.T) {
	t.Setenv("PATH", t.TempDir())
	if got := SavingsUSD(); got != 0 {
		t.Fatalf("%v", got)
	}
}

func TestSavingsUSDOnFailureIsZero(t *testing.T) {
	fakeRTK(t, "echo 'Tokens saved: 9M'; exit 3")
	if got := SavingsUSD(); got != 0 {
		t.Fatalf("%v", got)
	}
}

func TestSavingsUSDWithoutACounterIsZero(t *testing.T) {
	fakeRTK(t, "echo 'rtk did nothing'")
	if got := SavingsUSD(); got != 0 {
		t.Fatalf("%v", got)
	}
}

func TestSavingsUSDOnTimeoutIsZero(t *testing.T) {
	// The sleep keeps no handle on the script's stdout, so killing the shell
	// on timeout lets CommandContext return instead of outwaiting the child.
	fakeRTK(t, "sleep 30 >/dev/null 2>&1")
	old := Timeout
	Timeout = 50 * time.Millisecond
	t.Cleanup(func() { Timeout = old })
	if got := SavingsUSD(); got != 0 {
		t.Fatalf("%v", got)
	}
}

// A wrapper that leaves a child holding stdout open must not outlive the
// timeout, and the child is killed with it.
func TestSavingsUSDTimeoutKillsDescendants(t *testing.T) {
	pidFile := filepath.Join(t.TempDir(), "child.pid")
	fakeRTK(t, "sleep 30 & echo $! > "+pidFile+"\nwait")
	old := Timeout
	Timeout = 200 * time.Millisecond
	t.Cleanup(func() { Timeout = old })
	began := time.Now()
	if got := SavingsUSD(); got != 0 {
		t.Fatalf("%v", got)
	}
	if d := time.Since(began); d > 3*time.Second {
		t.Fatalf("returned after %v", d)
	}
	raw, err := os.ReadFile(pidFile)
	if err != nil {
		t.Fatal(err)
	}
	pid := strings.TrimSpace(string(raw))
	deadline := time.Now().Add(2 * time.Second)
	for {
		// Gone, or a zombie nobody reaps (PID 1 in a container is go test).
		stat, err := os.ReadFile("/proc/" + pid + "/stat")
		if err != nil || strings.Contains(string(stat), ") Z ") {
			return
		}
		if time.Now().After(deadline) {
			t.Fatalf("child %s still running: %s", pid, stat)
		}
		time.Sleep(20 * time.Millisecond)
	}
}

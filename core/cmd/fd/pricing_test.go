package main

import (
	"math"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"fdcore/internal/pricing"
)

func listPrices(t *testing.T, db string) pricing.Config {
	t.Helper()
	code, out, errOut := runFd("pricing", "list", "--json", "--db", db)
	if code != 0 {
		t.Fatalf("exit %d: %s", code, errOut)
	}
	var c pricing.Config
	oneDocument(t, out, &c)
	return c
}

func TestPricingListShowsTheShippedTable(t *testing.T) {
	c := listPrices(t, filepath.Join(t.TempDir(), "l.db"))
	if len(c.Models) != len(pricing.Defaults().Models) || c.Geo["us"] != 1.1 {
		t.Fatalf("%d models, geo %v", len(c.Models), c.Geo)
	}
}

func TestPricingSetChangesTheCostOfTokensAlreadyRead(t *testing.T) {
	db, projects := writeProjects(t)
	if code, _, errOut := runFd("pricing", "set", "claude-opus-5-5", "--input", "8", "--output", "40",
		"--cache-read-mult", "0.05", "--json", "--db", db); code != 0 {
		t.Fatalf("exit %d: %s", code, errOut)
	}
	code, out, errOut := runFd("spend", "--json", "--db", db, "--projects", projects)
	if code != 0 {
		t.Fatalf("exit %d: %s", code, errOut)
	}
	var v struct {
		Summary struct {
			TotalCost float64 `json:"total_cost"`
		} `json:"summary"`
	}
	oneDocument(t, out, &v)
	// (1000*8 + 1000*40 + 1e6*0.05*8) / 1e6
	if math.Abs(v.Summary.TotalCost-0.448) > 1e-9 {
		t.Fatalf("total_cost %v", v.Summary.TotalCost)
	}
}

// Changing Opus 5.5's input price must not quietly reset its 0.05 cache-read
// multiplier or drop its fast-mode pair: cache reads are most of its bill.
func TestPricingSetKeepsWhatItWasNotTold(t *testing.T) {
	db := filepath.Join(t.TempDir(), "l.db")
	if code, _, errOut := runFd("pricing", "set", "claude-opus-5-5", "--input", "5", "--output", "25",
		"--json", "--db", db); code != 0 {
		t.Fatalf("exit %d: %s", code, errOut)
	}
	for _, r := range listPrices(t, db).Models {
		if r.Prefix != "claude-opus-5-5" {
			continue
		}
		if r.Input != 5 || r.CacheReadMult != 0.05 || r.FastInput == nil || *r.FastInput != 8 {
			t.Fatalf("%+v", r)
		}
		return
	}
	t.Fatal("no claude-opus-5-5 row")
}

func TestPricingSetRefusesWhatWouldPriceWrongly(t *testing.T) {
	db := filepath.Join(t.TempDir(), "l.db")
	for name, args := range map[string][]string{
		"time in the date": {"claude-x", "--input", "1", "--output", "2", "--valid-from", "2026-10-01T12:00"},
		"no output price":  {"claude-x", "--input", "1"},
		"half a fast rate": {"claude-x", "--input", "1", "--output", "2", "--fast-input", "3"},
		"no prefix":        {"--input", "1", "--output", "2"},
	} {
		code, out, _ := runFd(append([]string{"pricing", "set"}, append(args, "--json", "--db", db)...)...)
		var v map[string]string
		oneDocument(t, out, &v)
		if code != 2 || v["error"] == "" {
			t.Errorf("%s: exit %d %v", name, code, v)
		}
	}
	if n := len(listPrices(t, db).Models); n != len(pricing.Defaults().Models) {
		t.Errorf("%d rows after refusals", n)
	}
}

func TestPricingImportReplacesTheTableAndRefusesUnknownFields(t *testing.T) {
	dir := t.TempDir()
	db := filepath.Join(dir, "l.db")
	good := filepath.Join(dir, "good.json")
	os.WriteFile(good, []byte(`{"models":[{"prefix":"claude-opus-5-5","input":4,"output":20,"cache_read_mult":0.05}],"geo":{}}`), 0o644)
	if code, _, errOut := runFd("pricing", "import", good, "--json", "--db", db); code != 0 {
		t.Fatalf("exit %d: %s", code, errOut)
	}
	if c := listPrices(t, db); len(c.Models) != 1 || len(c.Geo) != 0 {
		t.Fatalf("%+v", c)
	}
	bad := filepath.Join(dir, "bad.json")
	os.WriteFile(bad, []byte(`{"models":[{"prefix":"claude-x","inputs":4}]}`), 0o644)
	code, out, _ := runFd("pricing", "import", bad, "--json", "--db", db)
	var v map[string]string
	oneDocument(t, out, &v)
	if code != 2 || !strings.Contains(v["error"], "inputs") {
		t.Fatalf("exit %d %v", code, v)
	}
}

func TestPricingDefaultsImportBackUnchanged(t *testing.T) {
	dir := t.TempDir()
	db := filepath.Join(dir, "l.db")
	_, out, _ := runFd("pricing", "defaults", "--json")
	file := filepath.Join(dir, "defaults.json")
	os.WriteFile(file, []byte(out), 0o644)
	if code, _, errOut := runFd("pricing", "import", file, "--json", "--db", db); code != 0 {
		t.Fatalf("exit %d: %s", code, errOut)
	}
	if c := listPrices(t, db); len(c.Models) != len(pricing.Defaults().Models) || c.Geo["us"] != 1.1 {
		t.Fatalf("%d models, geo %v", len(c.Models), c.Geo)
	}
}

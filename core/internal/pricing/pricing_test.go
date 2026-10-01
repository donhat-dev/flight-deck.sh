package pricing

import (
	"math"
	"strings"
	"testing"
)

func near(t *testing.T, name string, got, want float64) {
	t.Helper()
	if math.Abs(got-want) > 1e-9 {
		t.Errorf("%s = %.12f, want %.12f", name, got, want)
	}
}

func defaults(t *testing.T) *Table {
	t.Helper()
	tbl, err := New(Defaults())
	if err != nil {
		t.Fatal(err)
	}
	return tbl
}

func f(v float64) *float64 { return &v }

func TestTheDefaultsMatchThePublishedTable(t *testing.T) {
	c := Defaults()
	if len(c.Models) != 17 || c.Geo["us"] != 1.1 {
		t.Fatalf("%d models, geo %v", len(c.Models), c.Geo)
	}
	byPrefix := map[string]Row{}
	for _, r := range c.Models {
		byPrefix[r.Prefix] = r
	}
	if r := byPrefix["claude-opus-5-5"]; r.CacheReadMult != 0.05 || *r.FastInput != 8 || r.Write1hMult != 2 {
		t.Errorf("opus 5.5 %+v", r)
	}
	if r := byPrefix["claude-fable-5-1"]; r.CacheReadMult != 0.025 || r.FastInput != nil {
		t.Errorf("fable 5.1 %+v", r)
	}
	if r := byPrefix["claude-sonnet-5-5"]; r.Input != 2 || r.Output != 10 || r.CacheReadMult != 0.1 {
		t.Errorf("sonnet 5.5 %+v", r)
	}
}

func TestLongestPrefixWins(t *testing.T) {
	tbl := defaults(t)
	cases := map[string]Rate{
		"claude-opus-4-8":          {5, 25},
		"claude-opus-4-1-20250805": {15, 75},
		"claude-opus-4-20250514":   {15, 75},
		"claude-opus-5-5":          {4, 20},
		"claude-opus-5-20260101":   {5, 25},
		"claude-fable-5-1":         {10, 50},
		"claude-sonnet-5-5":        {2, 10},
	}
	for m, want := range cases {
		got, ok := tbl.RateFor(m, "")
		if !ok || got != want {
			t.Errorf("%s: got %v %v, want %v", m, got, ok, want)
		}
	}
}

func TestAnUnknownModelIsUnpricedNotGuessed(t *testing.T) {
	tbl := defaults(t)
	for _, m := range []string{"", "gpt-5.6-sol", "claude-sonnet-3-7", "<synthetic>"} {
		if _, ok := tbl.RateFor(m, ""); ok {
			t.Errorf("%q is priced", m)
		}
		if _, ok := tbl.Cost(Usage{Model: m, Input: 1000}); ok {
			t.Errorf("%q has a cost", m)
		}
		if s := tbl.CacheSavings(Usage{Model: m, CacheRead: 1000}); s != 0 {
			t.Errorf("%q saves %v", m, s)
		}
	}
}

func TestCacheReadMultipliers(t *testing.T) {
	tbl := defaults(t)
	// 1M cache-read tokens: Opus 5.5 $4 x 0.05, Fable 5.1 $10 x 0.025, Opus 5 $5 x 0.1.
	for m, want := range map[string]float64{"claude-opus-5-5": 0.20, "claude-fable-5-1": 0.25, "claude-opus-5": 0.50} {
		got, _ := tbl.Cost(Usage{Model: m, CacheRead: 1_000_000})
		near(t, m, got, want)
	}
}

func TestFastModeAndUSInference(t *testing.T) {
	tbl := defaults(t)
	got, _ := tbl.Cost(Usage{Model: "claude-opus-5-5", Input: 1_000_000, Out: 1_000_000, Speed: "fast"})
	near(t, "fast in+out", got, 8+40)
	got, _ = tbl.Cost(Usage{Model: "claude-opus-5-5", CacheRead: 1_000_000, Speed: "fast"})
	near(t, "fast keeps the cache multiplier", got, 8*0.05)
	got, _ = tbl.Cost(Usage{Model: "claude-opus-4-7", Input: 1_000_000, Speed: "fast"})
	near(t, "no fast rate falls back to standard", got, 5)
	got, _ = tbl.Cost(Usage{Model: "claude-opus-5-5", Input: 1_000_000, CacheRead: 1_000_000, Geo: "us"})
	near(t, "us on every class", got, (4+4*0.05)*1.1)
}

func TestWritesCostMoreSoSavingsCanBeNegative(t *testing.T) {
	tbl := defaults(t)
	// Opus 5: 1M 5-minute writes $6.25 and 1M 1-hour writes $10; without caching both are $5.
	u := Usage{Model: "claude-opus-5", Create5m: 1_000_000, Create1h: 1_000_000}
	c, _ := tbl.Cost(u)
	near(t, "cost", c, 16.25)
	near(t, "net savings", tbl.CacheSavings(u), 10-16.25)
}

func TestARowAppliesFromItsDateByTheMessage(t *testing.T) {
	tbl, err := New(Config{Models: []Row{
		{Prefix: "claude-sonnet-5", Input: 1, Output: 5},
		{Prefix: "claude-sonnet-5", ValidFrom: "2026-09-01", Input: 2, Output: 10},
		{Prefix: "claude-later-1", ValidFrom: "2026-09-01", Input: 3, Output: 15},
	}})
	if err != nil {
		t.Fatal(err)
	}
	before, _ := tbl.RateFor("claude-sonnet-5", "2026-08-31T23:59:59.999Z")
	after, _ := tbl.RateFor("claude-sonnet-5", "2026-09-01T00:00:00.000Z")
	undated, _ := tbl.RateFor("claude-sonnet-5", "")
	if before != (Rate{1, 5}) || after != (Rate{2, 10}) || undated != (Rate{2, 10}) {
		t.Fatalf("before %v after %v undated %v", before, after, undated)
	}
	if _, ok := tbl.RateFor("claude-later-1", "2026-08-15T00:00:00Z"); ok {
		t.Error("a model is priced before its first row applies")
	}
}

func TestNormalizeFillsThePublishedMultipliers(t *testing.T) {
	c, err := Config{Models: []Row{{Prefix: "claude-x", Input: 1, Output: 2}}}.Normalize()
	if err != nil {
		t.Fatal(err)
	}
	r := c.Models[0]
	if r.CacheReadMult != 0.1 || r.Write5mMult != 1.25 || r.Write1hMult != 2 {
		t.Fatalf("%+v", r)
	}
}

func TestNormalizeRefusesATableThatWouldPriceWrongly(t *testing.T) {
	cases := map[string]Config{
		"no prefix":         {Models: []Row{{Input: 1}}},
		"time in the date":  {Models: []Row{{Prefix: "a", ValidFrom: "2026-09-01T12:00"}}},
		"duplicate row":     {Models: []Row{{Prefix: "a"}, {Prefix: "a"}}},
		"negative price":    {Models: []Row{{Prefix: "a", Input: -1}}},
		"half a fast price": {Models: []Row{{Prefix: "a", FastInput: f(8)}}},
		"zero geo":          {Geo: map[string]float64{"us": 0}},
	}
	for name, c := range cases {
		if _, err := c.Normalize(); err == nil {
			t.Errorf("%s: accepted", name)
		} else if strings.TrimSpace(err.Error()) == "" {
			t.Errorf("%s: empty error", name)
		}
	}
}

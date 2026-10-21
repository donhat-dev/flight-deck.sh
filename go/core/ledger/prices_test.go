package ledger

import (
	"testing"

	"flightdeck/core/pricing"
)

func prices(t *testing.T, s *Store) pricing.Config {
	t.Helper()
	c, err := Prices(s.DB)
	if err != nil {
		t.Fatal(err)
	}
	return c
}

func TestANewLedgerHoldsTheShippedPrices(t *testing.T) {
	s, _ := open(t)
	got, want := prices(t, s), pricing.Defaults()
	if len(got.Models) != len(want.Models) || got.Geo["us"] != 1.1 {
		t.Fatalf("%d models, geo %v", len(got.Models), got.Geo)
	}
	for i := range want.Models {
		g, w := got.Models[i], want.Models[i]
		if g.Prefix != w.Prefix || g.Input != w.Input || g.CacheReadMult != w.CacheReadMult ||
			(g.FastInput == nil) != (w.FastInput == nil) {
			t.Errorf("row %d: %+v, want %+v", i, g, w)
		}
	}
	tbl, err := PriceTable(s.DB)
	if err != nil {
		t.Fatal(err)
	}
	if r, ok := tbl.RateFor("claude-opus-5-5", ""); !ok || r != (pricing.Rate{In: 4, Out: 20}) {
		t.Fatalf("opus 5.5 %v %v", r, ok)
	}
}

func TestSetPriceAddsOrReplacesOneRow(t *testing.T) {
	s, _ := open(t)
	tx, _ := s.DB.Begin()
	if err := SetPrice(tx, pricing.Row{Prefix: "claude-opus-5-5", Input: 8, Output: 40}); err != nil {
		t.Fatal(err)
	}
	if err := SetPrice(tx, pricing.Row{Prefix: "claude-new-1", ValidFrom: "2026-10-01", Input: 1, Output: 2}); err != nil {
		t.Fatal(err)
	}
	tx.Commit()
	tbl, _ := PriceTable(s.DB)
	if r, _ := tbl.RateFor("claude-opus-5-5", ""); r != (pricing.Rate{In: 8, Out: 40}) {
		t.Errorf("opus 5.5 %v", r)
	}
	if n := len(prices(t, s).Models); n != len(pricing.Defaults().Models)+1 {
		t.Errorf("%d rows", n)
	}
}

func TestSetPriceRefusesABadRowAndKeepsTheTable(t *testing.T) {
	s, _ := open(t)
	tx, _ := s.DB.Begin()
	err := SetPrice(tx, pricing.Row{Prefix: "claude-x", ValidFrom: "2026-10-01T12:00", Input: 1})
	tx.Rollback()
	if err == nil {
		t.Fatal("a valid_from with a time was accepted")
	}
	if n := len(prices(t, s).Models); n != len(pricing.Defaults().Models) {
		t.Errorf("%d rows after a refused set", n)
	}
}

func TestAnEmptiedTableIsNotSeededAgain(t *testing.T) {
	s, path := open(t)
	tx, _ := s.DB.Begin()
	if err := ReplacePrices(tx, pricing.Config{}); err != nil {
		t.Fatal(err)
	}
	tx.Commit()
	s.Close()
	s2, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer s2.Close()
	if n := len(prices(t, s2).Models); n != 0 {
		t.Fatalf("reseeded: %d rows", n)
	}
}

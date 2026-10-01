package metrics

import (
	"encoding/json"
	"math"
	"path/filepath"
	"testing"
	"time"

	"fdcore/internal/ledger"
	"fdcore/internal/pricing"
)

// Costs by hand, rates per MTok:
//
//	m1 Opus 5.5  (1000*4 + 1000*20 + 1e6*0.05*4)/1e6      = 0.224
//	m2 Opus 5.5  (500*20 + 100000*2*4)/1e6                 = 0.81
//	m3 Fable 5.1 (2000*10 + 100*50 + 400000*0.025*10)/1e6  = 0.125
//	m4 <synthetic> and m5 claude-sonnet-3-7 are unpriced.
func fixture(t *testing.T) *ledger.Store {
	t.Helper()
	s, err := ledger.Open(filepath.Join(t.TempDir(), "ledger.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { s.Close() })
	rows := []ledger.Response{
		{MessageID: "m1", UUID: "u1", SessionID: "s1", Project: "/a", Model: "claude-opus-5-5",
			TS: "2026-09-24T10:00:00.000Z", Input: 1000, CacheRead: 1_000_000, Output: 1000},
		{MessageID: "m2", UUID: "u2", SessionID: "s1", Project: "/a", Model: "claude-opus-5-5",
			TS: "2026-09-25T10:00:00.000Z", Create1h: 100_000, Output: 500},
		{MessageID: "m3", UUID: "u3", SessionID: "s2", Project: "/b", Model: "claude-fable-5-1",
			TS: "2026-09-25T11:00:00.000Z", Input: 2000, CacheRead: 400_000, Output: 100},
		{MessageID: "m4", UUID: "u4", SessionID: "s2", Project: "/b", Model: "<synthetic>",
			TS: "2026-09-25T11:30:00.000Z", Input: 10},
		{MessageID: "m5", UUID: "u5", SessionID: "s3", Model: "claude-sonnet-3-7",
			TS: "2026-09-25T12:00:00.000Z", Input: 50, Output: 5},
	}
	tx, err := s.DB.Begin()
	if err != nil {
		t.Fatal(err)
	}
	for _, r := range rows {
		if _, err := ledger.Upsert(tx, r); err != nil {
			t.Fatal(err)
		}
	}
	if err := ledger.SetTitle(tx, "s1", "Mine", "custom"); err != nil {
		t.Fatal(err)
	}
	if err := tx.Commit(); err != nil {
		t.Fatal(err)
	}
	return s
}

func near(t *testing.T, name string, got, want float64) {
	t.Helper()
	if math.Abs(got-want) > 1e-9*math.Max(1, math.Abs(want)) {
		t.Errorf("%s = %.12f, want %.12f", name, got, want)
	}
}

func grain(t *testing.T, s *ledger.Store, since string) []Cell {
	t.Helper()
	c, err := Grain(s.DB, since)
	if err != nil {
		t.Fatal(err)
	}
	return c
}

// table is the ledger's price table: the shipped defaults unless a test edits it.
func table(t *testing.T, s *ledger.Store) *pricing.Table {
	t.Helper()
	tbl, err := ledger.PriceTable(s.DB)
	if err != nil {
		t.Fatal(err)
	}
	return tbl
}

func TestSummary(t *testing.T) {
	s := fixture(t)
	got := Summarize(table(t, s), grain(t, s, ""), 200)
	near(t, "total_cost", got.TotalCost, 1.159)
	// uncached minus cached: m1 4.024-0.224, m2 0.41-0.81 (writes cost more), m3 4.025-0.125
	near(t, "cache_savings", got.CacheSavings, 7.3)
	near(t, "cache_hit_rate", got.CacheHitRate, 1_400_000.0/1_503_060)
	// 2026-09-24T10:00 .. 2026-09-25T12:00 is 1 whole day, +1 = 2 days of subscription
	near(t, "saved_vs_subscription", got.SavedVsSubscription, 1.159-200*(2/30.4375))
	near(t, "avg_context_per_turn", got.AvgContextPerTurn, 1_503_060.0/5)
	if got.MessageCount != 5 || got.SessionCount != 3 || got.InputTokens != 3060 ||
		got.OutputTokens != 1605 || got.InputTokensRaw != 1_503_060 ||
		got.TotalContextTokens != 1_503_060 || got.UnknownModelTokens != 65 {
		t.Errorf("counts %+v", got)
	}
}

func TestSinceDropsEarlierDays(t *testing.T) {
	s := fixture(t)
	got := Summarize(table(t, s), grain(t, s, "2026-09-25"), 0)
	near(t, "total_cost", got.TotalCost, 0.935)
	if got.SessionCount != 3 || got.MessageCount != 4 {
		t.Errorf("%+v", got)
	}
}

func TestDailyCountsSyntheticAndEveryToken(t *testing.T) {
	s := fixture(t)
	d := Daily(table(t, s), grain(t, s, ""))
	if len(d) != 2 || d[0].Date != "2026-09-24" || d[1].Date != "2026-09-25" {
		t.Fatalf("%+v", d)
	}
	near(t, "day 1 cost", d[0].Cost, 0.224)
	near(t, "day 2 cost", d[1].Cost, 0.935)
	if d[0].Tokens != 1_002_000 || d[1].Tokens != 502_665 || d[1].CacheWrite != 100_000 {
		t.Errorf("%+v", d)
	}
}

func TestByModelSkipsSyntheticAndMarksUnpriced(t *testing.T) {
	s := fixture(t)
	m := ByModel(table(t, s), grain(t, s, ""))
	if len(m) != 3 || m[0].Model != "claude-opus-5-5" || m[1].Model != "claude-fable-5-1" ||
		m[2].Model != "claude-sonnet-3-7" {
		t.Fatalf("%+v", m)
	}
	near(t, "opus cost", m[0].Cost, 1.034)
	if !m[0].Priced || m[2].Priced || m[0].InputRaw != 1_101_000 {
		t.Errorf("%+v", m)
	}
}

func TestSessionsNewestFirstWithTitleProjectAndModels(t *testing.T) {
	s := fixture(t)
	rows, err := Sessions(s.DB, table(t, s), grain(t, s, ""), 100, 0)
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 3 || rows[0].SessionID != "s3" || rows[1].SessionID != "s2" || rows[2].SessionID != "s1" {
		t.Fatalf("%+v", rows)
	}
	s1 := rows[2]
	if s1.Title == nil || *s1.Title != "Mine" || s1.Project == nil || *s1.Project != "/a" ||
		s1.FirstTS != "2026-09-24T10:00:00.000Z" || s1.Turns != 2 {
		t.Errorf("s1 %+v", s1)
	}
	near(t, "s1 cost", s1.Cost, 1.034)
	if m := rows[1].Models; len(m) != 2 || m[0] != "<synthetic>" || m[1] != "claude-fable-5-1" {
		t.Errorf("s2 models %v", m)
	}
	if rows[0].Project != nil {
		t.Errorf("s3 project %q", *rows[0].Project)
	}
	page, _ := Sessions(s.DB, table(t, s), grain(t, s, ""), 1, 1)
	if len(page) != 1 || page[0].SessionID != "s2" {
		t.Errorf("page %+v", page)
	}
	far, _ := Sessions(s.DB, table(t, s), grain(t, s, ""), 10, 99)
	if far == nil || len(far) != 0 {
		t.Errorf("offset past the end: %v", far)
	}
}

func TestSinceUsesTheLocalDate(t *testing.T) {
	now := time.Date(2026, 10, 1, 12, 0, 0, 0, time.Local)
	for k, want := range map[string]string{"today": "2026-10-01", "7d": "2026-09-25", "30d": "2026-09-02", "all": "", "bogus": ""} {
		if got := Since(k, now); got != want {
			t.Errorf("%s: %q, want %q", k, got, want)
		}
	}
}

// Costs are derived at read time, so a price edit restates history.
func TestAPriceEditRestatesHistory(t *testing.T) {
	s := fixture(t)
	tx, _ := s.DB.Begin()
	if err := ledger.SetPrice(tx, pricing.Row{Prefix: "claude-sonnet-3-7", Input: 1000, Output: 0}); err != nil {
		t.Fatal(err)
	}
	tx.Commit()
	got := Summarize(table(t, s), grain(t, s, ""), 0)
	// m5 now costs 50 tokens x $1000/MTok = $0.05 and is no longer unknown.
	near(t, "total_cost", got.TotalCost, 1.159+0.05)
	if got.UnknownModelTokens != 10 {
		t.Errorf("unknown tokens %d", got.UnknownModelTokens)
	}
}

func TestAnEmptyLedgerGivesEmptyArrays(t *testing.T) {
	tbl := table(t, fixture(t))
	for name, v := range map[string]any{"daily": Daily(tbl, nil), "by_model": ByModel(tbl, nil)} {
		b, _ := json.Marshal(v)
		if string(b) != "[]" {
			t.Errorf("%s marshals as %s", name, b)
		}
	}
}

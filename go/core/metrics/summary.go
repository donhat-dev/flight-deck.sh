package metrics

import (
	"fmt"
	"time"

	"flightdeck/core/pricing"
)

const daysPerMonth = 30.4375

type Summary struct {
	TotalCost           float64 `json:"total_cost"`
	CacheSavings        float64 `json:"cache_savings"`
	SavedVsSubscription float64 `json:"saved_vs_subscription"`
	RTKSavings          float64 `json:"rtk_savings"` // not measured by fd yet
	CacheHitRate        float64 `json:"cache_hit_rate"`
	InputTokens         int64   `json:"input_tokens"`
	InputTokensRaw      int64   `json:"input_tokens_raw"`
	OutputTokens        int64   `json:"output_tokens"`
	SessionCount        int     `json:"session_count"`
	UnknownModelTokens  int64   `json:"unknown_model_tokens"`
	MessageCount        int64   `json:"message_count"`
	TotalContextTokens  int64   `json:"total_context_tokens"`
	AvgContextPerTurn   float64 `json:"avg_context_per_turn"`
}

func Summarize(prices *pricing.Table, cells []Cell, subscriptionMonthlyUSD float64) Summary {
	var s Summary
	var read, create int64
	sessions := map[string]bool{}
	minTS, maxTS := "", ""
	for _, c := range cells {
		s.MessageCount += c.Turns
		sessions[c.SessionID] = true
		read += c.CacheRead
		create += c.Create5m + c.Create1h
		s.InputTokens += c.Input
		s.OutputTokens += c.Output
		if c.FirstTS != "" && (minTS == "" || c.FirstTS < minTS) {
			minTS = c.FirstTS
		}
		if c.LastTS != "" && c.LastTS > maxTS {
			maxTS = c.LastTS
		}
		if cost, ok := prices.Cost(c.usage()); ok {
			s.TotalCost += cost
			s.CacheSavings += prices.CacheSavings(c.usage())
		} else {
			s.UnknownModelTokens += c.context() + c.Output
		}
	}
	s.SessionCount = len(sessions)
	denom := read + create + s.InputTokens
	s.InputTokensRaw, s.TotalContextTokens = denom, denom
	if denom > 0 {
		s.CacheHitRate = float64(read) / float64(denom)
	}
	if s.MessageCount > 0 {
		s.AvgContextPerTurn = float64(denom) / float64(s.MessageCount)
	}
	subscription := 0.0
	if a, err := parseTS(minTS); err == nil {
		if b, err := parseTS(maxTS); err == nil {
			days := int64(b.Sub(a)/(24*time.Hour)) + 1
			subscription = subscriptionMonthlyUSD * (float64(days) / daysPerMonth)
		}
	}
	s.SavedVsSubscription = s.TotalCost - subscription
	return s
}

// parseTS accepts what Python's datetime.fromisoformat accepted from the ledger.
func parseTS(v string) (time.Time, error) {
	for _, layout := range []string{time.RFC3339Nano, "2006-01-02T15:04:05.999999999", time.DateOnly} {
		if t, err := time.Parse(layout, v); err == nil {
			return t, nil
		}
	}
	return time.Time{}, fmt.Errorf("unparseable timestamp %q", v)
}

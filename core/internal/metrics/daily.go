package metrics

import (
	"sort"

	"fdcore/internal/pricing"
)

type Day struct {
	Date          string  `json:"date"`
	Cost          float64 `json:"cost"`
	Input         int64   `json:"input"`
	Output        int64   `json:"output"`
	CacheRead     int64   `json:"cache_read"`
	CacheWrite    int64   `json:"cache_write"`
	Tokens        int64   `json:"tokens"`
	AgentHours    float64 `json:"agent_hours"`     // 0 until the activity slice
	AgentHoursSub float64 `json:"agent_hours_sub"` // 0 until the activity slice
	AgentHoursEst float64 `json:"agent_hours_est"` // 0 until the activity slice
}

// Daily is one point per UTC day. `<synthetic>` turns count here (they are real
// turns on the chart) although ByModel leaves them out.
func Daily(prices *pricing.Table, cells []Cell) []Day {
	byDay := map[string]*Day{}
	for _, c := range cells {
		d := byDay[c.Day]
		if d == nil {
			d = &Day{Date: c.Day}
			byDay[c.Day] = d
		}
		d.Input += c.Input
		d.Output += c.Output
		d.CacheRead += c.CacheRead
		d.CacheWrite += c.Create5m + c.Create1h
		d.Tokens += c.context() + c.Output
		if cost, ok := prices.Cost(c.usage()); ok {
			d.Cost += cost
		}
	}
	out := make([]Day, 0, len(byDay))
	for _, d := range byDay {
		out = append(out, *d)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Date < out[j].Date })
	return out
}

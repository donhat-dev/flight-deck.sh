package metrics

import (
	"sort"

	"fdcore/internal/pricing"
)

// Claude Code writes "<synthetic>" turns (local command output); not a billable model.
const syntheticModel = "<synthetic>"

type ModelRow struct {
	Model     string  `json:"model"`
	Input     int64   `json:"input"`
	InputRaw  int64   `json:"input_raw"`
	Output    int64   `json:"output"`
	CacheRead int64   `json:"cache_read"`
	Cost      float64 `json:"cost"`
	Priced    bool    `json:"priced"`
}

// ByModel sums per-day cells, so a model that spans a rate change is priced on
// each side of it rather than at one blended rate.
func ByModel(prices *pricing.Table, cells []Cell) []ModelRow {
	agg := map[string]*ModelRow{}
	for _, c := range cells {
		if c.Model == syntheticModel {
			continue
		}
		m := agg[c.Model]
		if m == nil {
			_, priced := prices.RateFor(c.Model, "")
			m = &ModelRow{Model: c.Model, Priced: priced}
			agg[c.Model] = m
		}
		m.Input += c.Input
		m.InputRaw += c.context()
		m.Output += c.Output
		m.CacheRead += c.CacheRead
		if cost, ok := prices.Cost(c.usage()); ok {
			m.Cost += cost
		}
	}
	out := make([]ModelRow, 0, len(agg))
	for _, m := range agg {
		out = append(out, *m)
	}
	sort.Slice(out, func(i, j int) bool {
		if out[i].Cost != out[j].Cost {
			return out[i].Cost > out[j].Cost
		}
		return out[i].Model < out[j].Model
	})
	return out
}

// Package pricing prices Claude usage from a price table, per 1,000,000 tokens.
//
// Prices are data: the table lives in the ledger, is seeded from defaults.json
// (Anthropic's table, https://platform.claude.com/docs/en/about-claude/pricing,
// read 2026-10-01) and changes with `fd pricing`. A model with no row is
// unpriced, never priced from a neighbour: an honest gap beats an invisible
// wrong cost.
package pricing

import (
	_ "embed"
	"encoding/json"
	"fmt"
	"sort"
	"strings"
	"time"
)

//go:embed defaults.json
var defaultsJSON []byte

// Published multipliers of base input, used when a row leaves them out.
const (
	DefaultCacheReadMult = 0.1
	DefaultWrite5mMult   = 1.25
	DefaultWrite1hMult   = 2.0
)

// Row is one price. ValidFrom is "" (always) or a YYYY-MM-DD UTC date from
// which the row applies, judged by the message's own timestamp, so a price
// change restates nothing before it. It must be a bare date: the metrics grain
// is per UTC day and exact only while no rate changes inside a day.
type Row struct {
	Prefix        string   `json:"prefix"`
	ValidFrom     string   `json:"valid_from"`
	Input         float64  `json:"input"`
	Output        float64  `json:"output"`
	CacheReadMult float64  `json:"cache_read_mult"`
	Write5mMult   float64  `json:"write_5m_mult"`
	Write1hMult   float64  `json:"write_1h_mult"`
	FastInput     *float64 `json:"fast_input,omitempty"`
	FastOutput    *float64 `json:"fast_output,omitempty"`
	Note          string   `json:"note,omitempty"`
}

// Config is the whole table, in the shape `fd pricing list --json` prints and
// `fd pricing import` reads. Geo maps usage.inference_geo to a multiplier on
// every token class.
type Config struct {
	Models []Row              `json:"models"`
	Geo    map[string]float64 `json:"geo"`
}

// Defaults is the table fd ships with.
func Defaults() Config {
	var c Config
	if err := json.Unmarshal(defaultsJSON, &c); err != nil {
		panic(fmt.Sprintf("pricing: embedded defaults.json: %v", err))
	}
	n, err := c.Normalize()
	if err != nil {
		panic(fmt.Sprintf("pricing: embedded defaults.json: %v", err))
	}
	return n
}

// Normalize fills omitted multipliers with the published ones, orders the rows,
// and refuses a table that would price wrongly.
func (c Config) Normalize() (Config, error) {
	out := Config{Models: make([]Row, 0, len(c.Models)), Geo: map[string]float64{}}
	seen := map[string]bool{}
	for _, r := range c.Models {
		r.Prefix = strings.TrimSpace(r.Prefix)
		if r.Prefix == "" {
			return Config{}, fmt.Errorf("a price row has no prefix")
		}
		if r.ValidFrom != "" {
			if _, err := time.Parse(time.DateOnly, r.ValidFrom); err != nil {
				return Config{}, fmt.Errorf("%s: valid_from %q is not a YYYY-MM-DD date", r.Prefix, r.ValidFrom)
			}
		}
		key := r.Prefix + "@" + r.ValidFrom
		if seen[key] {
			return Config{}, fmt.Errorf("%s: two rows valid from %q", r.Prefix, r.ValidFrom)
		}
		seen[key] = true
		if r.CacheReadMult == 0 {
			r.CacheReadMult = DefaultCacheReadMult
		}
		if r.Write5mMult == 0 {
			r.Write5mMult = DefaultWrite5mMult
		}
		if r.Write1hMult == 0 {
			r.Write1hMult = DefaultWrite1hMult
		}
		if r.Input < 0 || r.Output < 0 || r.CacheReadMult < 0 || r.Write5mMult < 0 || r.Write1hMult < 0 {
			return Config{}, fmt.Errorf("%s: prices and multipliers cannot be negative", r.Prefix)
		}
		if (r.FastInput == nil) != (r.FastOutput == nil) {
			return Config{}, fmt.Errorf("%s: fast_input and fast_output go together", r.Prefix)
		}
		if r.FastInput != nil && (*r.FastInput < 0 || *r.FastOutput < 0) {
			return Config{}, fmt.Errorf("%s: fast prices cannot be negative", r.Prefix)
		}
		out.Models = append(out.Models, r)
	}
	for g, m := range c.Geo {
		if m <= 0 {
			return Config{}, fmt.Errorf("geo %q: the multiplier must be positive", g)
		}
		out.Geo[g] = m
	}
	sort.Slice(out.Models, func(i, j int) bool {
		if out.Models[i].Prefix != out.Models[j].Prefix {
			return out.Models[i].Prefix < out.Models[j].Prefix
		}
		return out.Models[i].ValidFrom < out.Models[j].ValidFrom
	})
	return out, nil
}

type Rate struct{ In, Out float64 }

// Table answers prices from a normalized Config.
type Table struct {
	rows map[string][]Row // per prefix, oldest ValidFrom first
	geo  map[string]float64
}

func New(c Config) (*Table, error) {
	n, err := c.Normalize()
	if err != nil {
		return nil, err
	}
	t := &Table{rows: map[string][]Row{}, geo: n.Geo}
	for _, r := range n.Models {
		t.rows[r.Prefix] = append(t.rows[r.Prefix], r)
	}
	return t, nil
}

// row picks the longest matching prefix ("claude-opus-4-8" must not price as
// "claude-opus-4"), then that prefix's latest row in effect at ts. An empty ts
// takes the latest row, so an undated message never gets an old or
// promotional price. No row in effect means unpriced, not a shorter prefix.
func (t *Table) row(model, ts string) (Row, bool) {
	best := ""
	for p := range t.rows {
		if strings.HasPrefix(model, p) && len(p) > len(best) {
			best = p
		}
	}
	if model == "" || best == "" {
		return Row{}, false
	}
	rs := t.rows[best]
	if ts == "" {
		return rs[len(rs)-1], true
	}
	for i := len(rs) - 1; i >= 0; i-- {
		if rs[i].ValidFrom <= ts {
			return rs[i], true
		}
	}
	return Row{}, false
}

// RateFor is the list (input, output) rate as of ts (an ISO timestamp).
func (t *Table) RateFor(model, ts string) (Rate, bool) {
	r, ok := t.row(model, ts)
	return Rate{r.Input, r.Output}, ok
}

// Usage is one priced unit: a response, or a grain cell that shares one rate.
type Usage struct {
	Model                                     string
	Input, CacheRead, Create5m, Create1h, Out int64
	TS, Speed, Geo                            string
}

func (t *Table) effective(u Usage) (Row, Rate, bool) {
	r, ok := t.row(u.Model, u.TS)
	if !ok {
		return Row{}, Rate{}, false
	}
	rate := Rate{r.Input, r.Output}
	if u.Speed == "fast" && r.FastInput != nil {
		rate = Rate{*r.FastInput, *r.FastOutput}
	}
	m := 1.0
	if g, ok := t.geo[u.Geo]; ok {
		m = g
	}
	return r, Rate{rate.In * m, rate.Out * m}, true
}

// Cost is the API-equivalent USD cost; false when the model is unpriced. Terms
// are added in pricing.py's order so float sums agree with the oracle.
func (t *Table) Cost(u Usage) (float64, bool) {
	r, rate, ok := t.effective(u)
	if !ok {
		return 0, false
	}
	return (float64(u.Input)*rate.In +
		float64(u.Out)*rate.Out +
		float64(u.CacheRead)*r.CacheReadMult*rate.In +
		float64(u.Create5m)*r.Write5mMult*rate.In +
		float64(u.Create1h)*r.Write1hMult*rate.In) / 1_000_000, true
}

// UncachedCost is what the same tokens cost with no prompt caching.
func (t *Table) UncachedCost(u Usage) (float64, bool) {
	_, rate, ok := t.effective(u)
	if !ok {
		return 0, false
	}
	context := u.Input + u.CacheRead + u.Create5m + u.Create1h
	return (float64(context)*rate.In + float64(u.Out)*rate.Out) / 1_000_000, true
}

// CacheSavings is uncached minus cached cost, net of the write premium; 0 when unpriced.
func (t *Table) CacheSavings(u Usage) float64 {
	c, ok := t.Cost(u)
	if !ok {
		return 0
	}
	un, _ := t.UncachedCost(u)
	return un - c
}

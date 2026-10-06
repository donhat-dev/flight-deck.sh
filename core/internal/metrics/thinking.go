package metrics

import (
	"database/sql"
	"math"
	"sort"
	"time"

	"fdcore/internal/ledger"
)

// ThinkingRow is one response as the thinking report reads it.
type ThinkingRow struct {
	// Agent is "main" for the session's own responses, "subagent" for a
	// subagent's, "" when the row was read before fd recorded it.
	Day, Model, Effort, Agent string
	Tokens, MS, WaitMS        *int64
}

func agentOf(sidechain bool) string {
	if sidechain {
		return "subagent"
	}
	return "main"
}

// ThinkingRows reads the ledger from `since` (YYYY-MM-DD, "" for all).
func ThinkingRows(db *sql.DB, since string) ([]ThinkingRow, error) {
	q := `SELECT COALESCE(substr(ts,1,10),''), COALESCE(model,''), COALESCE(effort,''),
		CASE sidechain WHEN 1 THEN 'subagent' WHEN 0 THEN 'main' ELSE '' END,
		thinking_tokens, thinking_ms, think_wait_ms FROM responses WHERE model IS NOT '<synthetic>'`
	args := []any{}
	if since != "" {
		q += " AND ts >= ?"
		args = append(args, since)
	}
	rows, err := db.Query(q, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []ThinkingRow{}
	for rows.Next() {
		var r ThinkingRow
		var tok, ms, wait sql.NullInt64
		if err := rows.Scan(&r.Day, &r.Model, &r.Effort, &r.Agent, &tok, &ms, &wait); err != nil {
			return nil, err
		}
		r.Tokens, r.MS, r.WaitMS = ptr(tok), ptr(ms), ptr(wait)
		out = append(out, r)
	}
	return out, rows.Err()
}

func ptr(v sql.NullInt64) *int64 {
	if !v.Valid {
		return nil
	}
	return &v.Int64
}

// ThinkingRowsOf turns responses read straight from the transcripts into rows.
func ThinkingRowsOf(resps []ledger.Response) []ThinkingRow {
	out := make([]ThinkingRow, 0, len(resps))
	for _, r := range resps {
		if r.Model == "<synthetic>" {
			continue
		}
		day := r.TS
		if len(day) > 10 {
			day = day[:10]
		}
		out = append(out, ThinkingRow{Day: day, Model: r.Model, Effort: r.Effort, Agent: agentOf(r.Sidechain),
			Tokens: r.ThinkingTokens, MS: r.ThinkingMS, WaitMS: r.ThinkWaitMS})
	}
	return out
}

// Stat summarises one figure over the responses that have it.
type Stat struct {
	N    int     `json:"n"`
	Sum  float64 `json:"sum"`
	Mean float64 `json:"mean"`
	P50  float64 `json:"p50"`
	P90  float64 `json:"p90"`
}

func stat(values []float64) Stat {
	if len(values) == 0 {
		return Stat{}
	}
	sort.Float64s(values)
	sum := 0.0
	for _, v := range values {
		sum += v
	}
	return Stat{N: len(values), Sum: sum, Mean: sum / float64(len(values)),
		P50: rank(values, .5), P90: rank(values, .9)}
}

// rank is the nearest-rank percentile of sorted values.
func rank(sorted []float64, p float64) float64 {
	i := int(math.Ceil(p*float64(len(sorted)))) - 1
	return sorted[max(i, 0)]
}

// ThinkingBucket is one day, model, effort or agent.
type ThinkingBucket struct {
	Key       string `json:"key"`
	Responses int    `json:"responses"`
	// Responses whose usage reported thinking tokens at all, and of them those
	// that thought (more than zero).
	TokensReported int  `json:"tokens_reported"`
	Thought        int  `json:"thought"`
	Tokens         Stat `json:"thinking_tokens"`
	// Measured by the CLI (thinkingDurationMs, 2.1.289 on), in seconds.
	Seconds Stat `json:"thinking_seconds"`
	// From the last line before the response to its last thinking block, in
	// seconds: thinking plus the wait for the first token, which grows with
	// the context. Read it as a trend within one model and effort, not as time.
	WaitSeconds Stat `json:"wait_seconds"`
}

// ThinkingFilter keeps the rows that match every field set ("" matches all).
type ThinkingFilter struct{ Model, Effort, Agent string }

func (f ThinkingFilter) keeps(r ThinkingRow) bool {
	return (f.Model == "" || r.Model == f.Model) && (f.Effort == "" || r.Effort == f.Effort) &&
		(f.Agent == "" || r.Agent == f.Agent)
}

// Thinking groups the rows the filter keeps by "day", "model", "effort" or "agent".
func Thinking(rows []ThinkingRow, by string, f ThinkingFilter) []ThinkingBucket {
	type acc struct {
		b              ThinkingBucket
		tok, sec, wait []float64
	}
	groups := map[string]*acc{}
	for _, r := range rows {
		if !f.keeps(r) {
			continue
		}
		key := r.Day
		switch by {
		case "model":
			key = r.Model
		case "effort":
			key = r.Effort
		case "agent":
			key = r.Agent
		}
		if key == "" {
			key = "unknown"
		}
		a := groups[key]
		if a == nil {
			a = &acc{b: ThinkingBucket{Key: key}}
			groups[key] = a
		}
		a.b.Responses++
		if r.Tokens != nil {
			a.b.TokensReported++
			if *r.Tokens > 0 {
				a.b.Thought++
				a.tok = append(a.tok, float64(*r.Tokens))
			}
		}
		if r.MS != nil {
			a.sec = append(a.sec, float64(*r.MS)/1000)
		}
		if r.WaitMS != nil {
			a.wait = append(a.wait, float64(*r.WaitMS)/1000)
		}
	}
	out := make([]ThinkingBucket, 0, len(groups))
	for _, a := range groups {
		a.b.Tokens, a.b.Seconds, a.b.WaitSeconds = stat(a.tok), stat(a.sec), stat(a.wait)
		out = append(out, a.b)
	}
	sort.Slice(out, func(i, j int) bool {
		if by == "day" {
			return out[i].Key < out[j].Key
		}
		return out[i].Responses > out[j].Responses
	})
	return out
}

// MinTrendSamples is how many responses a day needs to count in a trend.
const MinTrendSamples = 5

// Trend is how one daily median moved over the days that have it.
type Trend struct {
	Metric string `json:"metric"`
	Days   int    `json:"days"`
	// Least-squares slope over calendar days, as a share of the mean per day.
	SlopePctPerDay *float64 `json:"slope_pct_per_day"`
	First          *float64 `json:"first"`
	Last           *float64 `json:"last"`
	Note           string   `json:"note,omitempty"`
}

// Trends reads the daily medians of thinking tokens, thinking seconds and wait
// seconds from day buckets. A day with fewer than MinTrendSamples values is left
// out; fewer than three days give no slope.
func Trends(days []ThinkingBucket) []Trend {
	pick := []struct {
		name string
		get  func(ThinkingBucket) Stat
	}{
		{"thinking_tokens_p50", func(b ThinkingBucket) Stat { return b.Tokens }},
		{"thinking_seconds_p50", func(b ThinkingBucket) Stat { return b.Seconds }},
		{"wait_seconds_p50", func(b ThinkingBucket) Stat { return b.WaitSeconds }},
	}
	out := make([]Trend, 0, len(pick))
	for _, p := range pick {
		var xs, ys []float64
		for _, d := range days {
			s := p.get(d)
			day, err := time.Parse(time.DateOnly, d.Key)
			if err != nil || s.N < MinTrendSamples {
				continue
			}
			xs = append(xs, float64(day.Unix())/86400)
			ys = append(ys, s.P50)
		}
		t := Trend{Metric: p.name, Days: len(xs)}
		if len(ys) > 0 {
			t.First, t.Last = &ys[0], &ys[len(ys)-1]
		}
		if len(xs) < 3 {
			t.Note = "fewer than 3 days with enough data"
		} else if slope, ok := pctSlope(xs, ys); ok {
			t.SlopePctPerDay = &slope
		}
		out = append(out, t)
	}
	return out
}

func pctSlope(xs, ys []float64) (float64, bool) {
	n := float64(len(xs))
	var mx, my float64
	for i := range xs {
		mx += xs[i]
		my += ys[i]
	}
	mx, my = mx/n, my/n
	var num, den float64
	for i := range xs {
		num += (xs[i] - mx) * (ys[i] - my)
		den += (xs[i] - mx) * (xs[i] - mx)
	}
	if den == 0 || my == 0 {
		return 0, false
	}
	return 100 * (num / den) / my, true
}

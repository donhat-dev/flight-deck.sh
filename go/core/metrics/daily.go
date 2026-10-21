package metrics

import (
	"database/sql"
	"sort"

	"flightdeck/core/pricing"
)

type Day struct {
	Date          string  `json:"date"`
	Cost          float64 `json:"cost"`
	Input         int64   `json:"input"`
	Output        int64   `json:"output"`
	CacheRead     int64   `json:"cache_read"`
	CacheWrite    int64   `json:"cache_write"`
	Tokens        int64   `json:"tokens"`
	AgentHours    float64 `json:"agent_hours"`     // main + subagent + estimate busy time, hours
	AgentHoursSub float64 `json:"agent_hours_sub"` // the subagent part
	AgentHoursEst float64 `json:"agent_hours_est"` // the ledger-method estimate; Go never writes those rows
}

// ActivityDay is the agent busy seconds of one UTC day, split by source:
// main agents and subagents measured from the transcript, and the ledger-method
// estimate kept for sessions whose transcript is gone (Go writes none).
type ActivityDay struct {
	Day            string
	Main, Sub, Est float64
}

// ActivityRollup reads agent_activity per day, from `since` on (a bare
// YYYY-MM-DD, or "" for everything).
func ActivityRollup(db *sql.DB, since string) ([]ActivityDay, error) {
	q := `SELECT day,
	  SUM(CASE WHEN method='transcript' AND agent_id='' THEN busy_seconds ELSE 0 END),
	  SUM(CASE WHEN method='transcript' AND agent_id<>'' THEN busy_seconds ELSE 0 END),
	  SUM(CASE WHEN method='ledger' THEN busy_seconds ELSE 0 END)
	FROM agent_activity`
	var args []any
	if since != "" {
		if len(since) > 10 {
			since = since[:10]
		}
		q += " WHERE day >= ?"
		args = append(args, since)
	}
	rows, err := db.Query(q+" GROUP BY day", args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []ActivityDay{}
	for rows.Next() {
		var a ActivityDay
		if err := rows.Scan(&a.Day, &a.Main, &a.Sub, &a.Est); err != nil {
			return nil, err
		}
		out = append(out, a)
	}
	return out, rows.Err()
}

// Daily is one point per UTC day: cost, tokens, and agent work-hours.
// `<synthetic>` turns count here (they are real turns on the chart) although
// ByModel leaves them out. A day with activity but no billed row still gets a
// point, as the Python series does.
func Daily(prices *pricing.Table, cells []Cell, activity []ActivityDay) []Day {
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
	for _, a := range activity {
		d := byDay[a.Day]
		if d == nil {
			d = &Day{Date: a.Day}
			byDay[a.Day] = d
		}
		d.AgentHours = (a.Main + a.Sub + a.Est) / 3600
		d.AgentHoursSub = a.Sub / 3600
		d.AgentHoursEst = a.Est / 3600
	}
	out := make([]Day, 0, len(byDay))
	for _, d := range byDay {
		out = append(out, *d)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Date < out[j].Date })
	return out
}

// Package metrics answers the spend questions from the ledger with the same
// numbers as backend/flightdeck/metrics.py, endpoint for endpoint.
package metrics

import (
	"database/sql"
	"time"

	"flightdeck/core/pricing"
)

// Cell aggregates the ledger at (UTC day, model, session, speed, geo). A rate
// never changes inside a UTC day (the pricing tests guard it), so one rate
// prices a whole cell exactly. NULL model, session, speed and geo read as "".
type Cell struct {
	Day, Model, SessionID, Speed, Geo           string
	Turns, Input, CacheRead, Create5m, Create1h int64
	Output                                      int64
	FirstTS, LastTS                             string // "" when no row in the cell has a ts
}

const grainSQL = `SELECT COALESCE(substr(ts,1,10),''), COALESCE(model,''), COALESCE(session_id,''),
  COALESCE(speed,''), COALESCE(inference_geo,''), COUNT(*),
  SUM(input_tokens), SUM(cache_read), SUM(cache_create_5m), SUM(cache_create_1h), SUM(output_tokens),
  COALESCE(MIN(ts),''), COALESCE(MAX(ts),'')
FROM responses`

// Grain reads the cells from `since` on: a bare YYYY-MM-DD, or "" for everything.
func Grain(db *sql.DB, since string) ([]Cell, error) {
	q, args := grainSQL, []any{}
	if since != "" {
		q += " WHERE ts >= ?"
		args = append(args, since)
	}
	rows, err := db.Query(q+" GROUP BY 1, 2, 3, 4, 5", args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	cells := []Cell{}
	for rows.Next() {
		var c Cell
		if err := rows.Scan(&c.Day, &c.Model, &c.SessionID, &c.Speed, &c.Geo, &c.Turns,
			&c.Input, &c.CacheRead, &c.Create5m, &c.Create1h, &c.Output, &c.FirstTS, &c.LastTS); err != nil {
			return nil, err
		}
		cells = append(cells, c)
	}
	return cells, rows.Err()
}

func (c Cell) usage() pricing.Usage {
	return pricing.Usage{Model: c.Model, Input: c.Input, CacheRead: c.CacheRead,
		Create5m: c.Create5m, Create1h: c.Create1h, Out: c.Output,
		TS: c.FirstTS, Speed: c.Speed, Geo: c.Geo}
}

// context is every input-side token: uncached input, cache reads and cache writes.
func (c Cell) context() int64 { return c.Input + c.CacheRead + c.Create5m + c.Create1h }

// Since turns a UI range into a cutoff date. Like runtime.since() it takes the
// LOCAL calendar date and compares it with UTC timestamps; kept for parity.
func Since(rangeKey string, now time.Time) string {
	d := now.Local()
	switch rangeKey {
	case "today":
		return d.Format(time.DateOnly)
	case "7d":
		return d.AddDate(0, 0, -6).Format(time.DateOnly)
	case "30d":
		return d.AddDate(0, 0, -29).Format(time.DateOnly)
	}
	return ""
}

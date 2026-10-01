package metrics

import (
	"database/sql"
	"sort"

	"fdcore/internal/pricing"
)

type SessionRow struct {
	SessionID   string   `json:"session_id"`
	Project     *string  `json:"project"`
	Models      []string `json:"models"`
	FirstTS     string   `json:"first_ts"`
	LastTS      string   `json:"last_ts"`
	Turns       int64    `json:"turns"`
	Input       int64    `json:"input"`
	Output      int64    `json:"output"`
	CacheRead   int64    `json:"cache_read"`
	CacheCreate int64    `json:"cache_create"`
	Cost        float64  `json:"cost"`
	Title       *string  `json:"title"`
	Context     int64    `json:"context"`
	InputRaw    int64    `json:"input_raw"`
	AvgContext  float64  `json:"avg_context"`
	CacheRatio  float64  `json:"cache_ratio"`
}

// Sessions pages sessions by last activity, newest first. The Python endpoint
// also attaches `subagents`, read from transcript files per request; that waits
// for a later slice.
func Sessions(db *sql.DB, prices *pricing.Table, cells []Cell, limit, offset int) ([]SessionRow, error) {
	type acc struct {
		row    SessionRow
		models map[string]bool
	}
	agg := map[string]*acc{}
	for _, c := range cells {
		a := agg[c.SessionID]
		if a == nil {
			a = &acc{row: SessionRow{SessionID: c.SessionID, FirstTS: c.FirstTS, LastTS: c.LastTS},
				models: map[string]bool{}}
			agg[c.SessionID] = a
		}
		r := &a.row
		a.models[c.Model] = true
		r.Turns += c.Turns
		if c.FirstTS < r.FirstTS {
			r.FirstTS = c.FirstTS
		}
		if c.LastTS > r.LastTS {
			r.LastTS = c.LastTS
		}
		r.Input += c.Input
		r.Output += c.Output
		r.CacheRead += c.CacheRead
		r.CacheCreate += c.Create5m + c.Create1h
		if cost, ok := prices.Cost(c.usage()); ok {
			r.Cost += cost
		}
	}
	projects, err := bySession(db, `SELECT COALESCE(session_id,''), MAX(project) FROM responses GROUP BY 1`)
	if err != nil {
		return nil, err
	}
	titles, err := bySession(db, `SELECT session_id, title FROM session_meta`)
	if err != nil {
		return nil, err
	}
	out := make([]SessionRow, 0, len(agg))
	for id, a := range agg {
		r := a.row
		r.Project, r.Title = projects[id], titles[id]
		r.Context = r.Input + r.CacheRead + r.CacheCreate
		r.InputRaw = r.Context
		if r.Turns > 0 {
			r.AvgContext = float64(r.Context) / float64(r.Turns)
		}
		if r.Context > 0 {
			r.CacheRatio = float64(r.CacheRead) / float64(r.Context)
		}
		r.Models = []string{}
		for m := range a.models {
			if m != "" {
				r.Models = append(r.Models, m)
			}
		}
		sort.Strings(r.Models)
		out = append(out, r)
	}
	sort.Slice(out, func(i, j int) bool {
		if out[i].LastTS != out[j].LastTS {
			return out[i].LastTS > out[j].LastTS
		}
		return out[i].SessionID < out[j].SessionID
	})
	offset = min(max(offset, 0), len(out))
	end := min(offset+max(limit, 0), len(out))
	return out[offset:end], nil
}

// bySession reads (session, value) pairs; a NULL value stays nil (JSON null).
func bySession(db *sql.DB, q string) (map[string]*string, error) {
	rows, err := db.Query(q)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	m := map[string]*string{}
	for rows.Next() {
		var id string
		var v sql.NullString
		if err := rows.Scan(&id, &v); err != nil {
			return nil, err
		}
		if v.Valid {
			s := v.String
			m[id] = &s
		}
	}
	return m, rows.Err()
}

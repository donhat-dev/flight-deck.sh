package main

import (
	"fmt"
	"io"
	"text/tabwriter"
	"time"

	"flightdeck/core/ledger"
	"flightdeck/core/metrics"
	"flightdeck/core/pricing"
)

func init() {
	commands["ingest"] = command{"read new transcript lines into the ledger", cmdIngest}
	commands["spend"] = command{"cost and tokens for a range: summary, by model, by day", cmdSpend}
	commands["sessions"] = command{"sessions by last activity, with cost", cmdSessions}
}

func cmdIngest(args []string) (output, error) {
	fs := newFlags("ingest")
	p := addPaths(fs)
	if err := parse(fs, args); err != nil {
		return output{}, err
	}
	s, err := ledger.Open(p.db)
	if err != nil {
		return output{}, err
	}
	defer s.Close()
	t0 := time.Now()
	st, err := ingestAll(s, p.projects)
	if err != nil {
		return output{}, err
	}
	secs := time.Since(t0).Seconds()
	data := map[string]any{"files": st.Files, "new_rows": st.NewRows, "seconds": secs, "db": p.db}
	return output{Data: data, Text: func(w io.Writer) {
		fmt.Fprintf(w, "%d new responses from %d files in %.1fs into %s\n", st.NewRows, st.Files, secs, p.db)
	}}, nil
}

// ledgerCells opens the ledger, brings it up to date unless told not to, and
// reads the price table and the grain for a range. The caller closes the store.
func ledgerCells(p *paths, rng string, noIngest bool) (*ledger.Store, *pricing.Table, []metrics.Cell, error) {
	s, err := ledger.Open(p.db)
	if err != nil {
		return nil, nil, nil, err
	}
	fail := func(err error) (*ledger.Store, *pricing.Table, []metrics.Cell, error) {
		s.Close()
		return nil, nil, nil, err
	}
	if !noIngest {
		if _, err := ingestAll(s, p.projects); err != nil {
			return fail(err)
		}
	}
	prices, err := ledger.PriceTable(s.DB)
	if err != nil {
		return fail(err)
	}
	cells, err := metrics.Grain(s.DB, metrics.Since(rng, time.Now()))
	if err != nil {
		return fail(err)
	}
	return s, prices, cells, nil
}

func cmdSpend(args []string) (output, error) {
	fs := newFlags("spend")
	p := addPaths(fs)
	rng := fs.String("range", "all", "today, 7d, 30d or all")
	noIngest := fs.Bool("no-ingest", false, "answer from the ledger as it is")
	sub := fs.Float64("subscription-usd", envFloat("FD_SUBSCRIPTION_USD", 0),
		"monthly subscription price, for saved_vs_subscription")
	if err := parse(fs, args); err != nil {
		return output{}, err
	}
	if err := validRange(*rng); err != nil {
		return output{}, err
	}
	s, prices, cells, err := ledgerCells(p, *rng, *noIngest)
	if err != nil {
		return output{}, err
	}
	defer s.Close()
	sum := metrics.Summarize(prices, cells, *sub)
	models := metrics.ByModel(prices, cells)
	act, err := metrics.ActivityRollup(s.DB, metrics.Since(*rng, time.Now()))
	if err != nil {
		return output{}, err
	}
	data := map[string]any{"range": *rng, "summary": sum, "by_model": models,
		"daily": metrics.Daily(prices, cells, act)}
	return output{Data: data, Text: func(w io.Writer) {
		fmt.Fprintf(w, "%s: $%.2f over %d responses in %d sessions, cache hit %.1f%%\n",
			*rng, sum.TotalCost, sum.MessageCount, sum.SessionCount, 100*sum.CacheHitRate)
		tw := tabwriter.NewWriter(w, 0, 0, 2, ' ', 0)
		fmt.Fprintln(tw, "model\tcost\toutput\tinput+cache")
		for _, m := range models {
			fmt.Fprintf(tw, "%s\t$%.2f\t%d\t%d\n", m.Model, m.Cost, m.Output, m.InputRaw)
		}
		tw.Flush()
	}}, nil
}

func cmdSessions(args []string) (output, error) {
	fs := newFlags("sessions")
	p := addPaths(fs)
	rng := fs.String("range", "all", "today, 7d, 30d or all")
	limit := fs.Int("limit", 20, "sessions per page")
	offset := fs.Int("offset", 0, "sessions to skip")
	noIngest := fs.Bool("no-ingest", false, "answer from the ledger as it is")
	if err := parse(fs, args); err != nil {
		return output{}, err
	}
	if err := validRange(*rng); err != nil {
		return output{}, err
	}
	if *limit < 0 || *offset < 0 {
		return output{}, refuse("--limit and --offset cannot be negative")
	}
	s, prices, cells, err := ledgerCells(p, *rng, *noIngest)
	if err != nil {
		return output{}, err
	}
	defer s.Close()
	rows, err := metrics.Sessions(s.DB, prices, cells, *limit, *offset)
	if err != nil {
		return output{}, err
	}
	return output{Data: rows, Text: func(w io.Writer) {
		tw := tabwriter.NewWriter(w, 0, 0, 2, ' ', 0)
		fmt.Fprintln(tw, "last activity\tcost\tturns\tsession")
		for _, r := range rows {
			name := r.SessionID
			if r.Title != nil {
				name = *r.Title
			} else if r.Project != nil {
				name = *r.Project
			}
			fmt.Fprintf(tw, "%s\t$%.2f\t%d\t%s\n", r.LastTS, r.Cost, r.Turns, name)
		}
		tw.Flush()
	}}, nil
}

package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"text/tabwriter"
	"time"

	"fdcore/internal/ingest"
	"fdcore/internal/ledger"
	"fdcore/internal/metrics"
)

func init() {
	commands["thinking"] = command{"thinking tokens and time per day, model or effort, with the trend", cmdThinking}
}

// warnOut is where warnings go: stderr, so stdout keeps one JSON document.
var warnOut io.Writer = os.Stderr

func warn(format string, a ...any) string {
	msg := fmt.Sprintf(format, a...)
	fmt.Fprintln(warnOut, "fd thinking: warning: "+msg)
	return msg
}

func cmdThinking(args []string) (output, error) {
	fs := newFlags("thinking")
	p := addPaths(fs)
	days := fs.Int("days", 7, "days back from today, today included")
	fromFlag := fs.String("from", "", "start of the range instead of --days: a date (local midnight) "+
		"or a time such as 2026-10-01T16:27+07:00")
	toFlag := fs.String("to", "", "end of the range, not included: a date (local midnight) or a time")
	by := fs.String("by", "day", "day, model, effort, agent or split")
	splitAt := fs.String("split-at", "", "a time: buckets before and after it (sets --by split)")
	model := fs.String("model", "", "only this model, e.g. claude-opus-5-5")
	effort := fs.String("effort", "", "only this effort, e.g. high")
	agent := fs.String("agent", "", "only main (the sessions' own responses) or subagent")
	raw := fs.Bool("raw", false, "read the transcripts themselves instead of the ledger (slow)")
	noIngest := fs.Bool("no-ingest", false, "answer from the ledger as it is")
	if err := parse(fs, args); err != nil {
		return output{}, err
	}
	if *days < 1 {
		return output{}, refuse("--days must be 1 or more")
	}
	byGiven := false
	fs.Visit(func(f *flag.Flag) { byGiven = byGiven || f.Name == "by" })
	if *splitAt != "" && !byGiven {
		*by = "split"
	}
	switch *by {
	case "day", "model", "effort", "agent":
	case "split":
		if *splitAt == "" {
			return output{}, refuse("--by split needs --split-at")
		}
	default:
		return output{}, refuse("--by must be day, model, effort, agent or split, not %q", *by)
	}
	if *agent != "" && *agent != "main" && *agent != "subagent" {
		return output{}, refuse("--agent must be main or subagent, not %q", *agent)
	}
	// Like the spend ranges: the local calendar date, compared with UTC timestamps.
	from := time.Now().Local().AddDate(0, 0, -(*days - 1))
	from = time.Date(from.Year(), from.Month(), from.Day(), 0, 0, 0, 0, time.Local)
	since, until, split := from.Format(time.DateOnly), "", ""
	if *fromFlag != "" {
		t, err := parseWhen(*fromFlag)
		if err != nil {
			return output{}, err
		}
		from, since = t, utcStamp(t)
	}
	if *toFlag != "" {
		t, err := parseWhen(*toFlag)
		if err != nil {
			return output{}, err
		}
		until = utcStamp(t)
	}
	if *splitAt != "" {
		t, err := parseWhen(*splitAt)
		if err != nil {
			return output{}, err
		}
		split = utcStamp(t)
	}

	var rows []metrics.ThinkingRow
	var warnings []string
	var reach *coverage
	source := "ledger"
	if *raw {
		source = "transcripts"
		var err error
		rows, reach, warnings, err = rawRows(p.projects, from, since, until)
		if err != nil {
			return output{}, err
		}
	} else {
		s, err := ledger.Open(p.db)
		if err != nil {
			return output{}, err
		}
		defer s.Close()
		if !*noIngest {
			if _, err := ingestAll(s, p.projects); err != nil {
				return output{}, err
			}
		}
		if rows, err = metrics.ThinkingRows(s.DB, since, until); err != nil {
			return output{}, err
		}
	}
	buckets := metrics.Thinking(rows, metrics.Grouping{By: *by, SplitAt: split},
		metrics.ThinkingFilter{Model: *model, Effort: *effort, Agent: *agent})
	var trends []metrics.Trend
	if *by == "day" {
		trends = metrics.Trends(buckets)
	}
	notes := thinkingNotes(buckets)
	data := map[string]any{"source": source, "since": since, "until": until, "days": *days, "by": *by,
		"split_at": split, "model": *model, "effort": *effort, "agent": *agent, "buckets": buckets,
		"trends": trends, "notes": notes, "warnings": warnings}
	if reach != nil {
		data["coverage"] = reach
	}
	return output{Data: data, Text: func(w io.Writer) {
		span := fmt.Sprintf("since %s (%d days)", since, *days)
		if *fromFlag != "" || until != "" {
			span = fmt.Sprintf("from %s to %s", since, firstOf(until, "now"))
		}
		if split != "" {
			span += ", split at " + split
		}
		fmt.Fprintf(w, "thinking %s from the %s, by %s\n", span, source, *by)
		tw := tabwriter.NewWriter(w, 0, 0, 2, ' ', tabwriter.AlignRight)
		fmt.Fprintln(tw, *by+"\tresponses\tthought\ttokens p50\ttokens mean\tthink s p50\tthink n\twait s p50\twait n\t")
		for _, b := range buckets {
			fmt.Fprintf(tw, "%s\t%d\t%d\t%.0f\t%.0f\t%.1f\t%d\t%.1f\t%d\t\n", b.Key, b.Responses, b.Thought,
				b.Tokens.P50, b.Tokens.Mean, b.Seconds.P50, b.Seconds.N, b.WaitSeconds.P50, b.WaitSeconds.N)
		}
		tw.Flush()
		for _, t := range trends {
			if t.SlopePctPerDay == nil {
				fmt.Fprintf(w, "trend %s: %s (%d days)\n", t.Metric, t.Note, t.Days)
				continue
			}
			fmt.Fprintf(w, "trend %s: %+.1f%% per day over %d days (%.1f -> %.1f)\n",
				t.Metric, *t.SlopePctPerDay, t.Days, *t.First, *t.Last)
		}
		for _, n := range notes {
			fmt.Fprintln(w, "note: "+n)
		}
	}}, nil
}

// coverage is how far back the transcripts on disk answer.
type coverage struct {
	// Every day from this one on is whole on disk.
	CompleteFrom string `json:"complete_from"`
	// The earliest line any transcript holds; days before CompleteFrom keep
	// only the sessions still written to after it.
	OldestLine string `json:"oldest_line,omitempty"`
}

// rawRows reads the transcripts, saying first what that costs and how far back
// it can reach.
func rawRows(projects string, from time.Time, since, until string) ([]metrics.ThinkingRow, *coverage, []string, error) {
	if st, err := os.Stat(projects); err != nil || !st.IsDir() {
		return nil, nil, nil, refuse("projects directory %s not found", projects)
	}
	files, err := ingest.List(projects)
	if err != nil {
		return nil, nil, nil, err
	}
	var size int64
	reading := 0
	for _, f := range files {
		if f.MtimeNS >= from.UnixNano() {
			size += f.Size
			reading++
		}
	}
	var warnings []string
	warnings = append(warnings, warn("reading %d transcripts (%.0f MB) under %s directly. This is slow: "+
		"every run reads them all again, while the ledger answers the same question at once (drop --raw).",
		reading, float64(size)/(1<<20), projects))
	// Claude Code deletes a transcript once it has gone unwritten for
	// cleanupPeriodDays, so every day since then is whole on disk; an older
	// day keeps only the sessions still written to after it.
	days, setting := cleanupPeriod()
	complete := time.Now().Local().AddDate(0, 0, -days).Format(time.DateOnly)
	oldest := ingest.Oldest(files)
	if len(oldest) >= 10 {
		oldest = oldest[:10]
	}
	if oldest > complete {
		complete = oldest
	}
	msg := fmt.Sprintf("a direct read can only answer from %s on: Claude Code deletes a transcript "+
		"once it has not been written to for cleanupPeriodDays (%s).", complete, setting)
	if oldest != "" && oldest < complete {
		msg += fmt.Sprintf(" Older lines reach back to %s, but only from sessions still written to "+
			"after %s, so those days are incomplete.", oldest, complete)
	}
	if since < complete {
		msg += fmt.Sprintf(" The range starts at %s: the days before %s are missing from this answer "+
			"or incomplete.", since, complete)
	}
	warnings = append(warnings, warn("%s", msg))
	t0 := time.Now()
	resps, err := ingest.ReadRaw(files, from, since)
	if err != nil {
		return nil, nil, nil, err
	}
	fmt.Fprintf(warnOut, "fd thinking: read %d responses in %.1fs\n", len(resps), time.Since(t0).Seconds())
	return metrics.ThinkingRowsOf(resps, until), &coverage{CompleteFrom: complete, OldestLine: oldest}, warnings, nil
}

// stampLayout is how the transcripts write a timestamp, so a bound compares
// with them as text.
const stampLayout = "2006-01-02T15:04:05.000Z"

func utcStamp(t time.Time) string { return t.UTC().Format(stampLayout) }

// parseWhen reads a date (local midnight) or a time, with or without a zone
// (local when none is given).
func parseWhen(s string) (time.Time, error) {
	for _, layout := range []string{time.RFC3339Nano, "2006-01-02T15:04Z07:00"} {
		if t, err := time.Parse(layout, s); err == nil {
			return t, nil
		}
	}
	for _, layout := range []string{time.DateOnly, "2006-01-02T15:04:05", "2006-01-02T15:04"} {
		if t, err := time.ParseInLocation(layout, s, time.Local); err == nil {
			return t, nil
		}
	}
	return time.Time{}, refuse("%q is not a date or a time such as 2026-10-01 or 2026-10-01T16:27+07:00", s)
}

func firstOf(a, b string) string {
	if a != "" {
		return a
	}
	return b
}

// cleanupPeriod is Claude Code's cleanupPeriodDays as this machine sets it, and
// where that came from.
func cleanupPeriod() (int, string) {
	dir := os.Getenv("CLAUDE_CONFIG_DIR")
	if dir == "" {
		dir = filepath.Join(homeDir(), ".claude")
	}
	path := filepath.Join(dir, "settings.json")
	var settings struct {
		Days *int `json:"cleanupPeriodDays"`
	}
	if b, err := os.ReadFile(path); err == nil && json.Unmarshal(b, &settings) == nil &&
		settings.Days != nil && *settings.Days > 0 {
		return *settings.Days, fmt.Sprintf("%d days, set in %s", *settings.Days, path)
	}
	return 30, "30 days, the default; " + path + " does not set it"
}

// thinkingNotes says where a figure covers fewer responses than thought.
func thinkingNotes(buckets []metrics.ThinkingBucket) []string {
	var thought, measured, reported, responses int
	for _, b := range buckets {
		thought += b.Thought
		measured += b.Seconds.N
		reported += b.TokensReported
		responses += b.Responses
	}
	notes := []string{}
	if measured < thought {
		notes = append(notes, fmt.Sprintf("thinking time is measured on %d of %d responses that thought: "+
			"Claude Code writes it from 2.1.289 on; wait_seconds covers the rest but includes the wait "+
			"for the first token", measured, thought))
	}
	if reported < responses {
		notes = append(notes, fmt.Sprintf("%d of %d responses report no thinking tokens: subagent "+
			"transcripts mostly do not carry them, nor does a row whose transcript was deleted before fd "+
			"read thinking; the token figures are mostly the sessions' own (see --by agent)",
			responses-reported, responses))
	}
	return notes
}

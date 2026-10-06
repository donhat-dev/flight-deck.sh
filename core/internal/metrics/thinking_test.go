package metrics

import (
	"fmt"
	"math"
	"testing"
)

func n(v int64) *int64 { return &v }

// Five responses a day for four days; the median thinking time doubles from
// the first day to the last, one second a day.
func trendRows() []ThinkingRow {
	var rows []ThinkingRow
	for d := 0; d < 4; d++ {
		for i := 0; i < MinTrendSamples; i++ {
			rows = append(rows, ThinkingRow{Day: fmt.Sprintf("2026-10-0%d", d+3), Model: "claude-opus-5-5",
				Effort: "high", Tokens: n(int64(100 * (d + 1))), MS: n(int64(1000 * (d + 2))), WaitMS: n(4000)})
		}
	}
	// A response that did not think, one from an old CLI, and a day too thin to count.
	rows = append(rows, ThinkingRow{Day: "2026-10-03", Model: "claude-opus-5-5", Tokens: n(0)},
		ThinkingRow{Day: "2026-10-03", Model: "claude-sonnet-5-5"},
		ThinkingRow{Day: "2026-10-07", Model: "claude-opus-5-5", Tokens: n(9999), MS: n(99000)})
	return rows
}

func TestThinkingByDayCountsOnlyWhatEachResponseReports(t *testing.T) {
	days := Thinking(trendRows(), "day", ThinkingFilter{})
	if len(days) != 5 || days[0].Key != "2026-10-03" {
		t.Fatalf("%+v", days)
	}
	first := days[0]
	if first.Responses != 7 || first.TokensReported != 6 || first.Thought != 5 ||
		first.Tokens.P50 != 100 || first.Seconds.P50 != 2 || first.Seconds.N != 5 {
		t.Fatalf("%+v", first)
	}
}

func TestTheTrendIsTheSlopeOfTheDailyMedians(t *testing.T) {
	trends := Trends(Thinking(trendRows(), "day", ThinkingFilter{}))
	byName := map[string]Trend{}
	for _, tr := range trends {
		byName[tr.Metric] = tr
	}
	sec := byName["thinking_seconds_p50"]
	// Medians 2, 3, 4, 5 s: +1 s a day over a mean of 3.5 s. The thin last day is left out.
	if sec.Days != 4 || sec.SlopePctPerDay == nil || math.Abs(*sec.SlopePctPerDay-100.0/3.5) > 1e-9 ||
		*sec.First != 2 || *sec.Last != 5 {
		t.Fatalf("%+v", sec)
	}
	if wait := byName["wait_seconds_p50"]; wait.SlopePctPerDay == nil || *wait.SlopePctPerDay != 0 {
		t.Fatalf("%+v", wait)
	}
}

func TestATrendNeedsThreeDays(t *testing.T) {
	rows := trendRows()[:2*MinTrendSamples]
	for _, tr := range Trends(Thinking(rows, "day", ThinkingFilter{})) {
		if tr.SlopePctPerDay != nil || tr.Note == "" {
			t.Fatalf("%+v", tr)
		}
	}
}

func TestThinkingFiltersByModelAndEffort(t *testing.T) {
	got := Thinking(trendRows(), "model", ThinkingFilter{Effort: "high"})
	if len(got) != 1 || got[0].Key != "claude-opus-5-5" || got[0].Responses != 4*MinTrendSamples {
		t.Fatalf("%+v", got)
	}
}

func TestThinkingSplitsTheSessionsFromTheirSubagents(t *testing.T) {
	rows := []ThinkingRow{{Day: "2026-10-06", Agent: "main", WaitMS: n(3000)},
		{Day: "2026-10-06", Agent: "subagent", WaitMS: n(30000)},
		{Day: "2026-10-06", Agent: "subagent", WaitMS: n(40000)}}
	by := Thinking(rows, "agent", ThinkingFilter{})
	if len(by) != 2 || by[0].Key != "subagent" || by[0].WaitSeconds.N != 2 {
		t.Fatalf("%+v", by)
	}
	main := Thinking(rows, "day", ThinkingFilter{Agent: "main"})
	if len(main) != 1 || main[0].WaitSeconds.P50 != 3 {
		t.Fatalf("%+v", main)
	}
}

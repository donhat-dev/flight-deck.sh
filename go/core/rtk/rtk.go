// Package rtk measures the token savings the `rtk` CLI reports, the same way
// python/flightdeck/rtk.py does: run `rtk gain`, parse its counters, price the
// saved tokens at a fixed rate.
package rtk

import (
	"context"
	"math"
	"os/exec"
	"regexp"
	"strconv"
	"strings"
	"time"
)

// Timeout caps one `rtk gain` run, as Python's subprocess timeout does. Tests
// shorten it to exercise a hanging binary.
var Timeout = 10 * time.Second

// ratePerMtok is Python's hardcoded default (rtk.py:28): 5 USD per million
// saved tokens. There is no config key on either side.
const ratePerMtok = 5.0

var (
	tokensSaved = regexp.MustCompile(`Tokens saved:\s*([\d.,]+[KM]?)`)
	totalCmds   = regexp.MustCompile(`Total commands:\s*([\d.,]+[KM]?)`)
)

// Gain is the parsed `rtk gain` summary; counters the output lacks stay 0.
type Gain struct {
	TokensSaved int
	Commands    int
}

// ParseGain reads the counters out of `rtk gain` output.
func ParseGain(text string) Gain {
	var g Gain
	if m := tokensSaved.FindStringSubmatch(text); m != nil {
		g.TokensSaved = num(m[1])
	}
	if m := totalCmds.FindStringSubmatch(text); m != nil {
		g.Commands = num(m[1])
	}
	return g
}

// num reads "1,234", "12K", "1.5M" the way rtk.py's _num does: strip commas,
// expand a K/M suffix, round to the nearest integer (Python's round is
// half-to-even). An unparseable number counts as 0 — Python's float() raises
// there, and the caller treats the whole run as unreadable.
func num(s string) int {
	s = strings.ReplaceAll(strings.TrimSpace(s), ",", "")
	mult := 1.0
	if strings.HasSuffix(s, "K") {
		mult, s = 1e3, s[:len(s)-1]
	} else if strings.HasSuffix(s, "M") {
		mult, s = 1e6, s[:len(s)-1]
	}
	f, err := strconv.ParseFloat(s, 64)
	if err != nil {
		return 0
	}
	return int(math.RoundToEven(f * mult))
}

// SavingsUSD runs `rtk gain` and converts the saved-token count to dollars.
// It returns 0 when rtk is not on PATH, exits non-zero, times out, or prints
// no "Tokens saved" counter — the same fallbacks as rtk_savings_usd().
func SavingsUSD() float64 {
	path, err := exec.LookPath("rtk")
	if err != nil {
		return 0
	}
	ctx, cancel := context.WithTimeout(context.Background(), Timeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, path, "gain")
	// A wrapper's child can inherit stdout and keep it open past the timeout;
	// kill the whole group and stop waiting on the pipe shortly after.
	ownGroup(cmd)
	cmd.WaitDelay = time.Second
	out, err := cmd.Output()
	if err != nil {
		return 0
	}
	return float64(ParseGain(string(out)).TokensSaved) * ratePerMtok / 1e6
}

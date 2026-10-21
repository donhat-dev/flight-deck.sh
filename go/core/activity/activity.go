// Package activity is the Go port of python/flightdeck/activity.py: turns and
// agent busy time, read from the same transcript pass as usage.
//
// A turn starts at a prompt a person wrote and runs until the agent's final
// end_turn. Busy time is the sum of gaps between consecutive records inside an
// open turn, each gap capped (a long gap is someone away, not the agent
// working), minus the wait for a person to answer AskUserQuestion. A subagent
// file counts on its own. The tracker's state is persisted on the files row
// (the act_* columns) in the same transaction as the cursor, so incremental
// reads resume mid-file and continue an open turn.
package activity

import (
	"database/sql"
	"encoding/json"
	"errors"
	"os"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"
)

const (
	defaultIdleCapMinutes = 15.0
	askTool               = "AskUserQuestion"
	promptHeadChars       = 2000
	finalHeadChars        = 1000
)

// A user line that is machinery, not a person: tags the CLI injects (commands,
// reminders, task notifications, editor state), the interrupt marker, the image
// trailer and the caveat block.
var machinePrefixes = []string{"<", "[Request interrupted", "[Image:", "Caveat:",
	"This session is being continued from a previous conversation"}

// Some resumes re-append an earlier stretch of the session to the same file,
// with the original uuids and timestamps. A line this much older than the
// newest one read is such a replay.
const replaySlack = 120 * time.Second

var agentFile = regexp.MustCompile(`agent-([^/]+)\.jsonl$`)

// The VS Code extension puts the open file / selection in front of the prompt.
// RE2 has no backreferences, so the closing tag is found by hand.
var ideOpen = regexp.MustCompile(`^\s*<(ide_[a-z_]+)>`)

// IdleCapSeconds is the idle-gap cap: TOKEN_AUDIT_AGENT_IDLE_CAP_MIN, else 15
// minutes. Unlike Python there is no config-file fallback.
func IdleCapSeconds() float64 {
	raw := strings.TrimSpace(os.Getenv("TOKEN_AUDIT_AGENT_IDLE_CAP_MIN"))
	minutes := defaultIdleCapMinutes
	if raw != "" {
		if v, err := strconv.ParseFloat(raw, 64); err == nil {
			minutes = v
		}
	}
	if minutes < 0 {
		minutes = 0
	}
	return minutes * 60
}

// Line is what the tracker reads of one decoded transcript record. Message is
// kept raw: its shape differs by line type and the tracker decodes only what
// each rule needs.
type Line struct {
	Type, SessionID, Timestamp string
	PromptID, UUID             string
	TurnOrigin                 json.RawMessage
	CWD, GitBranch             string
	IsMeta, IsSidechain        bool
	Message                    json.RawMessage
}

func parseTS(s string) (time.Time, bool) {
	if s == "" {
		return time.Time{}, false
	}
	t, err := time.Parse(time.RFC3339Nano, s)
	if err != nil {
		return time.Time{}, false
	}
	return t, true
}

type contentBlock struct {
	Type string `json:"type"`
	Text string `json:"text"`
	Name string `json:"name"`
}

// content decodes message.content: a plain string, or the block list.
func content(msg json.RawMessage) (text string, blocks []contentBlock, isText bool) {
	var m struct {
		Content json.RawMessage `json:"content"`
	}
	if json.Unmarshal(msg, &m) != nil || len(m.Content) == 0 {
		return "", nil, false
	}
	if json.Unmarshal(m.Content, &text) == nil {
		return text, nil, true
	}
	if json.Unmarshal(m.Content, &blocks) == nil {
		return "", blocks, false
	}
	return "", nil, false
}

// userText is the text a user line carries; "" when it is a tool result.
func userText(l Line) string {
	text, blocks, isText := content(l.Message)
	if isText {
		return text
	}
	var parts []string
	for _, b := range blocks {
		if b.Type == "tool_result" {
			return ""
		}
		if b.Type == "text" {
			parts = append(parts, b.Text)
		}
	}
	return strings.Join(parts, "\n")
}

func assistantText(l Line) string {
	text, blocks, isText := content(l.Message)
	if isText {
		return text
	}
	var parts []string
	for _, b := range blocks {
		if b.Type == "text" {
			parts = append(parts, b.Text)
		}
	}
	return strings.Join(parts, "\n")
}

func asksAPerson(l Line) bool {
	_, blocks, isText := content(l.Message)
	if isText {
		return false
	}
	for _, b := range blocks {
		if b.Type == "tool_use" && b.Name == askTool {
			return true
		}
	}
	return false
}

func stopReason(l Line) string {
	var m struct {
		StopReason string `json:"stop_reason"`
	}
	json.Unmarshal(l.Message, &m)
	return m.StopReason
}

// hasUsage is the Python `if usage` truthiness on message.usage.
func hasUsage(l Line) bool {
	var m struct {
		Usage json.RawMessage `json:"usage"`
	}
	if json.Unmarshal(l.Message, &m) != nil || len(m.Usage) == 0 {
		return false
	}
	var v any
	if json.Unmarshal(m.Usage, &v) != nil {
		return false
	}
	switch u := v.(type) {
	case nil:
		return false
	case bool:
		return u
	case float64:
		return u != 0
	case string:
		return u != ""
	case map[string]any:
		return len(u) > 0
	case []any:
		return len(u) > 0
	}
	return true
}

// PromptBody is the prompt without the IDE context blocks in front of it.
func PromptBody(text string) string {
	for {
		m := ideOpen.FindStringSubmatch(text)
		if m == nil {
			return text
		}
		rest := text[len(m[0]):]
		i := strings.Index(rest, "</"+m[1]+">")
		if i < 0 {
			return text
		}
		text = rest[i+len(m[1])+3:]
	}
}

// IsHumanPrompt is true for a line a person typed (or picked) as a new prompt.
// Newer CLI versions say so in turnOrigin; older lines are judged by text.
func IsHumanPrompt(l Line) bool {
	if l.Type != "user" || l.IsMeta || l.IsSidechain {
		return false
	}
	if decided, human := byOrigin(l.TurnOrigin); decided {
		return human
	}
	text := strings.TrimSpace(PromptBody(userText(l)))
	if text == "" {
		return false
	}
	for _, p := range machinePrefixes {
		if strings.HasPrefix(text, p) {
			return false
		}
	}
	return true
}

// byOrigin applies `if origin: return origin == "human"`: a falsy JSON value
// (absent, null, false, 0, "", [], {}) leaves the prompt to be judged by text.
func byOrigin(raw json.RawMessage) (decided, human bool) {
	if len(raw) == 0 {
		return false, false
	}
	var v any
	if json.Unmarshal(raw, &v) != nil {
		return true, false
	}
	switch o := v.(type) {
	case nil:
		return false, false
	case bool:
		return o, false
	case string:
		return o != "", o == "human"
	case float64:
		return o != 0, false
	case map[string]any:
		return len(o) > 0, false
	case []any:
		return len(o) > 0, false
	}
	return true, false
}

func head(s string, n int) string {
	if utf8.RuneCountInString(s) > n {
		return string([]rune(s)[:n])
	}
	return s
}

func nullStr(s string) any {
	if s == "" {
		return nil
	}
	return s
}

// State is the tracker state persisted on the files row.
type State struct {
	LastTS            string // "" when there is none
	InTurn, WaitHuman bool
	PromptID          string
	Answered          bool
}

// Tracker is the turn and busy-time state for one jsonl file during one ingest
// pass. All its writes go through the transaction ingest holds for the file.
type Tracker struct {
	tx           *sql.Tx
	cap          float64
	sub          bool
	agentID      string
	lastTS       time.Time
	hasTS        bool
	inTurn       bool
	waitHuman    bool
	answered     bool // the agent has replied in the open turn; until it has, a further prompt joins it
	promptID     string
	copyMode     bool
	sessionID    string
	resetPending bool
	busy         map[[2]string]float64 // (prompt id, day) -> seconds
}

// NewTracker resumes from the saved cursor state; resumed is false for a read
// from offset 0, which recounts the whole file and drops its earlier
// contribution once the session id is known.
func NewTracker(tx *sql.Tx, path string, st State, resumed bool, capSeconds float64) *Tracker {
	t := &Tracker{tx: tx, cap: capSeconds, busy: map[[2]string]float64{},
		resetPending: !resumed, answered: true}
	m := agentFile.FindStringSubmatch(path)
	t.sub = strings.Contains(path, "/subagents/") && m != nil
	if t.sub {
		t.agentID = m[1]
	}
	if resumed {
		t.lastTS, t.hasTS = parseTS(st.LastTS)
		t.inTurn, t.waitHuman = st.InTurn, st.WaitHuman
		t.promptID = st.PromptID
		t.answered = st.Answered
	}
	return t
}

// AgentID is the subagent id taken from the file's name, "" for a main file.
func (t *Tracker) AgentID() string { return t.agentID }

// Stamp is the turn attribution for a billed row.
func (t *Tracker) Stamp() (promptID, agentID string) { return t.promptID, t.agentID }

// IsReplay is true for a line re-appended from earlier in the same file.
func (t *Tracker) IsReplay(ts string) bool {
	at, ok := parseTS(ts)
	return ok && t.hasTS && at.Before(t.lastTS.Add(-replaySlack))
}

// Feed advances the tracker on one line. copied is true when the line's billed
// row already belongs to another session (a resume/fork copy).
func (t *Tracker) Feed(l Line, copied bool) error {
	kind := l.Type
	if kind != "user" && kind != "assistant" && kind != "system" {
		return nil
	}
	ts, ok := parseTS(l.Timestamp)
	if !ok || (t.hasTS && ts.Before(t.lastTS.Add(-replaySlack))) {
		return nil // a replay was counted, and its turn recorded, when first read
	}
	if t.sessionID == "" && l.SessionID != "" {
		t.sessionID = l.SessionID
		if t.resetPending {
			if _, err := t.tx.Exec(`DELETE FROM agent_activity WHERE session_id=? AND agent_id=?
				AND method='transcript'`, t.sessionID, t.agentID); err != nil {
				return err
			}
			t.resetPending = false
		}
	}

	if t.sub {
		if t.promptID == "" && l.PromptID != "" {
			t.promptID = l.PromptID
		}
		t.inTurn = true
	} else if IsHumanPrompt(l) {
		pid := l.PromptID
		if pid == "" {
			pid = l.UUID
		}
		if t.inTurn && t.promptID != "" && (pid == t.promptID || !t.answered) {
			if err := t.fold(l, pid); err != nil {
				return err
			}
		} else {
			t.promptID = pid
			isCopy, err := t.openTurn(l)
			if err != nil {
				return err
			}
			t.copyMode = isCopy
			t.inTurn, t.waitHuman, t.lastTS, t.hasTS = true, false, ts, true
			t.answered = false
			return nil
		}
	} else if kind == "user" && !t.inTurn && l.PromptID != "" {
		// A system-origin turn (task notification, hook) continues the current
		// task; the agent starts working again from here.
		t.inTurn, t.waitHuman, t.lastTS, t.hasTS = true, false, ts, true
		t.answered = true
		return nil
	}

	// The gap that ends a copied block spans the pause before the resume, so it
	// is skipped too.
	wasCopy := t.copyMode
	if copied {
		t.copyMode = true
	} else if kind == "assistant" && hasUsage(l) {
		t.copyMode = false
	}
	if t.inTurn && t.hasTS && ts.After(t.lastTS) && !t.waitHuman && !wasCopy && !t.copyMode {
		end := ts
		if cap := t.lastTS.Add(time.Duration(t.cap * float64(time.Second))); cap.Before(end) {
			end = cap
		}
		t.add(t.lastTS, end)
	}
	if !t.hasTS || ts.After(t.lastTS) {
		t.lastTS, t.hasTS = ts, true
	}

	if kind == "assistant" {
		t.answered = true
		t.waitHuman = asksAPerson(l)
		if !t.sub && stopReason(l) == "end_turn" && t.inTurn && !t.copyMode {
			t.inTurn = false
			if err := t.closeTurn(t.promptID, l.Timestamp, assistantText(l)); err != nil {
				return err
			}
		}
	} else if kind == "user" {
		t.waitHuman = false
	}
	return nil
}

// add splits the busy stretch at midnight in the timestamp's own zone (the
// dashboard's days are UTC dates and the transcripts carry Z).
func (t *Tracker) add(start, end time.Time) {
	for start.Before(end) {
		y, m, d := start.Date()
		midnight := time.Date(y, m, d+1, 0, 0, 0, 0, start.Location())
		stop := end
		if midnight.Before(end) {
			stop = midnight
		}
		key := [2]string{t.promptID, start.Format("2006-01-02")}
		t.busy[key] += stop.Sub(start).Seconds()
		start = stop
	}
}

// openTurn records the turn and returns true when the prompt already belongs to
// another session, i.e. it is part of a copied history.
func (t *Tracker) openTurn(l Line) (bool, error) {
	var known sql.NullString
	err := t.tx.QueryRow(`SELECT session_id FROM turns WHERE turn_id=?`, t.promptID).Scan(&known)
	if err != nil && !errors.Is(err, sql.ErrNoRows) {
		return false, err
	}
	text := strings.TrimSpace(PromptBody(userText(l)))
	if err == nil {
		if known.String != l.SessionID {
			return true, nil
		}
		// A re-read of the owning session refreshes what the prompt rule reads.
		_, err := t.tx.Exec(`UPDATE turns SET prompt_head=?, cwd=?, git_branch=? WHERE turn_id=?`,
			head(text, promptHeadChars), nullStr(l.CWD), nullStr(l.GitBranch), t.promptID)
		return false, err
	}
	_, err = t.tx.Exec(`INSERT INTO turns (turn_id, session_id, started_at, prompt_head, cwd,
		git_branch) VALUES (?,?,?,?,?,?) ON CONFLICT(turn_id) DO NOTHING`,
		t.promptID, nullStr(l.SessionID), nullStr(l.Timestamp), head(text, promptHeadChars),
		nullStr(l.CWD), nullStr(l.GitBranch))
	return false, err
}

// fold adds a prompt to the open turn.
func (t *Tracker) fold(l Line, pid string) error {
	if t.copyMode {
		return nil
	}
	if pid != t.promptID {
		// An older ingest may have stored the joined prompt as its own turn.
		if _, err := t.tx.Exec(`DELETE FROM turns WHERE turn_id=? AND session_id=?`,
			pid, nullStr(l.SessionID)); err != nil {
			return err
		}
	}
	text := strings.TrimSpace(PromptBody(userText(l)))
	if text == "" {
		return nil
	}
	_, err := t.tx.Exec(`UPDATE turns SET prompt_head=substr(COALESCE(prompt_head, '') || ?, 1, ?)
		WHERE turn_id=?`, "\n\n"+text, promptHeadChars, t.promptID)
	return err
}

func (t *Tracker) closeTurn(turnID, ts, text string) error {
	if turnID == "" {
		return nil
	}
	_, err := t.tx.Exec(`UPDATE turns SET ended_at=?, final_head=? WHERE turn_id=?
		AND (ended_at IS NULL OR ended_at < ?)`,
		nullStr(ts), nullStr(head(strings.TrimSpace(text), finalHeadChars)), turnID, nullStr(ts))
	return err
}

// Flush writes the accumulated busy time; the tracker keeps running.
func (t *Tracker) Flush() error {
	if t.sessionID != "" {
		for key, sec := range t.busy {
			if sec <= 0 {
				continue
			}
			if _, err := t.tx.Exec(`INSERT INTO agent_activity (session_id, agent_id, prompt_id, day,
				method, busy_seconds) VALUES (?,?,?,?,'transcript',?)
				ON CONFLICT (session_id, agent_id, prompt_id, day, method)
				DO UPDATE SET busy_seconds = agent_activity.busy_seconds + excluded.busy_seconds`,
				t.sessionID, t.agentID, key[0], key[1], sec); err != nil {
				return err
			}
		}
	}
	t.busy = map[[2]string]float64{}
	return nil
}

// State is what the cursor row persists for the next incremental read.
func (t *Tracker) State() State {
	var last string
	if t.hasTS {
		last = t.lastTS.Format(time.RFC3339Nano)
	}
	return State{LastTS: last, InTurn: t.inTurn, WaitHuman: t.waitHuman,
		PromptID: t.promptID, Answered: t.answered}
}

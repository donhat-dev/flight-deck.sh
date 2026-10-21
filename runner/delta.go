package main

import (
	"encoding/json"
	"log"
	"os"
	"path/filepath"
	"sync"
	"time"
)

// The CLI, run with --include-partial-messages, narrates one assistant
// message as a sequence of stream_event frames: message_start, then per
// content block content_block_start/content_block_delta*/content_block_stop,
// then message_delta/message_stop. A block's delta.type is one of
// text_delta, thinking_delta, signature_delta or input_json_delta; only
// text_delta is user-visible prose (measured: a three-word reply produced 29
// frames, of which exactly one was a text delta — signature_delta alone ran
// to ~900 bytes of base64). The rest are dropped here, not forwarded.
//
// A block's index is scoped to the message and reused across it (thinking
// may be index 0, then text index 1; the next message starts again at 0), so
// index cannot key a stream by itself. What actually delimits one block is
// the plain "assistant" frame the CLI also emits once per completed block,
// carrying that block's full, authoritative content — and it arrives before
// the block's own content_block_stop. That frame is therefore the signal
// used below to flush and forget a block's accumulated text, not
// content_block_stop or message_stop (message_stop only backstops it).
//
// Snapshots, not increments: every emitted line carries the FULL text
// accumulated so far for its stream, so a client that missed a line (or
// reconnected) is caught up by the next one. This is what makes deltas.jsonl
// safe to leave unread across a restart, unlike events.jsonl.

// window is how often one stream may flush while it keeps receiving text. A
// provider notification lands per token; publishing each one would wake
// every subscriber per token, so the coalescer instead snapshots at most
// once per window as long as new text is pending.
const deltaWindow = 60 * time.Millisecond

// maxStreamBytes bounds one stream's accumulated text. An ordinary assistant
// answer runs to a few KB; this is generous headroom for a long one while
// still bounding memory and the size of one deltas.jsonl line against a
// runaway response.
const maxStreamBytes = 256 * 1024

// maxStreamsPerSession bounds concurrently tracked streams within one
// claude process. The main turn is one stream; concurrent subagents (Task
// tool calls the CLI itself limits to a handful at a time) are the only
// other source of concurrency here, so this is headroom over that, not a
// realistic ceiling.
const maxStreamsPerSession = 16

// maxDeltaFileBytes bounds deltas.jsonl itself. The file is never replayed —
// Odoo keeps no durable offset on it, only an in-memory cursor the watcher
// resets to 0 when the file shrinks (the same rule connectors/spool.py's
// read_events already applies to a rewritten events.jsonl) — so truncating
// it back to empty once it grows past this loses only superseded
// snapshots, never a line a client still needs: every live stream writes
// its next snapshot again within one window regardless.
const maxDeltaFileBytes = 4 * 1024 * 1024

// streamKey identifies one accumulating text block. sessionID is the
// stream_event frame's own "session_id" (the CLI session actually producing
// the text: the main turn, or a subagent's own transcript session), which
// differs from the Go-tracked session id for a subagent. parent is the
// frame's "parent_tool_use_id", carried too in case a future CLI version
// reuses a session id across parents.
type streamKey struct {
	sessionID string
	parent    string
}

type deltaStream struct {
	buf       []byte
	truncated bool
	dirty     bool
	seq       int64
	timer     *time.Timer
}

// deltaLine is one line of deltas.jsonl.
type deltaLine struct {
	TS        string `json:"ts"`
	SessionID string `json:"session_id"`
	Stream    string `json:"stream"`
	Seq       int64  `json:"seq"`
	Text      string `json:"text"`
}

// streamEventBody is the part of a stream_event frame's nested "event"
// object the coalescer acts on.
type streamEventBody struct {
	Type  string `json:"type"`
	Index int    `json:"index"`
	Delta *struct {
		Type string `json:"type"`
		Text string `json:"text"`
	} `json:"delta"`
}

// DeltaWriter coalesces stream_event text deltas per Go-tracked session and
// appends the snapshots to <spool>/deltas.jsonl.
type DeltaWriter struct {
	sp *Spool

	mu sync.Mutex
	// buckets holds every tracked stream, grouped by the Go-tracked session
	// (what FlushSession/DropSession act on) so a turn's lifecycle events can
	// flush or drop everything belonging to it without knowing every
	// frame-level session id in play.
	buckets map[string]map[streamKey]*deltaStream

	fileMu sync.Mutex
}

func NewDeltaWriter(sp *Spool) *DeltaWriter {
	return &DeltaWriter{sp: sp, buckets: map[string]map[streamKey]*deltaStream{}}
}

// OnAssistant handles a top-level "assistant" frame: the authoritative close
// of whatever block the matching stream was accumulating. A no-op when
// nothing is tracked under that key, which is the common case (thinking and
// tool-use blocks never open a stream here in the first place).
func (dw *DeltaWriter) OnAssistant(goSessionID, frameSessionID, parentToolUseID string) {
	dw.closeStream(goSessionID, streamKey{sessionID: frameSessionID, parent: parentToolUseID})
}

// OnStreamEvent handles one stream_event frame's nested event.
func (dw *DeltaWriter) OnStreamEvent(goSessionID, frameSessionID, parentToolUseID string, ev *streamEventBody) {
	if ev == nil {
		return
	}
	key := streamKey{sessionID: frameSessionID, parent: parentToolUseID}
	switch ev.Type {
	case "message_start":
		// A defensive reset: a message_start with something still tracked
		// under this key means an earlier block's "assistant" frame was
		// missed, and its stale text must not bleed into the new message.
		dw.closeStream(goSessionID, key)
	case "content_block_delta":
		if ev.Delta == nil || ev.Delta.Type != "text_delta" || ev.Delta.Text == "" {
			return
		}
		dw.append(goSessionID, key, ev.Delta.Text)
	case "message_stop":
		// Backstops OnAssistant in case a block's own "assistant" frame was
		// somehow lost; ordinarily this finds nothing left to close.
		dw.closeStream(goSessionID, key)
	}
}

func (dw *DeltaWriter) append(goSessionID string, key streamKey, text string) {
	dw.mu.Lock()
	bucket := dw.buckets[goSessionID]
	if bucket == nil {
		bucket = map[streamKey]*deltaStream{}
		dw.buckets[goSessionID] = bucket
	}
	st := bucket[key]
	if st == nil {
		if len(bucket) >= maxStreamsPerSession {
			dw.mu.Unlock()
			log.Printf("%s: dropping a text stream, %d already tracked",
				goSessionID[:min(8, len(goSessionID))], len(bucket))
			return
		}
		st = &deltaStream{}
		bucket[key] = st
	}
	if !st.truncated {
		room := maxStreamBytes - len(st.buf)
		if room <= 0 {
			st.truncated = true
			st.buf = append(st.buf, []byte("\n\n[truncated: this draft passed the size limit]")...)
			st.dirty = true
		} else {
			if len(text) > room {
				text = text[:room]
				st.truncated = true
			}
			st.buf = append(st.buf, text...)
			st.dirty = true
		}
	}
	if st.timer == nil {
		st.timer = time.AfterFunc(deltaWindow, func() { dw.tick(goSessionID, key) })
	}
	dw.mu.Unlock()
}

// tick is the trailing-edge flush: it fires every deltaWindow while a stream
// keeps receiving text, and lets its own timer lapse once nothing new has
// arrived since the last flush.
func (dw *DeltaWriter) tick(goSessionID string, key streamKey) {
	dw.mu.Lock()
	bucket := dw.buckets[goSessionID]
	st := bucket[key]
	if st == nil {
		dw.mu.Unlock()
		return
	}
	if !st.dirty {
		st.timer = nil
		dw.mu.Unlock()
		return
	}
	line := dw.lineFor(goSessionID, key, st)
	st.dirty = false
	st.timer = time.AfterFunc(deltaWindow, func() { dw.tick(goSessionID, key) })
	dw.mu.Unlock()
	dw.writeLine(line)
}

// closeStream flushes a stream's final text, if any is pending, and forgets
// it — it belongs to a block that just ended.
func (dw *DeltaWriter) closeStream(goSessionID string, key streamKey) {
	dw.mu.Lock()
	bucket := dw.buckets[goSessionID]
	st := bucket[key]
	if st == nil {
		dw.mu.Unlock()
		return
	}
	var line *deltaLine
	if st.dirty {
		l := dw.lineFor(goSessionID, key, st)
		line = &l
	}
	if st.timer != nil {
		st.timer.Stop()
	}
	delete(bucket, key)
	dw.mu.Unlock()
	if line != nil {
		dw.writeLine(*line)
	}
}

// FlushSession writes the current snapshot of every stream tracked for a
// session, without forgetting them. Called right before a state or
// permission event is emitted, so nothing durable is published ahead of the
// text that led up to it.
func (dw *DeltaWriter) FlushSession(goSessionID string) {
	dw.mu.Lock()
	bucket := dw.buckets[goSessionID]
	var lines []deltaLine
	for key, st := range bucket {
		if !st.dirty {
			continue
		}
		lines = append(lines, dw.lineFor(goSessionID, key, st))
		st.dirty = false
	}
	dw.mu.Unlock()
	for _, l := range lines {
		dw.writeLine(l)
	}
}

// DropSession flushes and forgets every stream tracked for a session. Called
// when its turn ends (the result event) and when the process exits, so nothing
// leaks past either boundary.
func (dw *DeltaWriter) DropSession(goSessionID string) {
	dw.mu.Lock()
	bucket := dw.buckets[goSessionID]
	delete(dw.buckets, goSessionID)
	var lines []deltaLine
	for key, st := range bucket {
		if st.timer != nil {
			st.timer.Stop()
		}
		if st.dirty {
			lines = append(lines, dw.lineFor(goSessionID, key, st))
		}
	}
	dw.mu.Unlock()
	for _, l := range lines {
		dw.writeLine(l)
	}
}

// lineFor must be called with dw.mu held.
func (dw *DeltaWriter) lineFor(goSessionID string, key streamKey, st *deltaStream) deltaLine {
	st.seq++
	stream := key.sessionID
	if key.parent != "" {
		stream += ":" + key.parent
	}
	return deltaLine{
		TS:        time.Now().UTC().Format(time.RFC3339Nano),
		SessionID: goSessionID,
		Stream:    stream,
		Seq:       st.seq,
		Text:      string(st.buf),
	}
}

func (dw *DeltaWriter) writeLine(line deltaLine) {
	raw, err := json.Marshal(line)
	if err != nil {
		return
	}
	raw = append(raw, '\n')
	dw.fileMu.Lock()
	defer dw.fileMu.Unlock()
	path := filepath.Join(dw.sp.root, "deltas.jsonl")
	f, err := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o664)
	if err != nil {
		log.Printf("deltas.jsonl: %v", err)
		return
	}
	_, werr := f.Write(raw)
	var size int64
	if info, statErr := f.Stat(); statErr == nil {
		size = info.Size()
	}
	f.Close()
	if werr != nil {
		log.Printf("deltas.jsonl: %v", werr)
		return
	}
	if size > maxDeltaFileBytes {
		if err := os.Truncate(path, 0); err != nil {
			log.Printf("deltas.jsonl: truncate: %v", err)
		}
	}
}

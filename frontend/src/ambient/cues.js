/**
 * Ambient cue layer — short synthesized sounds plus browser Notifications for
 * the handful of event kinds a human should notice without having the tab
 * focused.
 *
 * CLAUDE.md describes a `uisfx/` sound-effect monorepo that FlightDeck
 * supposedly consumes. Neither is true: `uisfx/` does not exist on this
 * machine and `frontend/src` has zero audio references today. So there is no
 * asset pipeline to plug into — every cue here is built from `AudioContext`
 * primitives (oscillator + gain envelope) at call time. That also keeps the
 * page offline-safe, matching FlightDeck's local-first rule: no files to
 * fetch, nothing to go stale.
 *
 * The OS-level half of "ambient" (a `notify-send` from a shell hook) is a
 * separate track. This module owns only what runs inside the browser tab:
 * WebAudio plus the `Notification` API.
 *
 * State lives at module scope (one shared `AudioContext`, one rate-limit map,
 * one mute flag, one "have we asked for notification permission yet" flag)
 * because there is exactly one of this layer per page, the same reasoning
 * `events.BUS` uses for its own module-level singleton.
 */
import { subscribeEvents } from "../api.js";

const MUTE_KEY = "flightdeck.ambient.muted";

// One cue (and its notification) per event kind per this many ms. A guard or
// a pending-decision producer can legitimately fire several events in a
// burst (e.g. five queued approvals surfacing at once); the human should
// hear that once, not five times layered on top of each other.
const RATE_LIMIT_MS = 3000;

let audioCtx = null;
let unlocked = false;
let notificationAsked = false;
const lastFired = new Map(); // event kind -> Date.now() of the last cue/notification let through

// ---- cue synthesis --------------------------------------------------------

/** One tone with a linear attack/release so it starts and ends at zero gain.
 * Skipping the envelope makes every short burst click at both edges. */
function tone(ctx, { freq, start, duration, peak, type = "sine" }) {
  const t0 = ctx.currentTime + start;
  const attack = 0.015;
  const release = 0.04;
  const osc = ctx.createOscillator();
  const gain = ctx.createGain();
  osc.type = type;
  osc.frequency.setValueAtTime(freq, t0);
  gain.gain.setValueAtTime(0, t0);
  gain.gain.linearRampToValueAtTime(peak, t0 + attack);
  gain.gain.setValueAtTime(peak, t0 + duration - release);
  gain.gain.linearRampToValueAtTime(0, t0 + duration);
  osc.connect(gain);
  gain.connect(ctx.destination);
  osc.start(t0);
  osc.stop(t0 + duration + 0.02);
}

// Three distinct, sub-400ms cues, each recognisable without looking at the
// screen. `done` has no mapped event kind yet (see MAPPING below) — it is
// built now, per the ambient-layer plan, for whichever "a long run finished"
// kind a future track adds; wiring it is a one-line addition there.
const CUE_BUILDERS = {
  // A session is blocked on a human: the most attention-getting of the
  // three, a two-note rising interval.
  waiting: (ctx) => {
    tone(ctx, { freq: 523.25, start: 0, duration: 0.12, peak: 0.05 });    // C5
    tone(ctx, { freq: 783.99, start: 0.11, duration: 0.16, peak: 0.06 }); // G5
  },
  // A guard refused something: lower, flatter, a single note — information,
  // not an emergency, so it must read as distinct from `waiting`.
  blocked: (ctx) => {
    tone(ctx, { freq: 196, start: 0, duration: 0.22, peak: 0.045, type: "triangle" });
  },
  // A long run finished: soft, falling — nothing needs the user's attention
  // right now, so this is the quietest and easiest to ignore of the three.
  done: (ctx) => {
    const t0 = ctx.currentTime;
    const duration = 0.28;
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "sine";
    osc.frequency.setValueAtTime(660, t0);
    osc.frequency.exponentialRampToValueAtTime(392, t0 + duration);
    gain.gain.setValueAtTime(0, t0);
    gain.gain.linearRampToValueAtTime(0.04, t0 + 0.02);
    gain.gain.linearRampToValueAtTime(0, t0 + duration);
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start(t0);
    osc.stop(t0 + duration + 0.02);
  },
};

function getAudioContext() {
  if (audioCtx) return audioCtx;
  const Ctor = typeof AudioContext !== "undefined" ? AudioContext
    : typeof webkitAudioContext !== "undefined" ? webkitAudioContext : null;
  if (!Ctor) return null; // no WebAudio in this environment: cues are silently unavailable
  audioCtx = new Ctor();
  return audioCtx;
}

/** Browsers refuse to start an AudioContext before a user gesture. A single
 * one-shot listener across both pointerdown and keydown creates the shared
 * context and resumes it, then removes itself — this is also the standard
 * cross-browser pattern (create + resume inside the gesture's own call
 * stack), not just an optimisation. */
function armUnlock() {
  const unlock = () => {
    unlocked = true;
    getAudioContext()?.resume?.();
    removeEventListener("pointerdown", unlock);
    removeEventListener("keydown", unlock);
  };
  addEventListener("pointerdown", unlock);
  addEventListener("keydown", unlock);
}

function playCue(name) {
  // Dropped, not queued: if the layer queued cues while locked, the instant
  // the user finally clicks they would hear every stale sound at once — a
  // worse experience than the silence they already had by not clicking yet.
  if (!unlocked) return;
  const ctx = getAudioContext();
  if (!ctx) return;
  CUE_BUILDERS[name]?.(ctx);
}

// ---- notifications ---------------------------------------------------------

// Copy is intentionally generic: the payload shape for decision.pending /
// guard.blocked is still being built by other tracks (see
// backend/flightdeck/routers/decisions.py), so nothing here assumes a field
// that does not exist yet.
const NOTICE = {
  "decision.pending": { title: "FlightDeck", body: "A session is waiting on your decision." },
  "guard.blocked": { title: "FlightDeck", body: "A guard blocked an action." },
};

/** Ask for Notification permission on the FIRST mapped event that wants one —
 * never at page load, which is the pattern users reflexively refuse. Once
 * denied, never ask again; audio cues keep working either way. */
function notify(kind) {
  const text = NOTICE[kind];
  if (!text || typeof Notification === "undefined") return;
  if (Notification.permission === "granted") {
    new Notification(text.title, { body: text.body });
    return;
  }
  if (Notification.permission === "denied") return; // asked before, refused: stop asking
  if (notificationAsked) return; // already asked this session; do not stack requests
  notificationAsked = true;
  Notification.requestPermission().then((permission) => {
    if (permission === "granted") new Notification(text.title, { body: text.body });
  });
}

// ---- mute -------------------------------------------------------------

export function isMuted() {
  try {
    return localStorage.getItem(MUTE_KEY) === "1";
  } catch {
    return false; // a blocked store must not crash the cue layer: default unmuted
  }
}

export function setMuted(muted) {
  try {
    if (muted) localStorage.setItem(MUTE_KEY, "1");
    else localStorage.removeItem(MUTE_KEY);
  } catch {
    /* see isMuted: a blocked store is not this layer's problem to solve */
  }
}

// ---- rate limit ---------------------------------------------------------

function allowed(kind) {
  const now = Date.now();
  const prev = lastFired.get(kind) ?? -Infinity;
  if (now - prev < RATE_LIMIT_MS) return false;
  lastFired.set(kind, now);
  return true;
}

// ---- event -> cue mapping ---------------------------------------------

// A plain table so adding a kind is one line. `{ cue: null, notify: false }`
// marks a kind this layer has looked at and decided to stay silent for — kept
// as an explicit row (rather than left out) so that silence reads as intent,
// not as a kind nobody got around to mapping yet. A kind with no row at all
// is simply unknown to this layer and is ignored the same way.
const MAPPING = {
  "decision.pending": { cue: "waiting", notify: true },
  // The human just acted — they already know. No cue needed on their own move.
  "decision.resolved": { cue: null, notify: false },
  "guard.blocked": { cue: "blocked", notify: true },
  // Fires on every ingest tick (see events.py's module docstring — this is
  // the old shared `_updated` bell). Cueing it would turn this layer into a
  // metronome, so it is excluded on purpose, not an oversight.
  "summary.updated": { cue: null, notify: false },
};

function handleEvent(ev) {
  const rule = MAPPING[ev.kind];
  if (!rule) return; // unknown kind: none of this layer's business
  if (isMuted()) return; // mute silences audio AND notifications together
  if (!allowed(ev.kind)) return; // one cue (and its notification) per kind per RATE_LIMIT_MS
  if (rule.cue) playCue(rule.cue);
  if (rule.notify) notify(ev.kind);
}

/**
 * Wire the cue layer to the live event stream. Call once, from the app
 * entry point, after the appearance bootstrap. Returns the unsubscribe
 * function `subscribeEvents` hands back, so a caller that ever needs to tear
 * this down (e.g. hot reload) has a handle for it.
 */
export function init() {
  armUnlock();
  return subscribeEvents("", handleEvent);
}

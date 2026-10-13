/**
 * Ambient cue layer contract.
 *
 * This repo's test suite has neither jsdom nor happy-dom installed (see
 * TreasureConfig.test.jsx) — vitest runs these under plain Node, so
 * `window`, `AudioContext`, `Notification` and `localStorage` do not exist
 * unless stubbed. Nothing here attempts to make real sound: `AudioContext`
 * is a fake that records which nodes got created, which is enough to prove
 * a cue fired without needing an actual audio graph.
 *
 * `cues.js` keeps its state (the shared AudioContext, the unlock flag, the
 * rate-limit map, the "have we asked for permission" flag) at module scope,
 * matching its "one exported init()" surface. `vi.resetModules()` plus a
 * fresh dynamic import per test gives each test its own copy of that state,
 * while the `vi.mock("../api.js", …)` registration (hoisted, and untouched
 * by resetModules) keeps handing back a controllable `subscribeEvents`.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { subscribeEvents } from "../api.js";

vi.mock("../api.js", () => ({
  subscribeEvents: vi.fn((_prefix, onEvent) => {
    capturedHandler = onEvent;
    capturedUnsubscribe = vi.fn();
    return capturedUnsubscribe;
  }),
}));

let capturedHandler;
let capturedUnsubscribe;
let listeners;
let audioInstances;
let notifications;
let storageMap;

function stubEnvironment() {
  listeners = {};
  audioInstances = [];
  notifications = [];
  storageMap = new Map();

  vi.stubGlobal("addEventListener", vi.fn((type, cb) => {
    (listeners[type] ??= []).push(cb);
  }));
  vi.stubGlobal("removeEventListener", vi.fn((type, cb) => {
    listeners[type] = (listeners[type] || []).filter((l) => l !== cb);
  }));

  function FakeGain() {
    this.gain = { setValueAtTime: vi.fn(), linearRampToValueAtTime: vi.fn() };
  }
  FakeGain.prototype.connect = function connect() { return this; };

  function FakeOscillator() {
    this.frequency = { setValueAtTime: vi.fn(), exponentialRampToValueAtTime: vi.fn() };
    this.start = vi.fn();
    this.stop = vi.fn();
  }
  FakeOscillator.prototype.connect = function connect() { return this; };

  function FakeAudioContext() {
    this.currentTime = 0;
    this.destination = {};
    this.resume = vi.fn();
    this.createGain = vi.fn(() => new FakeGain());
    this.createOscillator = vi.fn(() => new FakeOscillator());
    audioInstances.push(this);
  }
  vi.stubGlobal("AudioContext", FakeAudioContext);

  function FakeNotification(title, opts) {
    notifications.push({ title, opts });
  }
  FakeNotification.permission = "default";
  FakeNotification.requestPermission = vi.fn(() => Promise.resolve("granted"));
  vi.stubGlobal("Notification", FakeNotification);

  vi.stubGlobal("localStorage", {
    getItem: (k) => (storageMap.has(k) ? storageMap.get(k) : null),
    setItem: (k, v) => storageMap.set(k, String(v)),
    removeItem: (k) => storageMap.delete(k),
  });
}

function fireGesture(type = "pointerdown") {
  for (const cb of listeners[type] || []) cb();
}

/** Fresh `cues.js` module instance, so each test starts with unlocked=false,
 * an empty rate-limit map and a never-asked notification flag. */
async function loadCues() {
  vi.resetModules();
  return import("./cues.js");
}

function emit(kind) {
  capturedHandler({ kind, at: Date.now() / 1000, source: "test", payload: {} });
}

beforeEach(() => {
  stubEnvironment();
  capturedHandler = null;
  capturedUnsubscribe = null;
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("init wiring", () => {
  it("subscribes to every kind and hands back the unsubscribe function", async () => {
    const { init } = await loadCues();
    const result = init();
    expect(subscribeEvents).toHaveBeenCalledWith("", expect.any(Function));
    expect(result).toBe(capturedUnsubscribe);
  });
});

describe("the mapping table", () => {
  it("plays the waiting cue and requests a notification on decision.pending", async () => {
    const { init } = await loadCues();
    init();
    fireGesture();
    emit("decision.pending");
    expect(audioInstances[0].createOscillator).toHaveBeenCalled();
    expect(Notification.requestPermission).toHaveBeenCalledTimes(1);
  });

  it("plays the blocked cue and requests a notification on guard.blocked", async () => {
    const { init } = await loadCues();
    init();
    fireGesture();
    emit("guard.blocked");
    expect(audioInstances[0].createOscillator).toHaveBeenCalledTimes(1);
    expect(Notification.requestPermission).toHaveBeenCalledTimes(1);
  });

  it("does nothing on decision.resolved — the human already knows", async () => {
    const { init } = await loadCues();
    init();
    fireGesture();
    emit("decision.resolved");
    expect(audioInstances[0].createOscillator).not.toHaveBeenCalled();
    expect(notifications).toHaveLength(0);
    expect(Notification.requestPermission).not.toHaveBeenCalled();
  });

  it("never cues summary.updated, even though it fires on every ingest tick", async () => {
    const { init } = await loadCues();
    init();
    fireGesture();
    for (let i = 0; i < 5; i++) emit("summary.updated");
    expect(audioInstances[0].createOscillator).not.toHaveBeenCalled();
    expect(notifications).toHaveLength(0);
  });

  it("ignores an unknown kind silently", async () => {
    const { init } = await loadCues();
    init();
    fireGesture();
    emit("treasure.published");
    expect(audioInstances[0].createOscillator).not.toHaveBeenCalled();
    expect(notifications).toHaveLength(0);
  });
});

describe("rate limit", () => {
  it("turns a burst of five decision.pending into exactly one cue and one notification", async () => {
    const { init } = await loadCues();
    init();
    fireGesture();
    for (let i = 0; i < 5; i++) emit("decision.pending");
    // `waiting` is a two-note interval — one full play makes two oscillators,
    // so 2 (not 10) is what "exactly one sound" looks like at this level.
    expect(audioInstances[0].createOscillator).toHaveBeenCalledTimes(2);
    expect(Notification.requestPermission).toHaveBeenCalledTimes(1);
  });

  it("lets a kind fire again once the window has passed", async () => {
    vi.useFakeTimers();
    try {
      const { init } = await loadCues();
      init();
      fireGesture();
      emit("guard.blocked");
      expect(audioInstances[0].createOscillator).toHaveBeenCalledTimes(1);
      vi.advanceTimersByTime(3001);
      emit("guard.blocked");
      expect(audioInstances[0].createOscillator).toHaveBeenCalledTimes(2);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("mute", () => {
  it("defaults to unmuted", async () => {
    const { isMuted } = await loadCues();
    expect(isMuted()).toBe(false);
  });

  it("silences both audio and notifications once muted", async () => {
    const { init, isMuted, setMuted } = await loadCues();
    Notification.permission = "granted"; // would notify immediately if not muted
    setMuted(true);
    expect(isMuted()).toBe(true);
    init();
    fireGesture();
    emit("decision.pending");
    expect(audioInstances[0].createOscillator).not.toHaveBeenCalled();
    expect(notifications).toHaveLength(0);
  });

  it("un-mutes and lets cues through again", async () => {
    const { init, setMuted } = await loadCues();
    setMuted(true);
    setMuted(false);
    init();
    fireGesture();
    emit("guard.blocked");
    expect(audioInstances[0].createOscillator).toHaveBeenCalled();
  });
});

describe("audio unlock", () => {
  it("drops a cue requested before any user gesture", async () => {
    const { init } = await loadCues();
    init();
    emit("decision.pending"); // no fireGesture() first
    expect(audioInstances).toHaveLength(0); // context never even constructed
  });

  it("unlocks on a keydown gesture and removes both one-shot listeners", async () => {
    const { init } = await loadCues();
    init();
    fireGesture("keydown");
    expect(listeners.pointerdown).toHaveLength(0);
    expect(listeners.keydown).toHaveLength(0);
    emit("guard.blocked");
    expect(audioInstances[0].createOscillator).toHaveBeenCalled();
    expect(audioInstances[0].resume).toHaveBeenCalledTimes(1);
  });

  it("unlocks on a pointerdown gesture", async () => {
    const { init } = await loadCues();
    init();
    fireGesture("pointerdown");
    emit("guard.blocked");
    expect(audioInstances[0].createOscillator).toHaveBeenCalled();
  });
});

describe("notification permission timing", () => {
  it("never requests permission at init, only from a mapped event", async () => {
    const { init } = await loadCues();
    init();
    fireGesture();
    expect(Notification.requestPermission).not.toHaveBeenCalled();
    emit("decision.pending");
    expect(Notification.requestPermission).toHaveBeenCalledTimes(1);
  });

  it("stops asking once permission is denied", async () => {
    Notification.permission = "denied";
    const { init } = await loadCues();
    init();
    fireGesture();
    emit("decision.pending");
    emit("guard.blocked");
    expect(Notification.requestPermission).not.toHaveBeenCalled();
    expect(notifications).toHaveLength(0);
  });
});

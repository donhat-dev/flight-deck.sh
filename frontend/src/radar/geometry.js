/**
 * Radar geometry — the polar math both radar views share.
 *
 * Kept pure and unit-free: every radius is a FRACTION of the radar's radius, so
 * the same numbers drive the 880px full radar and the 530px quadrant view without
 * a second set of constants to keep in step. The React components multiply by
 * whatever radius they were given.
 *
 * Blip placement is DETERMINISTIC, not random. A radar whose blips jump on every
 * render cannot be read across two screenshots, and "did it move?" is the one
 * question this whole page exists to answer. Placement therefore comes from the
 * blip's position inside its (quadrant, ring) group, which changes only when the
 * membership of that group changes.
 */

/** Inner to outer. The order is load-bearing: index 0 is the innermost ring. */
export const RINGS = ["adopt", "trial", "assess", "caution"];

export const RING_LABEL = {
  adopt: "Adopt",
  trial: "Trial",
  assess: "Assess",
  caution: "Caution",
};

/**
 * Outer edge of each ring, as a fraction of the radar radius.
 *
 * Deliberately NOT evenly spaced. Adopt gets the widest band because it holds the
 * decisions that are already load-bearing, and a reader looking for "what did we
 * commit to" should not have to hunt inside a thin disc. Caution is narrowest: it
 * is a parking lot, and giving it equal area would suggest equal weight.
 */
export const RING_EDGE = { adopt: 0.34, trial: 0.585, assess: 0.815, caution: 1 };

/**
 * Quadrants, in the order they occupy the circle counter-clockwise from the +x
 * axis. `turn` is that index, so quadrant q spans [q*90, q*90+90) degrees.
 */
export const QUADRANTS = [
  { k: "platforms", label: "Platforms", turn: 0 },
  { k: "techniques", label: "Techniques", turn: 1 },
  { k: "tools", label: "Tools", turn: 2 },
  { k: "lang", label: "Languages & Frameworks", turn: 3 },
];

export const quadrantOf = (k) => QUADRANTS.find((q) => q.k === k) || QUADRANTS[0];

/**
 * The four quadrants with THIS board's labels.
 *
 * Labels are per-radar (a migration radar renames `lang` to Convention) and the server
 * resolves them onto `board.quadrants`. Keys and turns stay the module constants above —
 * they are geometry, not prose — so only the label is taken from the board, and a board
 * that predates the field falls back to the classic set unchanged.
 */
export function boardQuadrants(board) {
  const labels = new Map((board?.quadrants ?? []).map((q) => [q.k, q.label]));
  return QUADRANTS.map((q) => ({ ...q, label: labels.get(q.k) ?? q.label }));
}

/**
 * Which way a move from `from` to `to` travels: `in` toward Adopt, `out` toward
 * Caution, `held` when the ring does not change.
 *
 * This is the ONE derivation the client is allowed to repeat, and only because the
 * server cannot do it: the move-blip form has to label a choice the reader has not
 * made yet, and a hypothetical has nothing on the server to derive from. Once the
 * move is recorded the page refetches and the server's own `state` replaces this
 * preview, so a disagreement between the two would be visible immediately rather
 * than persisted — which is why a second copy of the ring order is tolerable here
 * and is not anywhere else.
 *
 * Read off `RINGS` rather than a table of its own, so the order has one home.
 */
export function directionTo(from, to) {
  const a = RINGS.indexOf(to);
  const b = RINGS.indexOf(from);
  if (a < 0 || b < 0) return "new";
  if (a === b) return "held";
  // Lower index is nearer the centre, which is nearer Adopt.
  return a < b ? "in" : "out";
}

/** [inner, outer] edge fractions of one ring, under the given edge set. */
export function ringBand(ring, edges = RING_EDGE) {
  const i = RINGS.indexOf(ring);
  if (i < 0) return [0, edges.adopt];
  return [i === 0 ? 0 : edges[RINGS[i - 1]], edges[ring]];
}

/**
 * Ring edges sized by OCCUPANCY: a busier ring gets more of the radius, so density
 * stays even across rings instead of even width crowding the one everyone lands in.
 * This replaced the fixed RING_EDGE on live boards after five Adopt blips landed in
 * the innermost — and therefore smallest — band and started overlapping while the
 * outer rings sat near-empty.
 *
 * The first version made band AREA strictly proportional to count. A real board
 * proved that too strong: once a majority of blips sat in Adopt, that one ring
 * swallowed roughly two thirds of the radius and the other three collapsed into
 * slivers whose labels needed `spreadMids` just to stop overlapping. Band WIDTH
 * proportional to the square root of the count is the damping that keeps this —
 * a ring's size still tracks its load, but no single ring can consume the disc, so
 * every ring stays a readable band.
 *
 * The floor keeps an empty ring visible. A zero-count band with no floor collapses to
 * a hairline, and a hairline labelled "Caution" reads as a rendering bug rather than
 * as an empty ring — the drawing must keep saying the ring exists.
 */
export function ringEdges(counts = {}, { floor = 0.1 } = {}) {
  const total = RINGS.reduce((sum, r) => sum + (counts[r] || 0), 0);
  if (total === 0) return { ...RING_EDGE };
  const pad = Math.max(1, total * floor);
  const weights = RINGS.map((r) => Math.sqrt((counts[r] || 0) + pad));
  const sum = weights.reduce((a, b) => a + b, 0);
  const edges = {};
  let acc = 0;
  RINGS.forEach((r, i) => {
    acc += weights[i];
    edges[r] = acc / sum;
  });
  edges[RINGS[RINGS.length - 1]] = 1;   // exact, not 0.9999…
  return edges;
}

/**
 * Push overlapping label anchors apart along one axis.
 *
 * Needed because occupancy-sized edges can make the outer rings THIN: on a board
 * whose blips sit mostly in Adopt, Trial/Assess/Caution midpoints land within a
 * label-width of each other and the corridor prints "TRIALASSESSCAUTION" as one
 * smear. Bands may be as narrow as the floor allows; labels may not.
 *
 * Two passes. Forward enforces the gap walking outward; if that pushes the last
 * label past `max`, the backward pass pulls the tail in while keeping the gap. The
 * result stays as close to the true midpoints as the constraints allow, so a label
 * still reads as belonging to its band.
 */
export function spreadMids(mids, { gap, max = Infinity } = {}) {
  const out = [...mids];
  for (let i = 1; i < out.length; i++) out[i] = Math.max(out[i], out[i - 1] + gap);
  if (out.length && out[out.length - 1] > max) {
    out[out.length - 1] = max;
    for (let i = out.length - 2; i >= 0; i--) out[i] = Math.min(out[i], out[i + 1] - gap);
  }
  return out;
}

/**
 * Polar to cartesian, in SCREEN space.
 *
 * `deg` is measured counter-clockwise from the +x axis the way a reader expects
 * (90° points up), which means the y term is subtracted: SVG's y grows downward.
 * Getting this backwards mirrors the whole radar vertically and every quadrant
 * ends up in the wrong corner, which is exactly the kind of error that looks
 * plausible in a screenshot — hence `geometry.test.js`.
 */
export function polar(cx, cy, r, deg) {
  const a = (deg * Math.PI) / 180;
  return { x: cx + r * Math.cos(a), y: cy - r * Math.sin(a) };
}

/**
 * SVG path for one donut sector: ring `[rIn, rOut]` between `deg0` and `deg1`.
 *
 * The sweep flags are 0 then 1 because the outer edge is traced counter-clockwise
 * (increasing degrees, which is anti-clockwise on screen in a y-down space) and
 * the inner edge has to come back the other way to close the band.
 *
 * `flat` scales ONLY the radii the `A` commands draw with, never the endpoints.
 * An SVG arc through two fixed points is underdetermined by radius alone — the
 * same two points sit on infinitely many circles, and a larger one bows less
 * between them. That is the whole fix for the full radar's four-leaf-clover look:
 * each quadrant is a donut sector drawn around its own translated centre (so the
 * assembled figure is four discs, not one), and each one bulges toward its own
 * corner. Handing this function a `flat` slightly above 1 draws the connecting
 * arc on a bigger circle while `a`/`b`/`c`/`d` — where the band meets its
 * neighbours and where blips are placed — stay computed from the true `rIn`/`rOut`,
 * so the bulge sags a couple percent of the radius inward and nothing that reads
 * or aligns against the endpoints moves at all.
 */
export function sectorPath(cx, cy, rIn, rOut, deg0, deg1, flat = 1) {
  const large = Math.abs(deg1 - deg0) > 180 ? 1 : 0;
  const a = polar(cx, cy, rOut, deg0);
  const b = polar(cx, cy, rOut, deg1);
  const c = polar(cx, cy, rIn, deg1);
  const d = polar(cx, cy, rIn, deg0);
  const Ro = rOut * flat;
  const Ri = rIn * flat;
  const f = (n) => Math.round(n * 100) / 100;
  if (rIn <= 0) {
    return `M ${f(cx)} ${f(cy)} L ${f(a.x)} ${f(a.y)} `
      + `A ${f(Ro)} ${f(Ro)} 0 ${large} 0 ${f(b.x)} ${f(b.y)} Z`;
  }
  return `M ${f(a.x)} ${f(a.y)} `
    + `A ${f(Ro)} ${f(Ro)} 0 ${large} 0 ${f(b.x)} ${f(b.y)} `
    + `L ${f(c.x)} ${f(c.y)} `
    + `A ${f(Ri)} ${f(Ri)} 0 ${large} 1 ${f(d.x)} ${f(d.y)} Z`;
}

/**
 * An OPEN arc: one concentric stroke, with no radial edges.
 *
 * The ring boundaries have to be drawn separately from the filled sectors, because
 * a filled sector is a CLOSED path and stroking it outlines all four of its sides
 * — including the two radial cuts at the quadrant seams. Those radial strokes meet
 * at each axis and read as the quadrant having been chamfered: the ring looks like
 * it stops short of the circle instead of continuing round it. Splitting fill from
 * edge is what makes the seams a gap rather than a bevel.
 *
 * `flat` scales only the `A` radius, same trick and same reason as in `sectorPath`:
 * the endpoints `a`/`b` are where this boundary meets its neighbour's, so they stay
 * on the true circle of radius `r` while the stroke connecting them bows on a
 * radius `flat` times larger. That is enough to stop a ring boundary drawn around a
 * translated quadrant centre from reading as its own small arc bulging into the
 * corner — the same few-percent sag that flattens the fills, applied to the line.
 */
export function arcPath(cx, cy, r, deg0, deg1, flat = 1) {
  const large = Math.abs(deg1 - deg0) > 180 ? 1 : 0;
  const a = polar(cx, cy, r, deg0);
  const b = polar(cx, cy, r, deg1);
  const R = r * flat;
  const f = (n) => Math.round(n * 100) / 100;
  return `M ${f(a.x)} ${f(a.y)} A ${f(R)} ${f(R)} 0 ${large} 0 ${f(b.x)} ${f(b.y)}`;
}

/** Per turn, which way the quarter opens: (ex, ey) multiply the local offsets. */
const BAND_SIGN = [[1, 1], [-1, 1], [-1, -1], [1, -1]];

/** Where a circle of radius r leaves each band: X at |Y| = gh, Y at |X| = gv. */
function bandEnds(r, gh, gv) {
  return {
    atH: Math.sqrt(Math.max(0, r * r - gh * gh)),
    atV: Math.sqrt(Math.max(0, r * r - gv * gv)),
  };
}

/**
 * The band-clip construction that replaced the translated-centre one above.
 *
 * Four quarter discs around four translated centres bulge toward their own
 * corners and read as a four-leaf clover; `sectorPath`'s `flat` factor only
 * ever compensated for that bulge without curing it — the arcs it drew were
 * no longer true circles. This is the correct construction, verified by
 * measurement: keep ONE centre and cut two straight strips out of the single
 * disc, a horizontal one of half-width `gh` and a vertical one of half-width
 * `gv`. Nothing is translated, so every arc this draws sits on the same
 * circle — the assembled figure cannot read as a clover and needs no
 * flattening. The angular extent shrinks as the radius shrinks, because a
 * constant-width strip eats a bigger angular bite the closer a ring sits to
 * the centre; that is the correct behaviour for a straight corridor, not a
 * defect to smooth over. A ring whose outer radius does not clear the corner
 * at `hypot(gv, gh)` lies entirely inside the cross the two strips form, and
 * returns an empty path rather than a degenerate one.
 */
export function bandSectorPath(cx, cy, rIn, rOut, turn, gh, gv) {
  const corner = Math.hypot(gv, gh);
  if (rOut <= corner) return "";
  const [ex, ey] = BAND_SIGN[turn];
  const sweep = ex * ey > 0 ? 0 : 1;
  const f = (n) => Math.round(n * 100) / 100;
  const pt = (X, Y) => `${f(cx + ex * X)} ${f(cy - ey * Y)}`;
  const o = bandEnds(rOut, gh, gv);
  const head = `M ${pt(o.atH, gh)} A ${f(rOut)} ${f(rOut)} 0 0 ${sweep} ${pt(gv, o.atV)}`;
  if (rIn <= corner) return `${head} L ${pt(gv, gh)} Z`;
  const i = bandEnds(rIn, gh, gv);
  return `${head} L ${pt(gv, i.atV)} `
    + `A ${f(rIn)} ${f(rIn)} 0 0 ${1 - sweep} ${pt(i.atH, gh)} Z`;
}

/** The open-arc counterpart of `bandSectorPath`, for ring boundaries — same
 *  reason `arcPath` exists apart from `sectorPath`: a filled band's stroke
 *  would outline its radial cuts too. Same clip, same empty-path rule. */
export function bandArcPath(cx, cy, r, turn, gh, gv) {
  const corner = Math.hypot(gv, gh);
  if (r <= corner) return "";
  const [ex, ey] = BAND_SIGN[turn];
  const sweep = ex * ey > 0 ? 0 : 1;
  const f = (n) => Math.round(n * 100) / 100;
  const pt = (X, Y) => `${f(cx + ex * X)} ${f(cy - ey * Y)}`;
  const e = bandEnds(r, gh, gv);
  return `M ${pt(e.atH, gh)} A ${f(r)} ${f(r)} 0 0 ${sweep} ${pt(gv, e.atV)}`;
}

/**
 * Give every blip a place, as `{ rFrac, deg }`.
 *
 * `spanDeg` is how much of the 90° quadrant blips may use; the remainder is a
 * margin so nothing sits on a quadrant seam where it would read as belonging to
 * the neighbour. `radialSpread` alternates blips off the band's midline so a
 * crowded ring does not become a single arc of touching circles.
 *
 * When `quadrant` is given, every blip is placed in the 0-90 degree sector — the
 * single-quadrant view draws only that sector, so its local frame always starts
 * at zero regardless of which quadrant is on screen.
 *
 * `bands` opts into the full view's band-clip geometry: `{ h, v, pad }`, all as
 * fractions of the radar radius. Under a fixed angular margin a blip near a
 * quadrant edge at small radius could land inside a corridor, because the
 * angular room a constant-width strip leaves GROWS as the radius shrinks — the
 * opposite of what a fixed `spanDeg` margin assumes. `bands` is optional and
 * `null` by default so existing callers (the quadrant view, the tests) see no
 * change at all — the two placements must stay byte-identical when `bands` is
 * absent.
 */
export function placeBlips(blips, { quadrant = null, spanDeg = 74, radialSpread = 0.19,
                                     edges = RING_EDGE, bands = null } = {}) {
  const groups = new Map();
  for (const b of blips) {
    const key = `${b.quadrant}|${b.ring}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(b);
  }
  const out = [];
  for (const [, list] of groups) {
    const [rIn, rOut] = ringBand(list[0].ring, edges);
    const mid = (rIn + rOut) / 2;
    const band = rOut - rIn;
    // The innermost ring reaches r=0, where there is no room for a circle. Push
    // its blips off the origin instead of letting one land on the centre point.
    const floor = list[0].ring === RINGS[0] ? rOut * 0.28 : rIn;
    const base = Math.max(mid, floor + band * 0.2);
    const turn = quadrant ? 0 : quadrantOf(list[0].quadrant).turn;
    // A blip must clear both strips, its own drawn radius included. Below this radius
    // there is no angle in the quadrant that does, so the radius is what gives.
    const clear = bands
      ? Math.hypot(bands.h + bands.pad, bands.v + bands.pad) * 1.02
      : 0;
    list.forEach((b, i) => {
      const nudge = list.length > 1 ? (i % 2 ? 1 : -1) * band * radialSpread : 0;
      const rFrac = Math.min(rOut * 0.94, Math.max(base + nudge, clear));
      let deg;
      if (bands) {
        // Odd turns sweep from the vertical axis, so the two strips swap roles.
        const [first, second] = turn % 2 === 0
          ? [bands.h, bands.v] : [bands.v, bands.h];
        const lo = (Math.asin(Math.min(1, (first + bands.pad) / rFrac)) * 180) / Math.PI;
        const hi = (Math.acos(Math.min(1, (second + bands.pad) / rFrac)) * 180) / Math.PI;
        const mid = (lo + hi) / 2;
        deg = hi > lo
          ? turn * 90 + lo + ((i + 0.5) / list.length) * (hi - lo)
          : turn * 90 + mid;
      } else {
        const margin = (90 - spanDeg) / 2;
        deg = turn * 90 + margin + ((i + 0.5) / list.length) * spanDeg;
      }
      out.push({ ...b, rFrac, deg });
    });
  }
  // Stable order so React keys and the DOM order do not shuffle between renders.
  return out.sort((a, b) => a.num - b.num);
}

/**
 * Which way the movement arc faces, in degrees.
 *
 * A blip that moved INWARD wears its arc on the side facing the centre, so the
 * arc reads as the edge it came through. Outward is the mirror. This is the only
 * encoding on the radar that carries direction, so it has to be derived from the
 * blip's own angle rather than from a fixed offset.
 */
export function arcFacing(state, deg) {
  if (state === "in") return deg + 180;
  if (state === "out") return deg;
  return null;
}

/** True when a blip's newest evidence is old enough to stop trusting. */
export const STALE_DAYS = 60;
export const isStale = (blip) => (blip.evidenceAgeDays ?? 0) > STALE_DAYS;

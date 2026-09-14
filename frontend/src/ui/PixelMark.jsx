import React from "react";

/**
 * Pixel marks for the side menu.
 *
 * Every mark is an 8x8 grid drawn at 16px, so one grid cell is exactly two
 * device pixels and no edge ever lands on a half pixel. That is the whole
 * reason for the grid: a stroked 16px icon has to hint its curves and comes out
 * soft, while a filled cell either covers a pixel pair or it does not.
 *
 * The source of truth is the ASCII art below — `#` filled, `.` empty — because
 * that is the form a person can read and correct. `gridToPath` turns one grid
 * into a single `<path>` with one `M{x} {y}h1v1h-1z` subpath per filled cell,
 * which keeps each mark to one element and one fill.
 *
 * The paths are built at module load, not on render, so a mistyped grid throws
 * the moment the file is imported instead of drawing a wrong-but-plausible
 * shape somebody has to notice by eye.
 */

const SIDE = 8;

export function gridToPath(grid, name = "mark") {
  const rows = grid.trim().split("\n").map((r) => r.trim());
  if (rows.length !== SIDE) {
    throw new Error(`pixel mark "${name}" has ${rows.length} rows, expected ${SIDE}`);
  }
  let d = "";
  rows.forEach((row, y) => {
    if (row.length !== SIDE) {
      throw new Error(`pixel mark "${name}" row ${y} is ${row.length} cells, expected ${SIDE}`);
    }
    for (let x = 0; x < SIDE; x += 1) {
      const cell = row[x];
      if (cell === "#") d += `M${x} ${y}h1v1h-1z`;
      else if (cell !== ".") {
        throw new Error(`pixel mark "${name}" row ${y} has "${cell}"; only "#" and "." are cells`);
      }
    }
  });
  return d;
}

const GRIDS = {
  spend: `
    ........
    ########
    #......#
    #....###
    #....#.#
    #....###
    #......#
    ########`,
  logbook: `
    ........
    ##.#####
    ........
    ##.#####
    ........
    ##.#####
    ........
    ........`,
  routeloom: `
    ....##..
    ...##...
    ..##....
    .#####..
    ...##...
    ..##....
    .##.....
    ........`,
  charts: `
    ........
    ......##
    ......##
    ...##.##
    ...##.##
    ##.##.##
    ##.##.##
    ########`,
  diff: `
    ########
    #..##..#
    #..##..#
    #..##..#
    #..##..#
    #..##..#
    #..##..#
    ########`,
  hub: `
    ..####..
    .##..##.
    ##....##
    ##....##
    ##....##
    ##....##
    .##..##.
    ..####..`,
  treasures: `
    ...##...
    ..####..
    .######.
    ########
    ########
    .######.
    ..####..
    ...##...`,
  // Treasures' one child (Config) is not in the mark set the design shipped, so
  // this cog is drawn here. Deliberately not the ring-with-corners shape the
  // other frame-like marks use: at 16px an icon's only job is to not be
  // mistaken for its neighbour.
  config: `
    ........
    .#.##.#.
    .######.
    .##..##.
    .##..##.
    .######.
    .#.##.#.
    ........`,
  components: `
    ###.###.
    ###.###.
    ###.###.
    ........
    ###.###.
    ###.###.
    ###.###.
    ........`,
  comms: `
    ........
    ########
    ##....##
    #.#..#.#
    #..##..#
    #......#
    ########
    ........`,
  manuals: `
    .######.
    .#....#.
    .#.##.#.
    .#....#.
    .#.##.#.
    .#....#.
    .######.
    ........`,
  // Three notes with a link running between them — the memory store is a graph,
  // and the one shape that separates this row from Manuals (a book) at 16px.
  memory: `
    ...##...
    ...##...
    ..#..#..
    .#....#.
    #......#
    ##....##
    ##....##
    ........`,
  hangar: `
    ........
    ...##...
    ..####..
    .######.
    ########
    ##....##
    ##....##
    ##....##`,
  relay: `
    ........
    ..#.....
    .#######
    ..#.....
    .....#..
    #######.
    .....#..
    ........`,
  tickets: `
    ........
    ##.##.##
    ##.##.##
    ##.##.##
    ##.##...
    ##.##...
    ##......
    ........`,
  missions: `
    ...##...
    ...##...
    ..####..
    ..####..
    .######.
    .######.
    ########
    ########`,
  appearance: `
    ..####..
    .##..##.
    ##..####
    ##..####
    ##..####
    ##..####
    .##..##.
    ..####..`,
  radar: `
    ...##...
    ..####..
    .##..##.
    ##.##.##
    ##.##.##
    .##..##.
    ..####..
    ...##...`,
  radio: `
    ........
    .######.
    #......#
    ..####..
    .#....#.
    ...##...
    ...##...
    ........`,
  moon: `
    ..###...
    .###....
    ###.....
    ###.....
    ###.....
    ###.....
    .###....
    ..###...`,
  sun: `
    ...##...
    #......#
    ..####..
    #.####.#
    #.####.#
    ..####..
    #......#
    ...##...`,
  chevron: `
    ........
    ..#..#..
    .#..#...
    #..#....
    #..#....
    .#..#...
    ..#..#..
    ........`,
  // The trailing mark on the two rows that open outside this app.
  leavesApp: `
    ........
    ..#####.
    .....##.
    ....#.#.
    ...#....
    ..#.....
    .#......
    ........`,
};

export const PIXEL_PATHS = Object.fromEntries(
  Object.entries(GRIDS).map(([name, grid]) => [name, gridToPath(grid, name)]),
);

/**
 * One mark. `flip` mirrors it horizontally, which is how the chevron serves
 * both directions without a second grid.
 */
export default function PixelMark({ name, className = "", flip = false }) {
  const d = PIXEL_PATHS[name];
  if (!d) return null;
  return (
    <svg
      className={`fdx-pixel-mark${flip ? " fdx-pixel-mark-flip" : ""}${className ? ` ${className}` : ""}`}
      viewBox="0 0 8 8"
      shapeRendering="crispEdges"
      aria-hidden="true"
      focusable="false"
    >
      <path d={d} fill="currentColor" />
    </svg>
  );
}

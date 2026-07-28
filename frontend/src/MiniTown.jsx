import React, {
  memo,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  ArrowsOutCardinal,
  BookOpen,
  Buildings,
  Clock,
  Cursor,
  Factory,
  FastForward,
  Heart,
  House,
  MagnifyingGlassMinus,
  MagnifyingGlassPlus,
  MapPin,
  Pause,
  Play,
  Scooter,
  SquaresFour,
  Storefront,
  Trash,
  UsersThree,
  X,
} from "@phosphor-icons/react";

const ROWS = 8;
const COLS = 10;
const CELL_W = 82;
const CELL_H = 70;
const MAX_DRAG = 3;

const ZONES = {
  residential: {
    label: "Residential",
    short: "Homes",
    Icon: House,
    tint: "#e9b94e",
    blurb: "Tube houses, balconies and sleepy courtyards",
  },
  shop: {
    label: "Shop",
    short: "Shops",
    Icon: Storefront,
    tint: "#e86645",
    blurb: "Cafés, mini markets and street-side trade",
  },
  workspace: {
    label: "Workspace",
    short: "Work",
    Icon: Factory,
    tint: "#3f9f90",
    blurb: "Repair yards, ateliers and compact studios",
  },
};

const PERIODS = [
  { id: "morning", label: "Morning", start: 6 * 60 + 20 },
  { id: "midday", label: "Midday", start: 12 * 60 + 10 },
  { id: "sunset", label: "Sunset", start: 17 * 60 + 35 },
  { id: "night", label: "Night", start: 20 * 60 + 15 },
];

const CONCEPTS = [
  {
    src: "/minitown-concepts/concept-empty-land.png",
    title: "First foundations",
    note: "Empty ground, a clear grid and the first perimeter road",
    wide: true,
  },
  {
    src: "/minitown-concepts/concept-lived-in-day.png",
    title: "A town in motion",
    note: "Growth, resident routines and varied street traffic",
  },
  {
    src: "/minitown-concepts/concept-connected-block.png",
    title: "Three-lot drag",
    note: "One shared footprint with no roads through the middle",
  },
  {
    src: "/minitown-concepts/concept-cozy-night.png",
    title: "Occupied after dark",
    note: "Cool streets, honey windows and a resident inspection",
    wide: true,
  },
  {
    src: "/minitown-concepts/morning-neighborhood.png",
    title: "Signature morning",
    note: "Dense blocks, breakfast traffic, warm clear light",
    wide: true,
  },
  {
    src: "/minitown-concepts/empty-grid.png",
    title: "Empty grid",
    note: "A legible square field ready to grow",
  },
  {
    src: "/minitown-concepts/connected-shophouses.png",
    title: "Connected shop-houses",
    note: "One block, one perimeter road",
  },
  {
    src: "/minitown-concepts/market-cafe.png",
    title: "Market corner",
    note: "Food carts, cafés and delivery rhythms",
  },
  {
    src: "/minitown-concepts/sunset-neighborhood.png",
    title: "Sunset",
    note: "Long coral shadows and first lights",
  },
  {
    src: "/minitown-concepts/night-neighborhood.png",
    title: "Night",
    note: "Cool streets, warm occupied windows",
  },
  {
    src: "/minitown-concepts/building-stages.png",
    title: "Growth kit",
    note: "Construction, occupation and mature forms",
    wide: true,
  },
];

const PEOPLE = [
  { name: "Mai Anh", role: "florist" },
  { name: "Minh Quân", role: "repair apprentice" },
  { name: "Cô Hương", role: "café keeper" },
  { name: "Chú Bảy", role: "delivery rider" },
  { name: "Ngọc Linh", role: "studio assistant" },
  { name: "Tuấn Kiệt", role: "scooter mechanic" },
  { name: "Bà Lan", role: "retired teacher" },
  { name: "Phúc An", role: "market porter" },
  { name: "Thảo Vy", role: "tailor" },
  { name: "Anh Dũng", role: "sign painter" },
  { name: "Quỳnh Chi", role: "mini-market clerk" },
  { name: "Ông Tâm", role: "bicycle repairer" },
];

const VEHICLE_TYPES = ["scooter", "bicycle", "car", "delivery", "bus"];

const STORIES = {
  residential: {
    morning: [
      "Bà Lan waters the balcony herbs before the lane gets busy.",
      "Minh Quân locks the gate and wheels his scooter toward work.",
      "A breakfast radio drifts through the open shutters.",
    ],
    midday: [
      "A ceiling fan turns above a quiet front room.",
      "Cô Hương brings groceries home before the afternoon heat.",
      "Laundry dries quickly across the upper balcony.",
    ],
    sunset: [
      "Neighbors trade news while the alley cools down.",
      "Dinner aromas climb past the rooftop water tank.",
      "A school bag lands by the door as the lights come on.",
    ],
    night: [
      "Warm windows glow while the family settles in.",
      "The lane is quieter; a late scooter hums past.",
      "Upstairs, someone tends a small balcony garden.",
    ],
  },
  shop: {
    morning: [
      "Cô Hương rolls up the awning and serves the first iced coffees.",
      "A delivery scooter drops fresh bread beside the counter.",
      "Low stools fill as the breakfast rush arrives.",
    ],
    midday: [
      "The mini market restocks cold drinks and fruit.",
      "A regular lingers in the shade beneath the striped awning.",
      "The shopkeeper counts change between passing scooters.",
    ],
    sunset: [
      "The café adds tiny tables as the street cools.",
      "A warm bulb flickers on above the open storefront.",
      "Neighbors pause for a quick bowl before heading home.",
    ],
    night: [
      "The last customers talk softly under the shop light.",
      "A delivery rider checks one final address.",
      "Metal shutters close one careful section at a time.",
    ],
  },
  workspace: {
    morning: [
      "Tuấn Kiệt opens the repair bay and sorts the day’s tools.",
      "A cargo trike arrives with a stack of small parcels.",
      "The atelier doors fold back into the bright lane.",
    ],
    midday: [
      "Fans turn above a focused, humming workshop.",
      "A mechanic tests a scooter before lunch.",
      "Finished orders gather neatly by the rolling door.",
    ],
    sunset: [
      "The repair crew returns the last scooter to its owner.",
      "Tools are wiped down as orange light reaches the workbench.",
      "A courier collects two carefully wrapped packages.",
    ],
    night: [
      "One desk lamp stays on above a nearly finished order.",
      "The workshop gate is half closed for the evening.",
      "A final bicycle rolls out into the cool blue street.",
    ],
  },
};

const cellKey = (row, col) => `${row}:${col}`;

function periodForTime(minutes) {
  if (minutes >= 5 * 60 && minutes < 10 * 60) return "morning";
  if (minutes >= 10 * 60 && minutes < 16 * 60 + 30) return "midday";
  if (minutes >= 16 * 60 + 30 && minutes < 19 * 60 + 20) return "sunset";
  return "night";
}

function formatTime(minutes) {
  const hour = Math.floor(minutes / 60) % 24;
  const minute = minutes % 60;
  return `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
}

function getStage(block, now) {
  const age = now - block.createdAt;
  if (age < 2600) return 0;
  if (age < 9200) return 1;
  return 2;
}

function buildResidentRoster(blocks, now, minutes) {
  const occupied = blocks.filter((block) => getStage(block, now) > 0);
  const homes = occupied.filter((block) => block.type === "residential");
  const shops = occupied.filter((block) => block.type === "shop");
  const workplaces = occupied.filter((block) => block.type === "workspace");
  const hour = minutes / 60;
  const roster = [];
  let residentIndex = 0;

  homes.forEach((homeBlock) => {
    const stage = getStage(homeBlock, now);
    const residentsPerLot = stage === 2 ? 4 : 2;
    homeBlock.cells.forEach((homeCell) => {
      for (let position = 0; position < residentsPerLot; position += 1) {
        const profile = PEOPLE[(homeBlock.seed + residentIndex) % PEOPLE.length];
        const workBlock = workplaces.length
          ? workplaces[(homeBlock.seed + residentIndex) % workplaces.length]
          : null;
        const shopBlock = shops.length
          ? shops[(homeBlock.seed + residentIndex * 2) % shops.length]
          : null;
        let currentBlock = homeBlock;
        let activity = "At home";
        let next = "Staying in for the evening";

        if (hour >= 6 && hour < 7.5) {
          currentBlock = shopBlock || homeBlock;
          activity = shopBlock ? "Picking up breakfast" : "Opening the shutters";
          next = workBlock ? "Heads to work at 07:30" : "Returns home after breakfast";
        } else if (hour >= 7.5 && hour < 11.5) {
          currentBlock = workBlock || homeBlock;
          activity = workBlock ? `Working as a ${profile.role}` : "Running errands nearby";
          next = shopBlock ? "Lunch stop at 11:30" : "Home for lunch at 11:30";
        } else if (hour >= 11.5 && hour < 13) {
          currentBlock = shopBlock || homeBlock;
          activity = shopBlock ? "Having lunch at the corner shop" : "Home for lunch";
          next = workBlock ? "Back to work at 13:00" : "An afternoon at home";
        } else if (hour >= 13 && hour < 17.25) {
          currentBlock = workBlock || homeBlock;
          activity = workBlock ? `Finishing the day’s ${profile.role} work` : "Visiting the market lane";
          next = shopBlock ? "Stops for groceries at 17:30" : "Heads home at 17:30";
        } else if (hour >= 17.25 && hour < 19) {
          currentBlock = shopBlock || homeBlock;
          activity = shopBlock ? "Buying dinner on the way home" : "Talking with neighbors";
          next = "Home for dinner at 19:00";
        } else if (hour >= 19 || hour < 5.5) {
          currentBlock = homeBlock;
          activity = "Relaxing at home";
          next = "Morning routine begins at 06:00";
        } else {
          currentBlock = homeBlock;
          activity = "Getting ready for the day";
          next = shopBlock ? "Breakfast stop at 06:00" : "Steps into the lane at 06:00";
        }

        roster.push({
          id: `${homeBlock.id}-resident-${residentIndex}`,
          name: profile.name,
          role: profile.role,
          homeBlockId: homeBlock.id,
          workBlockId: workBlock?.id || null,
          shopBlockId: shopBlock?.id || null,
          currentBlockId: currentBlock.id,
          currentCell: currentBlock.cells[residentIndex % currentBlock.cells.length],
          homeCell,
          activity,
          next,
        });
        residentIndex += 1;
      }
    });
  });
  return roster;
}

function stageLabel(stage) {
  if (stage === 0) return "Under construction";
  if (stage === 1) return "Newly occupied";
  return "Mature block";
}

function useClock() {
  const [minutes, setMinutes] = useState(6 * 60 + 38);
  const [paused, setPaused] = useState(false);
  const [speed, setSpeed] = useState(1);

  useEffect(() => {
    if (paused) return undefined;
    const timer = window.setInterval(() => {
      setMinutes((current) => (current + 2 * speed) % (24 * 60));
    }, 720);
    return () => window.clearInterval(timer);
  }, [paused, speed]);

  return {
    minutes,
    setMinutes,
    paused,
    setPaused,
    speed,
    setSpeed,
    period: periodForTime(minutes),
  };
}

const BrandMark = memo(function BrandMark() {
  return (
    <div className="mt-brand" aria-label="MiniTown">
      <span className="mt-brand-mark" aria-hidden="true">
        <span />
        <span />
        <span />
        <span />
      </span>
      <span className="mt-brand-copy">
        <strong>MiniTown</strong>
        <small>Nhịp phố mỗi ngày</small>
      </span>
    </div>
  );
});

function Metric({ icon: Icon, value, label }) {
  return (
    <div className="mt-metric">
      <Icon size={16} weight="fill" aria-hidden="true" />
      <span>
        <strong>{value}</strong>
        <small>{label}</small>
      </span>
    </div>
  );
}

function TopBar({
  blocks,
  population,
  happiness,
  scooters,
  clock,
  onOpenArtbook,
  onClear,
}) {
  return (
    <header className="mt-topbar">
      <BrandMark />
      <div className="mt-district-name">
        <MapPin size={15} weight="fill" aria-hidden="true" />
        <span>Phường Nắng Mai</span>
        <i />
        <small>{blocks.length === 0 ? "Fresh ground" : `${blocks.length} lively blocks`}</small>
      </div>
      <div className="mt-top-metrics" aria-label="Town statistics">
        <Metric icon={UsersThree} value={population} label="residents" />
        <Metric icon={Heart} value={`${happiness}%`} label="content" />
        <Metric icon={Scooter} value={scooters} label="moving" />
      </div>
      <div className="mt-top-actions">
        <button className="mt-icon-button" type="button" onClick={onOpenArtbook} title="Open concept artbook">
          <BookOpen size={20} weight="bold" />
          <span>Artbook</span>
        </button>
        <button
          className="mt-icon-button mt-clear-button"
          type="button"
          onClick={onClear}
          disabled={blocks.length === 0}
          title="Clear the town"
        >
          <Trash size={19} weight="bold" />
          <span>Clear</span>
        </button>
        <div className="mt-time-chip" aria-label={`Town time ${formatTime(clock.minutes)}`}>
          <Clock size={17} weight="bold" />
          <strong>{formatTime(clock.minutes)}</strong>
          <span>{clock.period}</span>
        </div>
      </div>
    </header>
  );
}

function TimeControls({ clock }) {
  const cycleSpeed = () => {
    clock.setSpeed((current) => (current === 1 ? 2 : current === 2 ? 4 : 1));
  };

  return (
    <section className="mt-time-controls" aria-label="Day and night controls">
      <div className="mt-periods" role="group" aria-label="Jump to time of day">
        {PERIODS.map((item) => (
          <button
            type="button"
            key={item.id}
            className={clock.period === item.id ? "is-active" : ""}
            onClick={() => clock.setMinutes(item.start)}
          >
            <span className={`mt-period-dot is-${item.id}`} />
            {item.label}
          </button>
        ))}
      </div>
      <span className="mt-time-divider" />
      <button
        className="mt-mini-control"
        type="button"
        onClick={() => clock.setPaused((value) => !value)}
        title={clock.paused ? "Resume time" : "Pause time"}
      >
        {clock.paused ? <Play size={16} weight="fill" /> : <Pause size={16} weight="fill" />}
      </button>
      <button className="mt-speed-control" type="button" onClick={cycleSpeed} title="Change time speed">
        <FastForward size={15} weight="fill" />
        {clock.speed}×
      </button>
    </section>
  );
}

function CameraControls({ zoom, setZoom, resetCamera }) {
  return (
    <div className="mt-camera-controls" aria-label="Camera controls">
      <span>
        <ArrowsOutCardinal size={15} weight="bold" />
        Drag to move
      </span>
      <button
        type="button"
        onClick={() => setZoom((value) => Math.max(0.62, Number((value - 0.1).toFixed(2))))}
        title="Zoom out"
      >
        <MagnifyingGlassMinus size={17} weight="bold" />
      </button>
      <button type="button" onClick={resetCamera} title="Reset camera">
        {Math.round(zoom * 100)}%
      </button>
      <button
        type="button"
        onClick={() => setZoom((value) => Math.min(1.24, Number((value + 0.1).toFixed(2))))}
        title="Zoom in"
      >
        <MagnifyingGlassPlus size={17} weight="bold" />
      </button>
    </div>
  );
}

function ZoneDock({ mode, setMode }) {
  return (
    <nav className="mt-zone-dock" aria-label="Build tools">
      <button
        type="button"
        className={`mt-explore-tool ${mode === "explore" ? "is-active" : ""}`}
        onClick={() => setMode("explore")}
      >
        <Cursor size={21} weight="bold" />
        <span>
          <strong>Explore</strong>
          <small>Move & inspect</small>
        </span>
      </button>
      <span className="mt-dock-rule" />
      {Object.entries(ZONES).map(([id, zone]) => {
        const Icon = zone.Icon;
        return (
          <button
            type="button"
            key={id}
            className={`mt-zone-tool is-${id} ${mode === id ? "is-active" : ""}`}
            style={{ "--zone-tint": zone.tint }}
            onClick={() => setMode(id)}
          >
            <span className="mt-zone-icon">
              <Icon size={23} weight={mode === id ? "fill" : "bold"} />
            </span>
            <span>
              <strong>{zone.short}</strong>
              <small>{id === "residential" ? "Live" : id === "shop" ? "Gather" : "Make"}</small>
            </span>
            <kbd>{id === "residential" ? "1" : id === "shop" ? "2" : "3"}</kbd>
          </button>
        );
      })}
    </nav>
  );
}

function RoadEdge({ edge }) {
  return (
    <span className={`mt-road-edge is-${edge}`} aria-hidden="true">
      <i />
    </span>
  );
}

function Building({
  zone,
  stage,
  variant,
  joined,
  isHovered,
}) {
  const zoneInfo = ZONES[zone];
  return (
    <div
      className={[
        "mt-building",
        `is-${zone}`,
        `is-stage-${stage}`,
        `is-variant-${variant}`,
        joined.left ? "is-joined-left" : "",
        joined.right ? "is-joined-right" : "",
        joined.top ? "is-joined-top" : "",
        joined.bottom ? "is-joined-bottom" : "",
        isHovered ? "is-hovered" : "",
      ].join(" ")}
      style={{ "--building-tint": zoneInfo.tint }}
    >
      {stage === 0 ? (
        <>
          <span className="mt-foundation" />
          <span className="mt-scaffold is-a" />
          <span className="mt-scaffold is-b" />
          <span className="mt-scaffold is-c" />
          <span className="mt-build-cloth" />
          <span className="mt-materials">
            <i />
            <i />
            <i />
          </span>
        </>
      ) : (
        <>
          <span className="mt-building-shadow" />
          <span className="mt-building-body">
            <span className="mt-window is-one" />
            <span className="mt-window is-two" />
            <span className="mt-window is-three" />
            <span className="mt-door" />
            {zone === "shop" && <span className="mt-awning" />}
            {zone === "workspace" && <span className="mt-work-door" />}
            <span className="mt-balcony">
              <i />
              <i />
              <i />
            </span>
          </span>
          <span className="mt-building-roof">
            <span className="mt-water-tank" />
            <span className="mt-roof-plant" />
            <span className="mt-roof-line" />
          </span>
          {stage === 2 && (
            <span className="mt-upper-floor">
              <span className="mt-window is-four" />
              <span className="mt-window is-five" />
              <span className="mt-upper-awning" />
            </span>
          )}
          {zone === "shop" && <span className="mt-shop-stools"><i /><i /></span>}
          {zone === "workspace" && <span className="mt-work-props"><i /><i /></span>}
          {zone === "residential" && <span className="mt-home-plants"><i /><i /></span>}
        </>
      )}
    </div>
  );
}

function GridCell({
  row,
  col,
  block,
  stage,
  isPreview,
  previewMode,
  hoveredKey,
  onPointerDown,
  onPointerEnter,
  onHover,
  onLeave,
  onInspect,
  blockLookup,
}) {
  const key = cellKey(row, col);
  const hasSameBlock = (targetRow, targetCol) => {
    const target = blockLookup.get(cellKey(targetRow, targetCol));
    return Boolean(block && target?.id === block.id);
  };
  const joined = {
    top: hasSameBlock(row - 1, col),
    right: hasSameBlock(row, col + 1),
    bottom: hasSameBlock(row + 1, col),
    left: hasSameBlock(row, col - 1),
  };
  const variant = ((row * 3 + col * 5 + (block?.seed || 0)) % 4) + 1;

  return (
    <div
      className={[
        "mt-cell",
        block ? "is-built" : "",
        isPreview ? "is-preview" : "",
        previewMode ? `is-preview-${previewMode}` : "",
      ].join(" ")}
      role="gridcell"
      aria-label={
        block
          ? `${ZONES[block.type].label}, ${stageLabel(stage)}`
          : `Empty lot, row ${row + 1}, column ${col + 1}`
      }
      data-cell={key}
      onPointerDown={(event) => onPointerDown(event, row, col)}
      onPointerEnter={(event) => onPointerEnter(event, row, col)}
      onMouseEnter={() => onHover(key)}
      onMouseLeave={onLeave}
      onClick={() => onInspect(key)}
    >
      <span className="mt-lot-grid" aria-hidden="true" />
      {block && !joined.top && <RoadEdge edge="top" />}
      {block && !joined.right && <RoadEdge edge="right" />}
      {block && !joined.bottom && <RoadEdge edge="bottom" />}
      {block && !joined.left && <RoadEdge edge="left" />}
      {isPreview && !block && (
        <span className="mt-preview-fill">
          {React.createElement(ZONES[previewMode].Icon, { size: 19, weight: "fill" })}
        </span>
      )}
      {block && (
        <Building
          zone={block.type}
          stage={stage}
          variant={variant}
          joined={joined}
          isHovered={hoveredKey === key}
        />
      )}
    </div>
  );
}

const StreetLife = memo(function StreetLife({ blocks, residents, period }) {
  if (blocks.length === 0) return null;
  const visibleResidents = residents.slice(0, 14);
  const vehicleCount = Math.min(10, 5 + blocks.length);
  return (
    <div className={`mt-street-life is-${period}`} aria-hidden="true">
      {visibleResidents.map((resident, index) => {
        const cell = resident.currentCell;
        return (
          <span
            className={`mt-person is-path-${index % 4}`}
            key={resident.id}
            style={{
              "--person-x": `${cell.col * CELL_W + 10 + (index % 3) * 18}px`,
              "--person-y": `${cell.row * CELL_H + 54 - (index % 2) * 48}px`,
              "--person-delay": `${-index * 0.73}s`,
              "--person-color": ["#e86b47", "#f0c15c", "#3d9c8b", "#4f74a8"][index % 4],
            }}
          >
            <i />
          </span>
        );
      })}
      {blocks.slice(0, 12).map((block, index) => {
        const cell = block.cells[index % block.cells.length];
        return (
          <span
            className={`mt-streetlight is-side-${index % 2}`}
            key={`streetlight-${block.id}`}
            style={{
              "--streetlight-x": `${cell.col * CELL_W + (index % 2 ? 67 : 7)}px`,
              "--streetlight-y": `${cell.row * CELL_H + (index % 3 ? 52 : 7)}px`,
            }}
          >
            <i />
          </span>
        );
      })}
      {Array.from({ length: vehicleCount }, (_, index) => {
        const block = blocks[index % blocks.length];
        const cell = block.cells[(index + 1) % block.cells.length];
        const vehicleType = VEHICLE_TYPES[index % VEHICLE_TYPES.length];
        return (
          <span
            className={`mt-vehicle is-${vehicleType} is-route-${index % 3}`}
            key={`vehicle-${block.id}-${index}`}
            style={{
              "--vehicle-x": `${cell.col * CELL_W + 4}px`,
              "--vehicle-y": `${cell.row * CELL_H + (index % 2 ? 62 : 3)}px`,
              "--vehicle-delay": `${-index * 1.6}s`,
            }}
          >
            <i />
            <b />
            <em />
          </span>
        );
      })}
    </div>
  );
});

function EmptyGuide({ mode }) {
  const zone = mode === "explore" ? null : ZONES[mode];
  return (
    <div className="mt-empty-guide">
      <span className="mt-guide-icon">
        {zone ? <zone.Icon size={26} weight="fill" /> : <SquaresFour size={27} weight="duotone" />}
      </span>
      <div>
        <strong>{zone ? `Paint your first ${zone.short.toLowerCase()} block` : "Choose a place to begin"}</strong>
        <p>
          {zone
            ? "Click one lot or drag across up to three. Roads will wrap around the shared block."
            : "Pick a zone below, then place it on the square grid."}
        </p>
      </div>
    </div>
  );
}

function BuildHint({ mode, dragCount }) {
  if (mode === "explore") {
    return (
      <div className="mt-build-hint">
        <Cursor size={15} weight="bold" />
        Explore mode · hover a building for its story
      </div>
    );
  }
  return (
    <div className={`mt-build-hint is-${mode}`}>
      {React.createElement(ZONES[mode].Icon, { size: 15, weight: "fill" })}
      {dragCount > 0
        ? `${dragCount} ${dragCount === 1 ? "lot" : "connected lots"} selected`
        : `Drag 1–3 lots for one ${ZONES[mode].short.toLowerCase()} block`}
    </div>
  );
}

function Inspector({ block, cell, stage, period, now, residents, pinned, onClose }) {
  if (!block || !cell) return null;
  const zone = ZONES[block.type];
  const Icon = zone.Icon;
  const personIndex = (block.seed + cell.row * 2 + cell.col) % PEOPLE.length;
  const storyList = STORIES[block.type][period];
  const story = storyList[(block.seed + cell.row + cell.col) % storyList.length];
  const presentResidents = residents.filter((resident) => resident.currentBlockId === block.id);
  const assignedResidents = residents.filter((resident) => (
    resident.homeBlockId === block.id
    || resident.workBlockId === block.id
    || resident.shopBlockId === block.id
  ));
  const resident = presentResidents[0] || assignedResidents[0] || {
    name: PEOPLE[personIndex].name,
    role: PEOPLE[personIndex].role,
    activity: block.type === "shop"
      ? "Keeping the storefront open"
      : block.type === "workspace"
        ? "Checking today’s orders"
        : "Settling into the new home",
    next: period === "night" ? "Closes up after the last visitor" : "Takes a short walk through the lane",
  };
  const visiblePeople = (
    presentResidents.length > 0
      ? presentResidents
      : assignedResidents.length > 0
        ? assignedResidents
        : [resident]
  ).slice(0, 3);
  const age = now - block.createdAt;
  const progress = stage === 0 ? Math.min(96, Math.round((age / 2600) * 100)) : 100;

  return (
    <aside className={`mt-inspector is-${block.type}`} style={{ "--zone-tint": zone.tint }}>
      <div className="mt-inspector-accent" />
      <div className="mt-inspector-head">
        <span className="mt-inspector-icon">
          <Icon size={22} weight="fill" />
        </span>
        <div>
          <small>{stageLabel(stage)}</small>
          <strong>
            {block.type === "residential"
              ? "Sunlit tube house"
              : block.type === "shop"
                ? "Corner street shop"
                : "Neighborhood workshop"}
          </strong>
        </div>
        {pinned && (
          <button type="button" onClick={onClose} title="Close inspector">
            <X size={17} weight="bold" />
          </button>
        )}
      </div>
      {stage === 0 ? (
        <div className="mt-construction-readout">
          <span>
            <b style={{ width: `${progress}%` }} />
          </span>
          <p>Scaffolding is up. Neighbors pause to watch the new block take shape.</p>
        </div>
      ) : (
        <>
          <p className="mt-story">“{story}”</p>
          <div className="mt-who-list">
            {visiblePeople.map((person, index) => {
              const isPresent = !person.currentBlockId || person.currentBlockId === block.id;
              return (
                <div className="mt-presence" key={person.id || `${person.name}-${index}`}>
                  <span className="mt-person-token" style={{ "--token-color": zone.tint }}>
                    {person.name
                      .split(" ")
                      .map((part) => part[0])
                      .slice(-2)
                      .join("")}
                  </span>
                  <span>
                    <small>{isPresent ? "Here right now" : "Linked resident"}</small>
                    <strong>{person.name}</strong>
                  </span>
                  <i>{isPresent ? "present" : "away"}</i>
                </div>
              );
            })}
          </div>
          <div className="mt-routine">
            <span>
              <b>{resident.activity}</b>
              <small>{resident.role}</small>
            </span>
            <span>
              <small>Next destination</small>
              <b>{resident.next}</b>
            </span>
          </div>
        </>
      )}
    </aside>
  );
}

function ConceptImage({ item }) {
  const [status, setStatus] = useState("loading");
  return (
    <figure className={`mt-concept ${item.wide ? "is-wide" : ""}`}>
      <div className="mt-concept-visual">
        {status === "loading" && <span className="mt-image-skeleton" />}
        {status === "error" ? (
          <div className="mt-image-error">
            <Buildings size={24} weight="duotone" />
            <span>Concept image unavailable</span>
          </div>
        ) : (
          <img
            src={item.src}
            alt={`${item.title} gameplay concept`}
            loading="lazy"
            onLoad={() => setStatus("loaded")}
            onError={() => setStatus("error")}
          />
        )}
      </div>
      <figcaption>
        <strong>{item.title}</strong>
        <span>{item.note}</span>
      </figcaption>
    </figure>
  );
}

function Artbook({ onClose }) {
  useEffect(() => {
    const handleKey = (event) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [onClose]);

  return (
    <div className="mt-artbook-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className="mt-artbook"
        role="dialog"
        aria-modal="true"
        aria-labelledby="artbook-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header>
          <div>
            <span>
              Visual development · {String(CONCEPTS.length).padStart(2, "0")} studies
            </span>
            <h2 id="artbook-title">The color and rhythm of MiniTown</h2>
            <p>
              A practical low-detail world kit: compact façades, bright mornings, readable roads and many tiny lives.
            </p>
          </div>
          <button type="button" onClick={onClose} title="Close artbook">
            <X size={21} weight="bold" />
          </button>
        </header>
        <div className="mt-concept-grid">
          {CONCEPTS.map((item) => (
            <ConceptImage item={item} key={item.src} />
          ))}
        </div>
      </section>
    </div>
  );
}

function MiniTown() {
  const [blocks, setBlocks] = useState([]);
  const [mode, setMode] = useState("explore");
  const [now, setNow] = useState(Date.now());
  const [hoveredKey, setHoveredKey] = useState(null);
  const [pinnedKey, setPinnedKey] = useState(null);
  const [dragCells, setDragCells] = useState([]);
  const [zoom, setZoom] = useState(0.9);
  const [pan, setPan] = useState({ x: 0, y: 8 });
  const [artbookOpen, setArtbookOpen] = useState(false);
  const clock = useClock();
  const dragStartRef = useRef(null);
  const dragCellsRef = useRef([]);
  const panStartRef = useRef(null);

  const blockLookup = useMemo(() => {
    const lookup = new Map();
    blocks.forEach((block) => {
      block.cells.forEach((cell) => lookup.set(cellKey(cell.row, cell.col), block));
    });
    return lookup;
  }, [blocks]);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 500);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    const handleKey = (event) => {
      if (event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement) return;
      if (event.key === "1") setMode("residential");
      if (event.key === "2") setMode("shop");
      if (event.key === "3") setMode("workspace");
      if (event.key.toLowerCase() === "e") setMode("explore");
      const amount = 24;
      if (event.key === "ArrowLeft") setPan((value) => ({ ...value, x: value.x + amount }));
      if (event.key === "ArrowRight") setPan((value) => ({ ...value, x: value.x - amount }));
      if (event.key === "ArrowUp") setPan((value) => ({ ...value, y: value.y + amount }));
      if (event.key === "ArrowDown") setPan((value) => ({ ...value, y: value.y - amount }));
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, []);

  const commitDrag = useCallback(() => {
    const cells = dragCellsRef.current;
    if (dragStartRef.current && cells.length > 0 && mode !== "explore") {
      const occupied = cells.some((cell) => blockLookup.has(cellKey(cell.row, cell.col)));
      if (!occupied) {
        const id = `block-${Date.now()}-${Math.random().toString(16).slice(2, 7)}`;
        setBlocks((current) => [
          ...current,
          {
            id,
            type: mode,
            cells,
            createdAt: Date.now(),
            seed: Math.floor(Math.random() * 19),
          },
        ]);
        setPinnedKey(cellKey(cells[0].row, cells[0].col));
      }
    }
    dragStartRef.current = null;
    dragCellsRef.current = [];
    setDragCells([]);
  }, [blockLookup, mode]);

  useEffect(() => {
    const handlePointerUp = () => {
      commitDrag();
      panStartRef.current = null;
    };
    window.addEventListener("pointerup", handlePointerUp);
    window.addEventListener("pointercancel", handlePointerUp);
    return () => {
      window.removeEventListener("pointerup", handlePointerUp);
      window.removeEventListener("pointercancel", handlePointerUp);
    };
  }, [commitDrag]);

  const startBuildDrag = (event, row, col) => {
    if (mode === "explore" || blockLookup.has(cellKey(row, col))) return;
    event.preventDefault();
    dragStartRef.current = { row, col };
    const first = [{ row, col }];
    dragCellsRef.current = first;
    setDragCells(first);
  };

  const extendBuildDrag = (event, row, col) => {
    if (!dragStartRef.current || mode === "explore" || event.buttons === 0) return;
    const start = dragStartRef.current;
    const rowDelta = row - start.row;
    const colDelta = col - start.col;
    const horizontal = Math.abs(colDelta) >= Math.abs(rowDelta);
    const distance = Math.min(MAX_DRAG - 1, Math.abs(horizontal ? colDelta : rowDelta));
    const direction = Math.sign(horizontal ? colDelta : rowDelta) || 1;
    const next = [];
    for (let step = 0; step <= distance; step += 1) {
      const candidate = {
        row: start.row + (horizontal ? 0 : step * direction),
        col: start.col + (horizontal ? step * direction : 0),
      };
      if (blockLookup.has(cellKey(candidate.row, candidate.col))) break;
      next.push(candidate);
    }
    dragCellsRef.current = next;
    setDragCells(next);
  };

  const startPan = (event) => {
    if (mode !== "explore" || event.button !== 0) return;
    panStartRef.current = {
      pointerX: event.clientX,
      pointerY: event.clientY,
      originX: pan.x,
      originY: pan.y,
    };
  };

  const movePan = (event) => {
    if (!panStartRef.current) return;
    const start = panStartRef.current;
    setPan({
      x: start.originX + event.clientX - start.pointerX,
      y: start.originY + event.clientY - start.pointerY,
    });
  };

  const previewKeys = useMemo(
    () => new Set(dragCells.map((cell) => cellKey(cell.row, cell.col))),
    [dragCells],
  );

  // A live hover temporarily takes priority; moving away returns to the
  // clicked inspection so observation stays fluid without losing context.
  const activeKey = hoveredKey || pinnedKey;
  const inspectedBlock = activeKey ? blockLookup.get(activeKey) : null;
  const inspectedCell = activeKey
    ? (() => {
        const [row, col] = activeKey.split(":").map(Number);
        return { row, col };
      })()
    : null;
  const inspectedStage = inspectedBlock ? getStage(inspectedBlock, now) : null;
  const residents = useMemo(
    () => buildResidentRoster(blocks, now, clock.minutes),
    [blocks, now, clock.minutes],
  );

  const matureLots = blocks.reduce((total, block) => {
    const stage = getStage(block, now);
    return total + (stage === 0 ? 0 : block.cells.length);
  }, 0);
  const population = residents.length;
  const happiness = blocks.length === 0 ? 0 : Math.min(96, 76 + Math.min(16, matureLots * 2));
  const scooterCount = blocks.length === 0 ? 0 : Math.min(28, 2 + matureLots * 2);

  const handleClear = () => {
    setBlocks([]);
    setPinnedKey(null);
    setHoveredKey(null);
    setMode("explore");
    setPan({ x: 0, y: 8 });
    setZoom(0.9);
  };

  return (
    <div className={`minitown-app is-${clock.period}`} data-period={clock.period}>
      <div className="mt-sky-wash" aria-hidden="true" />
      <div className="mt-sun" aria-hidden="true" />
      <TopBar
        blocks={blocks}
        population={population}
        happiness={happiness}
        scooters={scooterCount}
        clock={clock}
        onOpenArtbook={() => setArtbookOpen(true)}
        onClear={handleClear}
      />
      <div className="mt-game-shell">
        <TimeControls clock={clock} />
        <CameraControls
          zoom={zoom}
          setZoom={setZoom}
          resetCamera={() => {
            setPan({ x: 0, y: 8 });
            setZoom(0.9);
          }}
        />
        <div
          className={`mt-map-viewport ${mode === "explore" ? "is-exploring" : "is-building"}`}
          onPointerDown={startPan}
          onPointerMove={movePan}
        >
          <div className="mt-map-haze" aria-hidden="true" />
          <div
            className="mt-map-stage"
            style={{
              width: `${COLS * CELL_W}px`,
              height: `${ROWS * CELL_H}px`,
              transform: `translate3d(${pan.x}px, ${pan.y}px, 0) scale(${zoom})`,
            }}
          >
            <div
              className="mt-grid"
              role="grid"
              aria-label="MiniTown development grid"
              style={{
                gridTemplateColumns: `repeat(${COLS}, ${CELL_W}px)`,
                gridTemplateRows: `repeat(${ROWS}, ${CELL_H}px)`,
              }}
            >
              {Array.from({ length: ROWS * COLS }, (_, index) => {
                const row = Math.floor(index / COLS);
                const col = index % COLS;
                const key = cellKey(row, col);
                const block = blockLookup.get(key);
                return (
                  <GridCell
                    key={key}
                    row={row}
                    col={col}
                    block={block}
                    stage={block ? getStage(block, now) : null}
                    isPreview={previewKeys.has(key)}
                    previewMode={mode === "explore" ? null : mode}
                    hoveredKey={hoveredKey}
                    onPointerDown={startBuildDrag}
                    onPointerEnter={extendBuildDrag}
                    onHover={setHoveredKey}
                    onLeave={() => setHoveredKey(null)}
                    onInspect={(clickedKey) => {
                      if (mode === "explore" && blockLookup.has(clickedKey)) {
                        setPinnedKey((current) => (current === clickedKey ? null : clickedKey));
                      }
                    }}
                    blockLookup={blockLookup}
                  />
                );
              })}
            </div>
            <StreetLife blocks={blocks} residents={residents} period={clock.period} />
          </div>
          {blocks.length === 0 && <EmptyGuide mode={mode} />}
          <BuildHint mode={mode} dragCount={dragCells.length} />
          <Inspector
            block={inspectedBlock}
            cell={inspectedCell}
            stage={inspectedStage}
            period={clock.period}
            now={now}
            residents={residents}
            pinned={Boolean(pinnedKey)}
            onClose={() => setPinnedKey(null)}
          />
          <div className="mt-neighborhood-edge is-left" aria-hidden="true" />
          <div className="mt-neighborhood-edge is-right" aria-hidden="true" />
        </div>
        <ZoneDock mode={mode} setMode={setMode} />
      </div>
      <footer className="mt-status-line">
        <span>
          {blocks.length === 0
            ? "Morning breeze over empty ground"
            : `${population} residents moving through ${blocks.length} shared blocks`}
        </span>
        <span>Grid {COLS} × {ROWS}</span>
        <span>Press 1–3 to build · E to explore</span>
      </footer>
      {artbookOpen && <Artbook onClose={() => setArtbookOpen(false)} />}
    </div>
  );
}

export default MiniTown;

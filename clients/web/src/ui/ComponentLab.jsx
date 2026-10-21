import React, { useRef, useState } from "react";
import { gsap } from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import { useGSAP } from "@gsap/react";
import {
  Button,
  CheckField,
  DataList,
  EmptyState,
  Field,
  HorizontalAccordion,
  IconButton,
  MetricStrip,
  Notice,
  ProgressBar,
  SegmentedControl,
  SelectField,
  StatusBadge,
  SurfaceCard,
  Tabs,
  TextAreaField,
  TokenMarquee,
  Toggle,
} from "./FlightComponents.jsx";

gsap.registerPlugin(ScrollTrigger, useGSAP);

const controlModes = [
  { label: "Default", value: "default" },
  { label: "Dense", value: "dense" },
  { label: "Calm", value: "calm" },
];

const environmentOptions = [
  { label: "Local development", value: "local" },
  { label: "Preview branch", value: "preview" },
  { label: "Production", value: "production" },
];

const componentCatalog = [
  { category: "Actions", items: ["Button", "IconButton"] },
  { category: "Input", items: ["Field", "SelectField", "TextAreaField", "CheckField", "Toggle"] },
  { category: "Navigation", items: ["Tabs", "SegmentedControl", "HorizontalAccordion"] },
  { category: "Feedback", items: ["StatusBadge", "ProgressBar", "Notice", "EmptyState"] },
  { category: "Data + surface", items: ["SurfaceCard", "MetricStrip", "DataList", "TokenMarquee"] },
];

const accordionItems = [
  {
    id: "signal",
    label: "Signal",
    short: "Action",
    title: "Three layers, one decision",
    body: "Coral is the action face, ink defines the frame, and pink or orange carries physical depth. State colors never collapse into one flat block.",
  },
  {
    id: "structure",
    label: "Structure",
    short: "Layout",
    title: "Rules replace floating cards",
    body: "Hairlines, joined surfaces, and aligned baselines organize dense operational data without excess chrome.",
  },
  {
    id: "state",
    label: "State",
    short: "Feedback",
    title: "Every state remains explicit",
    body: "Loading, error, disabled, and empty states keep the same footprint so workflows do not jump during updates.",
  },
  {
    id: "access",
    label: "Access",
    short: "Input",
    title: "Keyboard and touch share priority",
    body: "Controls provide visible focus, 44 pixel targets, native semantics, and directional keyboard movement.",
  },
];

function ArrowIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path d="M4 10h11M11 5l5 5-5 5" fill="none" stroke="currentColor" strokeWidth="1.7" />
    </svg>
  );
}

function CloseIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path d="M5 5l10 10M15 5 5 15" fill="none" stroke="currentColor" strokeWidth="1.7" />
    </svg>
  );
}

function SunIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <circle cx="10" cy="10" r="3.25" fill="none" stroke="currentColor" strokeWidth="1.5" />
      <path
        d="M10 1.75v2M10 16.25v2M1.75 10h2M16.25 10h2M4.16 4.16l1.42 1.42M14.42 14.42l1.42 1.42M15.84 4.16l-1.42 1.42M5.58 14.42l-1.42 1.42"
        fill="none"
        stroke="currentColor"
        strokeLinecap="square"
        strokeWidth="1.5"
      />
    </svg>
  );
}

function MoonIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path
        d="M15.8 12.9A6.7 6.7 0 0 1 7.1 4.2 6.7 6.7 0 1 0 15.8 12.9Z"
        fill="none"
        stroke="currentColor"
        strokeLinejoin="miter"
        strokeWidth="1.5"
      />
    </svg>
  );
}

function ThemeToggle({ theme, onChange }) {
  return (
    <div className="fdx-theme-toggle" role="group" aria-label="Color theme">
      <button
        type="button"
        data-active={theme === "day"}
        aria-pressed={theme === "day"}
        onClick={() => onChange("day")}
      >
        <SunIcon />
        <span>Day</span>
      </button>
      <button
        type="button"
        data-active={theme === "night"}
        aria-pressed={theme === "night"}
        onClick={() => onChange("night")}
      >
        <MoonIcon />
        <span>Night</span>
      </button>
    </div>
  );
}

function PixelCloud({ className, variant = "cumulus" }) {
  return (
    <svg
      className={`fdx-sky-cloud ${className}`}
      data-variant={variant}
      viewBox="0 0 72 32"
      preserveAspectRatio="xMidYMid meet"
      shapeRendering="crispEdges"
      aria-hidden="true"
    >
      {variant === "tower" && (
        <>
          <path className="fdx-cloud-shadow" d="M3 23h7v-5h7V9h7V3h13v5h9v5h8v5h10v5h5v6H3Z" />
          <path className="fdx-cloud-mid" d="M8 22h7v-7h7V8h11v5h8v4h9v4h10v5H8Z" />
          <path className="fdx-cloud-high" d="M13 18h7v-7h6V6h8v7h8v5h9v4H13ZM26 6V4h7v2Z" />
          <path className="fdx-cloud-low" d="M3 26h18v-3h12v3h13v-4h10v4h12v3H3Z" />
        </>
      )}
      {variant === "streak" && (
        <>
          <path className="fdx-cloud-shadow" d="M3 21h12v-4h9v-5h10v3h8v4h10v-2h8v4h9v7H3Z" />
          <path className="fdx-cloud-mid" d="M8 20h11v-4h9v-3h8v4h9v4h11v-2h8v6H8Z" />
          <path className="fdx-cloud-high" d="M14 19h9v-4h7v-2h6v5h8v4H14ZM48 20h8v3h-8Z" />
          <path className="fdx-cloud-low" d="M3 25h20v-2h12v2h18v-3h11v3h5v3H3Z" />
        </>
      )}
      {variant === "cumulus" && (
        <>
          <path className="fdx-cloud-shadow" d="M3 23h7v-6h7v-6h8V7h9v5h6V9h9v5h7v5h8v4h5v6H3Z" />
          <path className="fdx-cloud-mid" d="M8 22h7v-6h7v-4h8v4h7v-4h8v5h8v4h9v5H8Z" />
          <path className="fdx-cloud-high" d="M13 19h7v-5h7v-4h6v6h7v-4h6v6h8v4H13ZM26 10V8h6v2Z" />
          <path className="fdx-cloud-low" d="M3 26h15v-3h11v3h14v-4h10v4h15v3H3Z" />
        </>
      )}
    </svg>
  );
}

function PixelCelestial() {
  return (
    <div className="fdx-pixel-celestial">
      <svg
        className="fdx-pixel-sun"
        viewBox="0 0 24 24"
        shapeRendering="crispEdges"
        aria-hidden="true"
      >
        <g fill="currentColor">
          <rect x="8" y="6" width="8" height="12" />
          <rect x="6" y="8" width="12" height="8" />
          <rect x="11" y="1" width="2" height="3" />
          <rect x="11" y="20" width="2" height="3" />
          <rect x="1" y="11" width="3" height="2" />
          <rect x="20" y="11" width="3" height="2" />
          <rect x="4" y="4" width="3" height="3" />
          <rect x="17" y="4" width="3" height="3" />
          <rect x="4" y="17" width="3" height="3" />
          <rect x="17" y="17" width="3" height="3" />
        </g>
      </svg>
      <svg
        className="fdx-pixel-moon"
        viewBox="0 0 24 24"
        shapeRendering="crispEdges"
        aria-hidden="true"
      >
        <g fill="currentColor">
          <rect x="7" y="3" width="6" height="3" />
          <rect x="4" y="6" width="7" height="12" />
          <rect x="7" y="18" width="7" height="3" />
          <rect x="11" y="16" width="6" height="3" />
          <rect x="14" y="13" width="5" height="4" />
        </g>
        <g className="fdx-moon-detail">
          <rect x="6" y="8" width="2" height="2" />
          <rect x="8" y="15" width="2" height="2" />
        </g>
      </svg>
    </div>
  );
}

const pixelStars = [
  { x: 8, y: 16, size: 2 },
  { x: 17, y: 42, size: 1 },
  { x: 29, y: 18, size: 1 },
  { x: 39, y: 49, size: 2 },
  { x: 51, y: 13, size: 1 },
  { x: 62, y: 39, size: 1 },
  { x: 71, y: 22, size: 2 },
  { x: 82, y: 47, size: 1 },
  { x: 91, y: 18, size: 1 },
];

function PixelPlane() {
  return (
    <svg
      className="fdx-plane-sprite"
      viewBox="0 0 56 20"
      preserveAspectRatio="xMidYMid meet"
      shapeRendering="crispEdges"
      aria-hidden="true"
    >
      <g className="fdx-plane-body">
        <rect x="8" y="7" width="39" height="7" />
        <rect x="4" y="6" width="8" height="3" />
        <rect x="7" y="2" width="4" height="7" />
        <rect x="11" y="4" width="3" height="5" />
        <rect x="47" y="9" width="5" height="3" />
        <rect x="43" y="6" width="5" height="7" />
        <rect x="20" y="14" width="15" height="3" />
        <rect x="24" y="17" width="7" height="2" />
      </g>
      <g className="fdx-plane-detail">
        <rect x="17" y="9" width="3" height="3" />
        <rect x="23" y="9" width="3" height="3" />
        <rect x="29" y="9" width="3" height="3" />
        <rect x="35" y="9" width="3" height="3" />
        <rect x="41" y="9" width="3" height="3" />
      </g>
      <g className="fdx-plane-wake">
        <rect x="0" y="10" width="2" height="1" />
        <rect x="-5" y="10" width="3" height="1" />
        <rect x="-11" y="10" width="2" height="1" />
      </g>
    </svg>
  );
}

function PixelSky() {
  const sky = useRef(null);

  useGSAP(() => {
    const media = gsap.matchMedia();

    media.add("(prefers-reduced-motion: no-preference)", () => {
      const nearClouds = gsap.to(".fdx-cloud-near", {
        x: 30,
        y: -3,
        duration: 18,
        stagger: 2.4,
        repeat: -1,
        yoyo: true,
        ease: "sine.inOut",
      });
      const farClouds = gsap.to(".fdx-cloud-far", {
        x: -22,
        y: 2,
        duration: 26,
        stagger: 3.2,
        repeat: -1,
        yoyo: true,
        ease: "sine.inOut",
      });
      const flight = gsap.fromTo(
        ".fdx-plane-route",
        { x: 0 },
        {
          x: () => (sky.current?.clientWidth ?? 1280) + 320,
          duration: 30,
          repeat: -1,
          repeatRefresh: true,
          ease: "none",
        },
      );
      const aircraftFloat = gsap.to(".fdx-plane-sprite", {
        y: -4,
        duration: 1.8,
        repeat: -1,
        yoyo: true,
        ease: "sine.inOut",
      });
      const starBlink = gsap.to(".fdx-pixel-star", {
        opacity: 0.12,
        duration: 0.82,
        stagger: {
          each: 0.16,
          from: "end",
        },
        repeat: -1,
        yoyo: true,
        ease: "steps(1)",
      });
      flight.progress(0.34);

      return () => {
        nearClouds.kill();
        farClouds.kill();
        flight.kill();
        aircraftFloat.kill();
        starBlink.kill();
      };
    });

    return () => media.revert();
  }, { scope: sky });

  return (
    <div ref={sky} className="fdx-pixel-sky" aria-hidden="true">
      <div className="fdx-star-field">
        {pixelStars.map((star) => (
          <i
            key={`${star.x}-${star.y}`}
            className="fdx-pixel-star"
            style={{
              "--star-x": `${star.x}%`,
              "--star-y": `${star.y}%`,
              "--star-size": `${star.size}px`,
            }}
          />
        ))}
      </div>
      <PixelCelestial />
      <PixelCloud className="fdx-cloud-a fdx-cloud-near" variant="tower" />
      <PixelCloud className="fdx-cloud-b fdx-cloud-far" variant="streak" />
      <PixelCloud className="fdx-cloud-c fdx-cloud-near" variant="cumulus" />
      <PixelCloud className="fdx-cloud-d fdx-cloud-far" variant="streak" />
      <PixelCloud className="fdx-cloud-e fdx-cloud-near" variant="cumulus" />
      <div className="fdx-plane-route">
        <PixelPlane />
      </div>
    </div>
  );
}

function ComponentCatalog() {
  let position = 0;
  return (
    <section className="fdx-catalog" aria-labelledby="component-catalog-title">
      <div className="fdx-catalog-intro">
        <p className="fdx-eyebrow">Component catalog</p>
        <h2 id="component-catalog-title">18 production-ready contracts.</h2>
        <p>Small primitives, compound patterns, and data surfaces share one state and token model.</p>
      </div>
      <div className="fdx-catalog-groups">
        {componentCatalog.map((group) => (
          <section key={group.category}>
            <h3>{group.category}</h3>
            <ol>
              {group.items.map((item) => {
                position += 1;
                return (
                  <li key={item}>
                    <span>{String(position).padStart(2, "0")}</span>
                    <strong>{item}</strong>
                  </li>
                );
              })}
            </ol>
          </section>
        ))}
      </div>
    </section>
  );
}

function SignalConsole() {
  return (
    <div className="fdx-console" aria-label="Interface signal preview">
      <div className="fdx-console-top">
        <span>FlightDeck / control surface</span>
        <StatusBadge tone="live" pulse>Live</StatusBadge>
      </div>
      <div className="fdx-console-stage">
        <div className="fdx-console-reticle" aria-hidden="true">
          <i />
          <i />
          <i />
        </div>
        <div>
          <span>Selected language</span>
          <strong>Operational</strong>
          <small>Clear structure. Decisive signal.</small>
        </div>
      </div>
      <div className="fdx-console-options" aria-hidden="true">
        {["01 / Quiet", "02 / Field", "03 / Signal", "04 / Alert"].map((item, index) => (
          <span key={item} data-active={index === 2}>{item}</span>
        ))}
      </div>
    </div>
  );
}

function StateMatrix() {
  return (
    <div className="fdx-state-demo">
      <div className="fdx-depth-key" aria-label="Button depth anatomy">
        <span><i data-layer="face" aria-hidden="true" />Coral face</span>
        <span><i data-layer="frame" aria-hidden="true" />Ink frame</span>
        <span><i data-layer="depth" aria-hidden="true" />Pink depth</span>
      </div>
      <div className="fdx-state-matrix">
        <div>
          <span>Default</span>
          <Button>Run check</Button>
        </div>
        <div>
          <span>Loading</span>
          <Button loading>Run check</Button>
        </div>
        <div>
          <span>Error</span>
          <Button error>Retry check</Button>
        </div>
        <div>
          <span>Disabled</span>
          <Button disabled>Run check</Button>
        </div>
      </div>
    </div>
  );
}

function PixelAirspace({ paused }) {
  const stage = useRef(null);

  useGSAP(() => {
    const media = gsap.matchMedia();

    media.add("(prefers-reduced-motion: no-preference)", () => {
      if (paused) return undefined;

      const patrol = gsap.timeline({ repeat: -1 });
      patrol
        .to(".fdx-pixel-craft", {
          x: 58,
          y: -13,
          duration: 3.6,
          ease: "steps(9)",
        })
        .to(".fdx-pixel-craft", {
          x: 26,
          y: 5,
          duration: 2.4,
          ease: "steps(6)",
        })
        .to(".fdx-pixel-craft", {
          x: 0,
          y: 0,
          duration: 2.4,
          ease: "steps(6)",
        });

      const wake = gsap.timeline({ repeat: -1 });
      wake
        .to(".fdx-pixel-wake", {
          x: 50,
          y: -13,
          opacity: 0.34,
          duration: 3.6,
          ease: "steps(9)",
        })
        .to(".fdx-pixel-wake", {
          x: 18,
          y: 5,
          opacity: 0.22,
          duration: 2.4,
          ease: "steps(6)",
        })
        .to(".fdx-pixel-wake", {
          x: -8,
          y: 0,
          opacity: 0.12,
          duration: 2.4,
          ease: "steps(6)",
        });

      const scan = gsap.timeline({ repeat: -1, repeatDelay: 0.45 });
      scan
        .fromTo(
          ".fdx-pixel-scan",
          { y: -44, opacity: 0 },
          { y: 160, opacity: 0.16, duration: 4.2, ease: "steps(10)" },
        )
        .to(".fdx-pixel-scan", { opacity: 0, duration: 0.18, ease: "none" });

      gsap.to(".fdx-pixel-beacon", {
        opacity: 0.12,
        duration: 0.9,
        stagger: 0.45,
        repeat: -1,
        yoyo: true,
        ease: "steps(1)",
      });

      return () => {
        patrol.kill();
        wake.kill();
        scan.kill();
      };
    });

    return () => media.revert();
  }, { scope: stage, dependencies: [paused], revertOnUpdate: true });

  return (
    <div
      ref={stage}
      className="fdx-pixel-airspace"
      data-paused={paused}
      role="img"
      aria-label="Monochrome pixel aircraft drifting through a local telemetry field"
    >
      <div className="fdx-pixel-meta" aria-hidden="true">
        <span>Signal drift</span>
        <span>{paused ? "Motion paused" : "Local telemetry"}</span>
      </div>
      <svg
        viewBox="0 0 96 64"
        preserveAspectRatio="xMidYMid meet"
        shapeRendering="crispEdges"
        aria-hidden="true"
      >
        <g fill="currentColor" opacity="0.2">
          <rect x="5" y="10" width="1" height="1" />
          <rect x="12" y="33" width="1" height="1" />
          <rect x="19" y="7" width="1" height="1" />
          <rect x="27" y="46" width="1" height="1" />
          <rect x="37" y="15" width="1" height="1" />
          <rect x="48" y="6" width="1" height="1" />
          <rect x="61" y="40" width="1" height="1" />
          <rect x="72" y="12" width="1" height="1" />
          <rect x="83" y="30" width="1" height="1" />
          <rect x="90" y="18" width="1" height="1" />
          <rect x="7" y="57" width="82" height="1" />
          <rect x="7" y="53" width="13" height="1" />
          <rect x="76" y="53" width="13" height="1" />
          <rect x="23" y="55" width="7" height="1" />
          <rect x="65" y="55" width="6" height="1" />
        </g>
        <g className="fdx-pixel-scan" fill="currentColor">
          <rect x="4" y="8" width="88" height="1" />
          <rect x="9" y="10" width="1" height="1" opacity="0.55" />
          <rect x="86" y="10" width="1" height="1" opacity="0.55" />
        </g>
        <g className="fdx-pixel-wake" fill="currentColor">
          <rect x="15" y="29" width="3" height="1" />
          <rect x="20" y="29" width="5" height="1" />
          <rect x="27" y="29" width="2" height="1" />
        </g>
        <g className="fdx-pixel-craft" fill="currentColor">
          <rect x="42" y="18" width="3" height="18" />
          <rect x="39" y="23" width="9" height="8" />
          <rect x="30" y="27" width="27" height="3" />
          <rect x="35" y="30" width="17" height="3" />
          <rect x="39" y="36" width="10" height="3" />
          <rect x="41" y="39" width="6" height="2" />
        </g>
        <g fill="currentColor">
          <g className="fdx-pixel-beacon">
            <rect x="10" y="42" width="1" height="5" />
            <rect x="8" y="44" width="5" height="1" />
          </g>
          <g className="fdx-pixel-beacon">
            <rect x="82" y="25" width="1" height="5" />
            <rect x="80" y="27" width="5" height="1" />
          </g>
        </g>
      </svg>
    </div>
  );
}

export default function ComponentLab() {
  const root = useRef(null);
  const desire = useRef(null);
  const pin = useRef(null);
  const [mode, setMode] = useState("default");
  const [accordion, setAccordion] = useState("signal");
  const [noticeVisible, setNoticeVisible] = useState(true);
  const [environment, setEnvironment] = useState("local");
  const [autosave, setAutosave] = useState(true);
  const [retainLogs, setRetainLogs] = useState(false);
  const [detailTab, setDetailTab] = useState("overview");
  const [pixelMotionPaused, setPixelMotionPaused] = useState(false);
  const [theme, setTheme] = useState(() => (
    document.documentElement.dataset.theme === "day" ? "day" : "night"
  ));

  useGSAP(() => {
    const media = gsap.matchMedia();
    media.add("(prefers-reduced-motion: no-preference)", () => {
      gsap.from(".fdx-hero-copy > *", {
        y: 24,
        opacity: 0,
        duration: 0.62,
        stagger: 0.08,
        ease: "power3.out",
      });
      gsap.from(".fdx-console", {
        y: 32,
        rotate: 1.8,
        opacity: 0,
        duration: 0.8,
        ease: "power3.out",
      });
      gsap.fromTo(
        ".fdx-reveal-word",
        { opacity: 0.12 },
        {
          opacity: 1,
          stagger: 0.06,
          ease: "none",
          scrollTrigger: {
            trigger: ".fdx-reveal-copy",
            start: "top 82%",
            end: "bottom 46%",
            scrub: true,
          },
        },
      );
    });
    media.add("(min-width: 960px) and (prefers-reduced-motion: no-preference)", () => {
      ScrollTrigger.create({
        trigger: desire.current,
        pin: pin.current,
        start: "top 96px",
        end: "bottom bottom-=160",
        pinSpacing: false,
      });
    });
    return () => media.revert();
  }, { scope: root });

  const scrollToPrimitives = () => {
    document.querySelector("#component-primitives")?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  const changeTheme = (nextTheme) => {
    if (nextTheme === "day") {
      document.documentElement.dataset.theme = "day";
    } else {
      document.documentElement.removeAttribute("data-theme");
    }
    setTheme(nextTheme);
  };

  return (
    <main ref={root} className="fd-next overflow-x-hidden" data-density={mode}>
      <section className="fdx-hero">
        <PixelSky />
        <ThemeToggle theme={theme} onChange={changeTheme} />
        <div className="fdx-hero-copy">
          <p className="fdx-eyebrow">FlightDeck interface kit</p>
          <h2>
            Operational UI with
            <span className="fdx-inline-image" role="img" aria-label="signal waveform" />
            signal and restraint.
          </h2>
          <p className="fdx-lede">
            A token-driven component system for dense technical workflows, built to remain legible,
            responsive, and predictable in every product state.
          </p>
          <div className="fdx-hero-actions">
            <Button onClick={scrollToPrimitives} trailing={<ArrowIcon />}>Explore components</Button>
            <Button variant="secondary" onClick={() => setMode(mode === "dense" ? "default" : "dense")}>
              Toggle density
            </Button>
          </div>
        </div>
        <SignalConsole />
      </section>

      <MetricStrip
        items={[
          { label: "Components", value: "18", detail: "documented contracts" },
          { label: "New", value: "+7", detail: "workflow primitives" },
          { label: "States", value: "07", detail: "stable footprints" },
          { label: "Targets", value: "44", detail: "minimum CSS px" },
          { label: "Depth", value: "03", detail: "face, frame, offset" },
          { label: "Themes", value: "02", detail: "night and day" },
        ]}
      />

      <TokenMarquee
        items={["Satoshi", "Warm paper", "Ink frame", "Coral face", "Pink depth", "Orange depth", "Fast feedback", "Visible focus"]}
      />

      <ComponentCatalog />

      <section id="component-primitives" className="fdx-interest">
        <div className="fdx-section-heading">
          <p className="fdx-eyebrow">Reusable foundations</p>
          <h2>18 components. One grammar.</h2>
          <p>The catalog now covers actions, form controls, navigation, feedback, and dense operational data.</p>
        </div>
        <div className="fdx-bento">
          <SurfaceCard
            eyebrow="Action"
            title="Buttons communicate consequence"
            className="fdx-span-7"
            footer={<StateMatrix />}
          >
            <p>Every action separates its face, structural frame, and offset depth into independent semantic tokens.</p>
          </SurfaceCard>
          <SurfaceCard
            eyebrow="Input"
            title="Fields keep guidance attached"
            className="fdx-span-5"
          >
            <Field label="Repository" defaultValue="flight-deck.sh" hint="Use owner/repository format." />
            <Field label="Environment" defaultValue="production" error="This environment is read-only." />
          </SurfaceCard>
          <SurfaceCard eyebrow="Choice" title="Selection stays compact" className="fdx-span-4">
            <SegmentedControl label="Component density" items={controlModes} value={mode} onChange={setMode} />
            <p>Arrow keys move selection. Home and End jump to the boundaries.</p>
          </SurfaceCard>
          <SurfaceCard eyebrow="Status" title="Signals carry meaning" className="fdx-span-4">
            <div className="fdx-status-row">
              <StatusBadge tone="live" pulse>Live</StatusBadge>
              <StatusBadge tone="warning">Delayed</StatusBadge>
              <StatusBadge tone="critical">Failed</StatusBadge>
            </div>
            <p>Color is paired with a label and never acts as the only state cue.</p>
          </SurfaceCard>
          <SurfaceCard
            eyebrow="Surface"
            title="Interactive cards behave like controls"
            className="fdx-span-4"
            interactive
            onClick={() => setNoticeVisible(true)}
            footer={<span className="fdx-text-link">Open state details <ArrowIcon /></span>}
          >
            <p>The whole surface has one focus target, a clear action, and no nested buttons.</p>
          </SurfaceCard>
          <SurfaceCard
            eyebrow="Expanded input"
            title="Select and textarea preserve context"
            className="fdx-span-7"
          >
            <SelectField
              label="Deployment environment"
              options={environmentOptions}
              value={environment}
              onChange={(event) => setEnvironment(event.target.value)}
              hint="Selection remains native on touch and assistive technology."
            />
            <TextAreaField
              label="Release note"
              defaultValue="Cache telemetry now retains the last verified reading during reconnects."
              hint="Describe the operational change and recovery path."
            />
          </SurfaceCard>
          <SurfaceCard
            eyebrow="Boolean control"
            title="Toggles separate state from consent"
            className="fdx-span-5"
          >
            <div className="fdx-control-stack">
              <Toggle
                label="Autosave flight plan"
                description="Persist valid edits every 30 seconds."
                checked={autosave}
                onChange={setAutosave}
              />
              <CheckField
                label="Retain diagnostic logs"
                description="Keep local traces for seven days."
                checked={retainLogs}
                onChange={(event) => setRetainLogs(event.target.checked)}
              />
              <CheckField
                label="Remote write access"
                description="Locked by the active environment."
                disabled
              />
            </div>
          </SurfaceCard>
          <SurfaceCard
            eyebrow="Navigation"
            title="Tabs expose related views"
            className="fdx-span-7"
          >
            <Tabs
              label="Mission detail views"
              value={detailTab}
              onChange={setDetailTab}
              items={[
                {
                  label: "Overview",
                  value: "overview",
                  meta: "03",
                  content: (
                    <div className="fdx-tab-copy">
                      <strong>Mission state is nominal</strong>
                      <p>Three active checks are reporting within the expected response window.</p>
                    </div>
                  ),
                },
                {
                  label: "Timeline",
                  value: "timeline",
                  meta: "12",
                  content: (
                    <div className="fdx-tab-copy">
                      <strong>Latest event at 22:41</strong>
                      <p>The ingest worker reconciled two delayed usage records without data loss.</p>
                    </div>
                  ),
                },
                {
                  label: "Artifacts",
                  value: "artifacts",
                  meta: "08",
                  content: (
                    <div className="fdx-tab-copy">
                      <strong>Eight artifacts indexed</strong>
                      <p>Build output, audit snapshots, and operator notes are available locally.</p>
                    </div>
                  ),
                },
              ]}
            />
          </SurfaceCard>
          <SurfaceCard
            eyebrow="Feedback"
            title="Progress explains remaining work"
            className="fdx-span-5"
          >
            <div className="fdx-progress-stack">
              <ProgressBar label="Context budget" value={68} detail="132k tokens remain" />
              <ProgressBar label="Ingest queue" value={43} max={64} valueLabel="43 / 64" tone="warning" detail="21 records pending" />
              <ProgressBar label="Validation" value={100} tone="positive" detail="All required checks passed" />
            </div>
          </SurfaceCard>
          <SurfaceCard
            eyebrow="Dense data"
            title="Key-value rows stay scannable"
            className="fdx-span-12 fdx-card-compact"
          >
            <DataList
              label="Runtime configuration"
              items={[
                { label: "Frontend", value: "127.0.0.1:5190", detail: "available", tone: "live", status: "Ready" },
                { label: "Backend", value: "127.0.0.1:8010", detail: "local API", tone: "live", status: "Live" },
                { label: "Environment", value: environment, detail: "selected above", tone: "neutral", status: "Local" },
                { label: "Retention", value: retainLogs ? "7 days" : "Off", detail: "diagnostic traces", tone: retainLogs ? "warning" : "neutral", status: retainLogs ? "Enabled" : "Disabled" },
              ]}
            />
          </SurfaceCard>
        </div>
      </section>

      <section className="fdx-statement">
        <p className="fdx-reveal-copy">
          {"Interfaces should explain themselves before motion, color, or decoration enters the room."
            .split(" ")
            .map((word, index) => <span className="fdx-reveal-word" key={`${word}-${index}`}>{word} </span>)}
        </p>
      </section>

      <section ref={desire} className="fdx-desire">
        <div ref={pin} className="fdx-pin">
          <p className="fdx-eyebrow">Interaction language</p>
          <h2>One system, every state.</h2>
          <p>Explore the same rules across actions, notices, empty states, and expanding content.</p>
        </div>
        <div className="fdx-showcase-stack">
          <section className="fdx-showcase-panel">
            <div className="fdx-panel-title">
              <span>State handling</span>
              <IconButton label="Dismiss state example" onClick={() => setNoticeVisible(false)}>
                <CloseIcon />
              </IconButton>
            </div>
            {noticeVisible ? (
              <Notice
                title="Connection interrupted"
                tone="critical"
                action={<Button size="sm" error>Reconnect</Button>}
              >
                Existing data remains visible while FlightDeck retries.
              </Notice>
            ) : (
              <EmptyState
                title="Notice dismissed"
                action={<Button size="sm" variant="secondary" onClick={() => setNoticeVisible(true)}>Restore notice</Button>}
              >
                The content area keeps its structure and offers a reversible next action.
              </EmptyState>
            )}
          </section>

          <section className="fdx-showcase-panel">
            <div className="fdx-panel-title">
              <span>Horizontal disclosure</span>
              <StatusBadge tone="neutral">Keyboard ready</StatusBadge>
            </div>
            <HorizontalAccordion items={accordionItems} value={accordion} onChange={setAccordion} />
          </section>

          <section id="pixel-airspace-demo" className="fdx-showcase-panel fdx-pixel-showcase">
            <div className="fdx-panel-title">
              <span>Monochrome pixel airspace</span>
              <Button
                size="sm"
                variant="secondary"
                aria-pressed={pixelMotionPaused}
                onClick={() => setPixelMotionPaused((current) => !current)}
              >
                {pixelMotionPaused ? "Play motion" : "Pause motion"}
              </Button>
            </div>
            <div className="fdx-pixel-demo">
              <div className="fdx-pixel-copy">
                <p className="fdx-eyebrow">Experimental ambient state</p>
                <strong>Quiet, not inactive.</strong>
                <p>
                  A stepped aircraft drift, scan line, and asynchronous beacons add low-frequency
                  activity without introducing another accent color.
                </p>
                <dl className="fdx-pixel-spec">
                  <div><dt>Palette</dt><dd>currentColor</dd></div>
                  <div><dt>Motion</dt><dd>transform / opacity</dd></div>
                  <div><dt>Fallback</dt><dd>static frame</dd></div>
                </dl>
              </div>
              <PixelAirspace paused={pixelMotionPaused} />
            </div>
          </section>

          <section className="fdx-showcase-panel">
            <div className="fdx-panel-title">
              <span>Empty and loading</span>
              <span className="fdx-eyebrow">Stable footprint</span>
            </div>
            <div className="fdx-empty-grid">
              <EmptyState
                title="No missions queued"
                action={<Button size="sm">Create mission</Button>}
              >
                Create a mission or change the active filter.
              </EmptyState>
              <EmptyState loading title="Loading" />
            </div>
          </section>
        </div>
      </section>

      <section className="fdx-action">
        <div>
          <p className="fdx-eyebrow">Ready for product work</p>
          <h2>Use the system. Keep the signal clear.</h2>
        </div>
        <div>
          <p>
            Import the primitives from <code>ui/FlightComponents.jsx</code>. Product teams must use
            semantic tokens and should extend behavior through props before adding local styles.
          </p>
          <Button variant="inverse" onClick={scrollToPrimitives} trailing={<ArrowIcon />}>Review primitives</Button>
        </div>
      </section>
    </main>
  );
}

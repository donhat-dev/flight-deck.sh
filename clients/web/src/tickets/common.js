// Shared vocabulary for the board and the form. Tailwind families here follow
// the app remap in tailwind.config.js: emerald IS the coral accent, zinc is the
// neutral ramp, amber/rose/sky/violet stay semantic.
export const BATON = {
  MINE: "border-emerald-500 bg-emerald-500 text-zinc-950",
  PM: "border-amber-400/70 text-amber-300",
  LEAD: "border-sky-400/70 text-sky-300",
  QA: "border-violet-400/70 text-violet-300",
  AGENT: "border-zinc-600 text-zinc-400",
};

export const STEP_STATUS = {
  done: { dot: "bg-teal-400", text: "text-teal-300", label: "DONE" },
  doing: { dot: "bg-emerald-500", text: "text-emerald-400", label: "DOING" },
  planned: { dot: "bg-zinc-600", text: "text-zinc-500", label: "PLANNED" },
  dropped: { dot: "bg-zinc-700", text: "text-zinc-600", label: "DROPPED" },
};

export const CHILD_KIND = {
  TODO: "border-zinc-600 text-zinc-400",
  FIX: "border-rose-400/70 text-rose-300",
  BUG: "border-rose-400/70 text-rose-300",
  AMEND: "border-amber-400/70 text-amber-300",
  RERUN: "border-sky-400/70 text-sky-300",
};

// A card that has not moved in days is the board's real signal, so the age
// carries colour of its own rather than sitting in the metadata ramp.
export function ageClass(days) {
  if (days >= 8) return "text-rose-400";
  if (days >= 5) return "text-amber-400";
  return "text-zinc-600";
}

export const goTicket = (key) => {
  window.location.hash = `#/ticket/${encodeURIComponent(key)}`;
  window.scrollTo(0, 0);
};

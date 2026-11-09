// "Right now" dashboard: crowd level of every room, from GET /api/occupancy.
// Rooms stay in venue order so rows don't jump around on refresh.
const DASH_REFRESH_MS = 10000;
const LEVELS = {
  low: { label: "Quiet", icon: "✓" },
  medium: { label: "Filling up", icon: "◐" },
  high: { label: "Busy", icon: "▲" },
  full: { label: "Full", icon: "■" },
  unknown: { label: "No data", icon: "?" },
};
const SUMMARY_ORDER = ["low", "medium", "high", "full"];

const dashList = document.getElementById("rooms-now");
const dashSummary = document.getElementById("rooms-summary");
const dashNote = document.getElementById("rooms-note");

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function ago(seconds) {
  if (seconds < 60) return `${Math.max(0, Math.round(seconds))}s ago`;
  return `${Math.round(seconds / 60)} min ago`;
}

function levelOf(area) {
  return area.level in LEVELS ? area.level : "unknown";
}

function roomRow(area, now) {
  const level = levelOf(area);
  const { label, icon } = LEVELS[level];
  const row = el("li", `room room-${level}`);

  const head = el("div", "room-head");
  const badge = el("span", `badge badge-${level}`);
  badge.append(el("span", "badge-icon", icon), document.createTextNode(` ${label}`));
  badge.firstChild.setAttribute("aria-hidden", "true");
  head.append(el("span", "room-name", area.name), badge);
  row.append(head);

  const known = area.count !== null && area.count !== undefined && level !== "unknown";
  // Floor, not round: 99.6% must not read "100% full" next to a "Busy" badge
  // (the level only turns Full at 100%).
  const pct = known ? Math.floor(area.ratio * 100) : 0;
  const meter = el("div", "meter");
  meter.setAttribute("role", "meter");
  meter.setAttribute("aria-label", `${area.name} occupancy`);
  meter.setAttribute("aria-valuemin", "0");
  meter.setAttribute("aria-valuemax", String(area.capacity));
  if (known) {
    meter.setAttribute("aria-valuenow", String(area.count));
    meter.setAttribute("aria-valuetext", `${area.count} of ${area.capacity} people, ${label.toLowerCase()}`);
  } else {
    meter.setAttribute("aria-valuetext", "no recent data");
  }
  const fill = el("div", "meter-fill");
  // Over capacity still fills the track; the number below says by how much.
  fill.style.width = `${Math.min(pct, 100)}%`;
  meter.append(fill);
  row.append(meter);

  let detail;
  if (known) {
    detail = `${area.count} of ${area.capacity} people · ${pct}% full`;
    if (area.updated) detail += ` · updated ${ago(now - area.updated)}`;
  } else if (area.updated) {
    detail = `No recent reading (last ${ago(now - area.updated)})`;
  } else {
    detail = "No camera reading yet";
  }
  row.append(el("p", "room-detail", detail));
  return row;
}

function summary(areas) {
  const counts = {};
  for (const a of areas) counts[levelOf(a)] = (counts[levelOf(a)] || 0) + 1;
  const parts = SUMMARY_ORDER.filter((l) => counts[l]).map((l) => `${counts[l]} ${LEVELS[l].label.toLowerCase()}`);
  return parts.length ? parts.join(" · ") : "Waiting for crowd data";
}

async function refreshRooms() {
  try {
    const response = await fetch("/api/occupancy");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    const now = data.generated_at || Date.now() / 1000;
    dashList.replaceChildren(...data.areas.map((a) => roomRow(a, now)));
    dashSummary.textContent = summary(data.areas);
    const unavailable = data.source !== "ok";
    dashNote.textContent = unavailable ? "Live crowd data is unavailable right now." : "";
    dashNote.hidden = !unavailable;
  } catch {
    dashNote.textContent = "Couldn't load crowd levels. Retrying…";
    dashNote.hidden = false;
  }
}

refreshRooms();
setInterval(refreshRooms, DASH_REFRESH_MS);

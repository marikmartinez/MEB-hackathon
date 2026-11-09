// "Right now" dashboard: crowd level of every room, from GET /api/occupancy.
// Click a room to open its camera feed: the counter's latest frame with the
// people it detected boxed, refreshed every few seconds.
// Rows are built once and updated in place, so an open feed survives refreshes
// and rows never jump around.
const DASH_REFRESH_MS = 10000;
const FEED_REFRESH_MS = 5000;
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

const rows = new Map(); // area id -> row parts
let lastNow = Date.now() / 1000;
let deepLinkDone = false;

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

function buildRow(area) {
  const li = el("li", "room");
  const panelId = `feed-${area.id}`;

  const toggle = el("button", "room-toggle");
  toggle.type = "button";
  toggle.setAttribute("aria-expanded", "false");
  toggle.setAttribute("aria-controls", panelId);
  const head = el("span", "room-head");
  const name = el("span", "room-name", area.name);
  const badge = el("span", "badge");
  head.append(name, badge);
  const meter = el("span", "meter");
  meter.setAttribute("role", "meter");
  meter.setAttribute("aria-label", `${area.name} occupancy`);
  meter.setAttribute("aria-valuemin", "0");
  const fill = el("span", "meter-fill");
  meter.append(fill);
  const detail = el("span", "room-detail");
  const chevron = el("span", "chevron", "▾");
  chevron.setAttribute("aria-hidden", "true");
  toggle.append(head, meter, detail, chevron);

  const panel = el("div", "feed");
  panel.id = panelId;
  panel.hidden = true;
  const img = el("img", "feed-img");
  img.alt = `Latest camera frame from ${area.name}, people the counter detected are boxed`;
  img.width = 640;
  img.height = 480;
  const caption = el("p", "feed-caption");
  panel.append(img, caption);

  li.append(toggle, panel);
  const row = { li, toggle, badge, meter, fill, detail, panel, img, caption, area, timer: null, loaded: false };
  toggle.addEventListener("click", () => setOpen(row, toggle.getAttribute("aria-expanded") !== "true"));
  return row;
}

function updateRow(row, area, now) {
  row.area = area;
  const level = levelOf(area);
  const { label, icon } = LEVELS[level];
  row.li.className = `room room-${level}`;

  row.badge.className = `badge badge-${level}`;
  const iconSpan = el("span", "badge-icon", icon);
  iconSpan.setAttribute("aria-hidden", "true");
  row.badge.replaceChildren(iconSpan, document.createTextNode(` ${label}`));

  const known = area.count !== null && area.count !== undefined && level !== "unknown";
  // Floor, not round: 99.6% must not read "100% full" next to a "Busy" badge
  // (the level only turns Full at 100%).
  const pct = known ? Math.floor(area.ratio * 100) : 0;
  row.meter.setAttribute("aria-valuemax", String(area.capacity));
  if (known) {
    row.meter.setAttribute("aria-valuenow", String(area.count));
    row.meter.setAttribute("aria-valuetext", `${area.count} of ${area.capacity} people, ${label.toLowerCase()}`);
  } else {
    row.meter.removeAttribute("aria-valuenow");
    row.meter.setAttribute("aria-valuetext", "no recent data");
  }
  // Over capacity still fills the track; the number below says by how much.
  row.fill.style.width = `${Math.min(pct, 100)}%`;

  let text;
  if (known) {
    text = `${area.count} of ${area.capacity} people · ${pct}% full`;
    if (area.updated) text += ` · updated ${ago(now - area.updated)}`;
  } else if (area.updated) {
    text = `No recent reading (last ${ago(now - area.updated)})`;
  } else {
    text = "No camera reading yet";
  }
  row.detail.textContent = text;
}

// Feed: preload each frame off-screen and swap it in, so the image never
// blanks between refreshes.
function loadFrame(row) {
  if (document.hidden) return; // don't pull frames for a tab nobody's looking at
  const next = new Image();
  next.onload = () => {
    row.img.src = next.src;
    row.loaded = true;
    row.img.hidden = false;
    // The number drawn on the image is this one frame; the count above is
    // smoothed over recent frames, so the two can differ by a few.
    row.caption.textContent = `Camera snapshot · refreshes every ${FEED_REFRESH_MS / 1000}s · the count above is smoothed over recent frames`;
  };
  next.onerror = () => {
    if (!row.loaded) {
      row.img.hidden = true;
      row.caption.textContent = "No camera image for this room yet.";
    }
  };
  next.src = `/api/occupancy/${encodeURIComponent(row.area.id)}/frame.jpg?t=${Date.now()}`;
}

function setOpen(row, open) {
  row.toggle.setAttribute("aria-expanded", String(open));
  row.panel.hidden = !open;
  row.li.classList.toggle("open", open);
  clearInterval(row.timer);
  row.timer = null;
  if (open) {
    if (!row.loaded) row.caption.textContent = "Loading camera…";
    loadFrame(row);
    row.timer = setInterval(() => loadFrame(row), FEED_REFRESH_MS);
  }
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
    lastNow = data.generated_at || Date.now() / 1000;
    for (const area of data.areas) {
      let row = rows.get(area.id);
      if (!row) {
        row = buildRow(area);
        rows.set(area.id, row);
        dashList.append(row.li);
      }
      updateRow(row, area, lastNow);
    }
    if (!deepLinkDone) {
      // /#room-hall-a opens that room's feed on load.
      deepLinkDone = true;
      if (location.hash.startsWith("#room-")) {
        let id = null;
        try { id = decodeURIComponent(location.hash.slice(6)); } catch { /* bad hash */ }
        const wanted = id && rows.get(id);
        if (wanted) {
          setOpen(wanted, true);
          wanted.li.scrollIntoView({ block: "nearest" });
        }
      }
    }
    dashSummary.textContent = summary(data.areas);
    const unavailable = data.source !== "ok";
    dashNote.textContent = unavailable ? "Live crowd data is unavailable right now." : "";
    dashNote.hidden = !unavailable;
  } catch {
    dashNote.textContent = "Couldn't load crowd levels. Retrying…";
    dashNote.hidden = false;
  }
}

// When the tab comes back, show open feeds' latest frame straight away.
document.addEventListener("visibilitychange", () => {
  if (document.hidden) return;
  for (const row of rows.values()) if (row.timer) loadFrame(row);
});

refreshRooms();
setInterval(refreshRooms, DASH_REFRESH_MS);

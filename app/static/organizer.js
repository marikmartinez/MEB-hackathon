// Organizer dashboard.
//   GET /api/occupancy           live levels, straight from the counter
//   GET /api/organizer/history   per-room series from Elasticsearch
//   GET /api/organizer/talks     occupancy during each talk's slot
// One small chart per room on shared scales, with each talk's slot shaded,
// so rooms compare at a glance and a spike lines up with the talk that caused it.
// All text from the API goes in with textContent.

const LIVE_MS = 10000;
const SLOW_MS = 60000;
const LEVELS = {
  low: { label: "Quiet", icon: "✓" },
  medium: { label: "Filling up", icon: "◐" },
  high: { label: "Busy", icon: "▲" },
  full: { label: "Full", icon: "■" },
  unknown: { label: "No data", icon: "?" },
};
const SVGNS = "http://www.w3.org/2000/svg";
// Chart geometry in CSS pixels. The viewBox width is the panel's measured
// width, so text stays at its real size instead of scaling with the panel.
const H = 170, M = { top: 20, right: 10, bottom: 22, left: 34 };
const PH = H - M.top - M.bottom;
const PANEL_PADDING = 26; // .panel horizontal padding + border

const state = { range: "day", history: null, talks: null, live: null, tableView: false };
const $ = (id) => document.getElementById(id);
const tooltip = $("tooltip");

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function svg(tag, attrs = {}, text) {
  const node = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (text !== undefined) node.textContent = text;
  return node;
}

const timeFmt = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" });
const hhmm = (ms) => timeFmt.format(new Date(ms));
const pct = (ratio) => `${Math.round(ratio * 100)}%`;
const levelOf = (level) => (level in LEVELS ? level : "unknown");

function badge(level) {
  const lv = levelOf(level);
  const b = el("span", `badge badge-${lv}`);
  const icon = el("span", "badge-icon", LEVELS[lv].icon);
  icon.setAttribute("aria-hidden", "true");
  b.append(icon, document.createTextNode(` ${LEVELS[lv].label}`));
  return b;
}

async function getJSON(url) {
  const resp = await fetch(url, { cache: "no-store" });
  if (!resp.ok) throw new Error(`${url}: ${resp.status}`);
  return resp.json();
}

function sourceNote() {
  const note = $("source-note");
  const down = [state.history, state.talks].some((d) => d && d.source !== "elasticsearch");
  note.hidden = !down;
  note.textContent = down
    ? "Elasticsearch isn't answering, so history and talk averages are missing. The live numbers still come from the counter."
    : "";
}

// ---------- Right now ----------

function renderTiles() {
  const areas = (state.live && state.live.areas) || [];
  const known = areas.filter((a) => a.level !== "unknown" && a.count !== null);
  $("tile-people").textContent = known.length ? known.reduce((n, a) => n + a.count, 0).toLocaleString() : "–";
  $("tile-people-sub").textContent = known.length
    ? `across ${known.length} of ${areas.length} rooms`
    : "no live readings";

  const busy = known.filter((a) => a.level === "high" || a.level === "full");
  $("tile-busy").textContent = known.length ? `${busy.length} of ${areas.length}` : "–";
  $("tile-busy-sub").textContent = busy.length ? busy.map((a) => a.name).join(", ") : known.length ? "every room has space" : "";

  const byRoom = Object.fromEntries(known.map((a) => [a.id, a]));
  const live = ((state.talks && state.talks.talks) || [])
    .filter((t) => t.status === "live" && byRoom[t.room])
    .sort((a, b) => byRoom[b.room].ratio - byRoom[a.room].ratio);
  if (live.length) {
    const t = live[0], a = byRoom[t.room];
    $("tile-talk").textContent = t.title;
    $("tile-talk-sub").textContent = `${t.room_name} · ${pct(a.ratio)} · ${a.count} of ${a.capacity} people`;
  } else {
    $("tile-talk").textContent = "–";
    $("tile-talk-sub").textContent = "no talk with a live reading";
  }
}

// ---------- Rooms over time ----------

function xTicks(start, end) {
  const span = end - start;
  const step = span <= 90 * 60e3 ? 15 * 60e3 : span <= 5 * 3600e3 ? 3600e3 : 2 * 3600e3;
  const ticks = [];
  for (let t = Math.ceil(start / step) * step; t <= end; t += step) ticks.push(t);
  return ticks;
}

function yMaxFor(rooms) {
  let max = 1;
  for (const r of rooms) for (const p of r.points) max = Math.max(max, p.ratio ?? 0);
  return Math.min(1.5, Math.ceil(max * 4) / 4);
}

// Split where readings stop, so a gap reads as "no data", not a straight line.
function segments(points, intervalMs) {
  const out = [];
  let cur = [];
  for (const p of points) {
    if (p.ratio === null) continue;
    if (cur.length && p.t - cur[cur.length - 1].t > intervalMs * 2.5) {
      out.push(cur);
      cur = [];
    }
    cur.push(p);
  }
  if (cur.length) out.push(cur);
  return out;
}

function talkAt(roomId, t) {
  return ((state.talks && state.talks.talks) || []).find(
    (k) => k.room === roomId && Date.parse(k.start) <= t && t < Date.parse(k.end),
  );
}

function buildPanel(room, h, yMax, W) {
  const PW = W - M.left - M.right;
  const { start, end } = h;
  const intervalMs = (h.interval || 60) * 1000;
  const x = (t) => M.left + ((t - start) / Math.max(end - start, 1)) * PW;
  const y = (r) => M.top + PH - (Math.min(r, yMax) / yMax) * PH;

  const panel = el("article", "panel");
  panel.tabIndex = 0;
  const head = el("div", "panel-head");
  head.append(el("h3", "panel-name", room.name));
  const pts = room.points.filter((p) => p.ratio !== null);
  const last = pts[pts.length - 1];
  const peak = pts.reduce((m, p) => (p.peak > (m ? m.peak : -1) ? p : m), null);
  const meta = el(
    "p",
    "panel-meta",
    pts.length
      ? `latest ${pct(last.ratio)} · peak ${pct(peak.peak)} at ${hhmm(peak.t)} · cap ${room.capacity}`
      : `capacity ${room.capacity}`,
  );
  head.append(meta);
  panel.append(head);

  const chart = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img" });
  chart.setAttribute(
    "aria-label",
    pts.length
      ? `${room.name}: ${pts.length} readings from ${hhmm(pts[0].t)} to ${hhmm(last.t)}, latest ${pct(last.ratio)} of capacity, peak ${pct(peak.peak)}.`
      : `${room.name}: no readings in this range.`,
  );

  // Talk slots in this room, behind everything.
  for (const t of (state.talks && state.talks.talks) || []) {
    if (t.room !== room.id) continue;
    const s = Math.max(Date.parse(t.start), start), e = Math.min(Date.parse(t.end), end);
    if (e <= s) continue;
    const bx = x(s), bw = x(e) - bx;
    chart.append(svg("rect", { class: `band${t.status === "live" ? " live" : ""}`, x: bx, y: M.top, width: bw, height: PH }));
    // ~6.2 px per character at 10.5px, with room to spare before the next band.
    const fit = Math.floor((bw - 10) / 6.2);
    if (fit >= 4) {
      const label = t.title.length > fit ? `${t.title.slice(0, fit - 1)}…` : t.title;
      chart.append(svg("text", { class: "band-label", x: bx + 3, y: M.top - 6 }, label));
    }
  }

  // Grid and axes: hairlines, 100% of capacity a step stronger.
  for (const r of [0, 0.5, 1, 1.5].filter((r) => r <= yMax)) {
    chart.append(svg("line", { class: r === 1 ? "capacity-line" : "grid-line", x1: M.left, x2: W - M.right, y1: y(r), y2: y(r) }));
    chart.append(svg("text", { class: "axis-label", x: M.left - 6, y: y(r) + 4, "text-anchor": "end" }, r === 1 ? "cap" : pct(r)));
  }
  for (const t of xTicks(start, end)) {
    chart.append(svg("text", { class: "axis-label", x: x(t), y: H - 6, "text-anchor": "middle" }, hhmm(t)));
  }

  if (!pts.length) {
    chart.append(svg("text", { class: "empty", x: M.left + PW / 2, y: M.top + PH / 2, "text-anchor": "middle" }, "No readings in this range"));
  }
  for (const seg of segments(room.points, intervalMs)) {
    if (seg.length === 1) {
      chart.append(svg("circle", { class: "series-dot", cx: x(seg[0].t), cy: y(seg[0].ratio), r: 3 }));
      continue;
    }
    const d = seg.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)},${y(p.ratio).toFixed(1)}`).join("");
    chart.append(svg("path", { class: "series-line", d }));
  }
  if (last) chart.append(svg("circle", { class: "series-dot", cx: x(last.t), cy: y(last.ratio), r: 4 }));

  // Hover/focus layer: crosshair snaps to the nearest reading.
  const cross = svg("line", { class: "crosshair", y1: M.top, y2: M.top + PH, visibility: "hidden" });
  const dot = svg("circle", { class: "series-dot", r: 4.5, visibility: "hidden" });
  chart.append(cross, dot);
  panel.append(chart);

  let idx = -1;
  const show = (i, clientX, clientY) => {
    if (!pts.length) return;
    idx = Math.max(0, Math.min(pts.length - 1, i));
    const p = pts[idx];
    cross.setAttribute("x1", x(p.t));
    cross.setAttribute("x2", x(p.t));
    dot.setAttribute("cx", x(p.t));
    dot.setAttribute("cy", y(p.ratio));
    cross.setAttribute("visibility", "visible");
    dot.setAttribute("visibility", "visible");

    const talk = talkAt(room.id, p.t);
    const strong = el("strong", null, `${pct(p.ratio)} of capacity`);
    const line1 = el("div");
    line1.append(el("span", "key"), document.createTextNode(`${room.name} · ${hhmm(p.t)}`));
    const line2 = el("div", "sub", `about ${Math.round(p.people)} of ${room.capacity} people`);
    tooltip.replaceChildren(strong, line1, line2);
    if (talk) tooltip.append(el("div", "sub", `During: ${talk.title}`));
    tooltip.hidden = false;
    const box = tooltip.getBoundingClientRect();
    const left = Math.min(clientX + 14, window.innerWidth - box.width - 8);
    const top = Math.min(clientY + 14, window.innerHeight - box.height - 8);
    tooltip.style.left = `${Math.max(8, left)}px`;
    tooltip.style.top = `${Math.max(8, top)}px`;
  };
  const hide = () => {
    cross.setAttribute("visibility", "hidden");
    dot.setAttribute("visibility", "hidden");
    tooltip.hidden = true;
  };
  const nearest = (clientX) => {
    const rect = chart.getBoundingClientRect();
    const vx = ((clientX - rect.left) / rect.width) * W;
    const t = start + ((vx - M.left) / PW) * (end - start);
    let best = 0;
    for (let i = 1; i < pts.length; i++) if (Math.abs(pts[i].t - t) < Math.abs(pts[best].t - t)) best = i;
    return best;
  };
  chart.addEventListener("pointermove", (e) => show(nearest(e.clientX), e.clientX, e.clientY));
  chart.addEventListener("pointerleave", hide);
  panel.addEventListener("blur", hide);
  panel.addEventListener("keydown", (e) => {
    if (!pts.length || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return;
    e.preventDefault();
    const next = e.key === "Home" ? 0 : e.key === "End" ? pts.length - 1
      : (idx < 0 ? pts.length - 1 : idx + (e.key === "ArrowRight" ? 1 : -1));
    const rect = chart.getBoundingClientRect();
    show(next, rect.left + (x(pts[Math.max(0, Math.min(pts.length - 1, next))].t) / W) * rect.width, rect.top + 8);
  });
  return panel;
}

function renderHistory() {
  const h = state.history;
  const box = $("history");
  box.classList.remove("loading");
  if (!h) return;
  const yMax = yMaxFor(h.rooms);
  const cols = getComputedStyle(box).gridTemplateColumns.split(" ").length || 1;
  const gap = parseFloat(getComputedStyle(box).columnGap) || 0;
  const width = Math.max(240, Math.floor((box.clientWidth - gap * (cols - 1)) / cols - PANEL_PADDING));
  box.replaceChildren(...h.rooms.map((r) => buildPanel(r, h, yMax, width)));

  const readings = h.rooms.reduce((n, r) => n + r.points.length, 0);
  const every = h.interval ? (h.interval >= 120 ? `${h.interval / 60} min` : `${h.interval} s`) : null;
  $("history-summary").textContent = h.source !== "elasticsearch"
    ? "No history: Elasticsearch isn't answering."
    : readings
      ? `${hhmm(h.start)}–${hhmm(h.end)} · one point per ${every} · shaded bands are talks, darker while on now · hover or use arrow keys for values`
      : `No readings between ${hhmm(h.start)} and ${hhmm(h.end)} yet. The counter adds one per room every 30 seconds.`;
  renderHistoryTable();
}

function renderHistoryTable() {
  const h = state.history;
  const wrap = $("history-table");
  if (!h || !state.tableView) return;
  const times = [...new Set(h.rooms.flatMap((r) => r.points.map((p) => p.t)))].sort((a, b) => b - a);
  const byRoom = h.rooms.map((r) => new Map(r.points.map((p) => [p.t, p])));
  const table = el("table");
  const head = el("tr");
  head.append(el("th", null, "Time"), ...h.rooms.map((r) => {
    const th = el("th", "num", `${r.name} (cap ${r.capacity})`);
    th.scope = "col";
    return th;
  }));
  table.append(el("thead"));
  table.tHead.append(head);
  const body = el("tbody");
  for (const t of times) {
    const tr = el("tr");
    tr.append(el("td", "time", hhmm(t)));
    byRoom.forEach((m) => {
      const p = m.get(t);
      tr.append(el("td", "num", p && p.ratio !== null ? `${pct(p.ratio)} (${Math.round(p.people)})` : "–"));
    });
    body.append(tr);
  }
  if (!times.length) {
    const tr = el("tr");
    const td = el("td", "muted", "No readings in this range.");
    td.colSpan = h.rooms.length + 1;
    tr.append(td);
    body.append(tr);
  }
  table.append(body);
  wrap.replaceChildren(table);
}

// ---------- Talks ----------

function renderTalks() {
  const data = state.talks;
  const body = $("talks");
  body.parentElement.classList.remove("loading");
  if (!data) return;
  const rows = data.talks.map((t) => {
    const tr = el("tr", t.status === "live" ? "live" : "");
    tr.append(el("td", "time", `${hhmm(Date.parse(t.start))}–${hhmm(Date.parse(t.end))}`));
    const talk = el("td");
    talk.append(el("div", "talk-title", t.title), el("div", "talk-speaker", t.speaker));
    tr.append(talk, el("td", null, t.room_name));

    const status = el("td", `status status-${t.status}`);
    status.textContent = t.status === "live" ? "● On now" : t.status === "ended" ? "Ended" : "Upcoming";
    tr.append(status);

    const avg = el("td", "num");
    const peak = el("td", "num peak-cell");
    if (t.status === "upcoming") {
      avg.append(el("span", "muted", "–"));
      peak.append(el("span", "muted", "–"));
    } else if (!t.readings || t.avg_ratio === null) {
      avg.append(el("span", "muted", "No readings"));
      peak.append(el("span", "muted", "–"));
    } else {
      const meter = el("span", "cell-meter");
      meter.setAttribute("aria-hidden", "true");
      const fill = el("span");
      fill.style.width = `${Math.min(100, t.avg_ratio * 100)}%`;
      meter.append(fill);
      avg.append(meter, document.createTextNode(`${pct(t.avg_ratio)} · ${Math.round(t.avg_people)} ppl`));
      peak.append(document.createTextNode(`${pct(t.peak_ratio)} · ${t.peak_people}/${t.capacity}`), badge(t.peak_level));
    }
    tr.append(avg, peak);
    return tr;
  });
  body.replaceChildren(...rows);

  const live = data.talks.filter((t) => t.status === "live").length;
  const measured = data.talks.filter((t) => t.readings).length;
  $("talks-summary").textContent = `${data.talks.length} talks · ${live} on now · ${measured} with readings`;
}

// ---------- Loading ----------

async function loadLive() {
  try {
    state.live = await getJSON("/api/occupancy");
  } catch {
    state.live = null;
  }
  renderTiles();
}

async function loadTalks() {
  $("talks").parentElement.classList.add("loading");
  try {
    state.talks = await getJSON("/api/organizer/talks");
  } catch {
    /* keep the last render */
  }
  renderTalks();
  renderTiles();
  sourceNote();
}

async function loadHistory() {
  $("history").classList.add("loading");
  const range = state.range;
  try {
    const data = await getJSON(`/api/organizer/history?range=${encodeURIComponent(range)}`);
    if (range === state.range) state.history = data;
  } catch {
    /* keep the last render */
  }
  renderHistory();
  sourceNote();
}

for (const btn of document.querySelectorAll(".segmented button")) {
  btn.addEventListener("click", () => {
    state.range = btn.dataset.range;
    for (const b of document.querySelectorAll(".segmented button")) b.setAttribute("aria-checked", String(b === btn));
    loadHistory();
  });
}

$("history-view").addEventListener("click", (e) => {
  state.tableView = !state.tableView;
  e.currentTarget.setAttribute("aria-pressed", String(state.tableView));
  e.currentTarget.textContent = state.tableView ? "Show as charts" : "Show as table";
  $("history").hidden = state.tableView;
  $("history-table").hidden = !state.tableView;
  // Charts drawn while hidden were measured at zero width.
  if (state.tableView) renderHistoryTable();
  else renderHistory();
});

let resizeTimer = null;
let lastWidth = $("history").clientWidth;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    const w = $("history").clientWidth;
    if (w !== lastWidth && !state.tableView) {
      lastWidth = w;
      renderHistory();
    }
  }, 150);
});

// Talks first: the charts shade talk slots from it.
loadTalks().then(loadHistory);
loadLive();
setInterval(loadLive, LIVE_MS);
setInterval(() => loadTalks().then(loadHistory), SLOW_MS);

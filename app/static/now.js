// Simulated conference clock, and what's happening in each room. Adds to the
// room rows that dashboard.js builds (rows, el and LEVELS come from there).
const NOW_REFRESH_MS = 3000;
const clockTime = document.getElementById("sim-clock-time");

let serverMinutes = null;
let endMinutes = null;
let receivedAt = 0;
let simSpeed = 60;

const hhmm = (timestamp) => timestamp.slice(11, 16);

function minutesOf(timestamp) {
  const [hours, minutes, seconds] = timestamp.slice(11, 19).split(":").map(Number);
  return hours * 60 + minutes + seconds / 60;
}

function drawClock() {
  if (serverMinutes === null) return;
  const elapsedMinutes = ((performance.now() - receivedAt) / 60000) * simSpeed;
  const minutes = Math.floor(Math.min(serverMinutes + elapsedMinutes, endMinutes));
  const pad = (n) => String(n).padStart(2, "0");
  clockTime.textContent = `${pad(Math.floor(minutes / 60))}:${pad(minutes % 60)}`;
}

function talkLine(state) {
  if (state.talk) {
    const line = el("p", "room-talk");
    line.append(
      el("span", "room-talk-label", "Now: "),
      el("strong", "", state.talk.title),
      el("span", "room-talk-meta", ` · until ${hhmm(state.talk.end)} · ${state.talk.speaker}`),
    );
    return line;
  }
  if (state.next) {
    return el("p", "room-talk room-talk-idle", `Between talks · next at ${hhmm(state.next.start)}: ${state.next.title}`);
  }
  return el("p", "room-talk room-talk-idle", "No more talks in this room today");
}

function suggestionBox(state) {
  const box = el("div", "room-suggest");
  box.append(el("p", "room-suggest-title", `${state.name} is crowded. Most similar talk with space:`));
  const suggestion = state.suggestion;
  if (!suggestion) {
    box.append(el("p", "room-suggest-why", "No less crowded talks are running right now."));
    return box;
  }
  const { label } = LEVELS[suggestion.crowd_level] || LEVELS.unknown;
  const roomLabel = (rows.get(suggestion.room) || { area: { name: suggestion.room } }).area.name;
  box.append(
    el("p", "room-suggest-talk", suggestion.title),
    el("p", "room-suggest-why", `${roomLabel} · until ${hhmm(suggestion.end)} · ${label}`),
  );
  if (suggestion.shared_tags.length) {
    box.append(el("p", "room-suggest-why", `Also about ${suggestion.shared_tags.join(", ")}`));
  }
  return box;
}

function renderRoomNow(state) {
  const row = rows.get(state.id);
  if (!row) return; // dashboard.js hasn't built this row yet; the next poll will
  let holder = row.li.querySelector(".room-now");
  if (!holder) {
    holder = el("div", "room-now");
    row.panel.before(holder);
  }
  const parts = [talkLine(state)];
  if (state.talk && (state.crowd_level === "high" || state.crowd_level === "full")) {
    parts.push(suggestionBox(state));
  }
  holder.replaceChildren(...parts);
}

async function refreshNow() {
  try {
    const response = await fetch("/api/now");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    if (!data.now) return;
    serverMinutes = minutesOf(data.now);
    endMinutes = minutesOf(data.end);
    receivedAt = performance.now();
    simSpeed = data.speed;
    drawClock();
    data.rooms.forEach(renderRoomNow);
  } catch {
    // Keep showing the last state; the next poll retries.
  }
}

refreshNow();
setInterval(refreshNow, NOW_REFRESH_MS);
setInterval(drawClock, 500);

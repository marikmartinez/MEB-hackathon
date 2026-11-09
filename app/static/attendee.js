const REFRESH_MS = 5000;
const CROWD_RANK = { low: 0, medium: 1, high: 2, full: 3 };
const CROWD_LABEL = {
  low: "Quiet",
  medium: "Filling up",
  high: "Busy",
  full: "Full",
  unknown: "No data",
};

const rooms = JSON.parse(document.getElementById("rooms").textContent);
const searchInput = document.getElementById("search");
const talkSelect = document.getElementById("talk");
const errorText = document.getElementById("error");
const statusSection = document.getElementById("status");
const alternativesSection = document.getElementById("alternatives");

let talks = new Map();
let refreshTimer = null;
let searchTimer = null;

const roomName = (id) => rooms[id] || id;
const clock = (timestamp) => timestamp.slice(11, 16);
const details = (talk) =>
  `${clock(talk.start)}–${clock(talk.end)} · ${roomName(talk.room)} · ${talk.speaker}`;

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function badge(level) {
  const known = level in CROWD_LABEL ? level : "unknown";
  return element("span", `badge badge-${known}`, CROWD_LABEL[known]);
}

function card(talk, level, whyText) {
  const article = element("article", "card");
  const head = element("div", "card-head");
  head.append(element("h3", "", talk.title), badge(level));
  article.append(head, element("p", "meta", details(talk)));
  if (whyText) article.append(element("p", "why", whyText));
  return article;
}

function showError(message) {
  errorText.textContent = message;
  errorText.hidden = !message;
}

async function getJson(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

async function loadTalks(query) {
  const url = query ? `/api/talks?q=${encodeURIComponent(query)}` : "/api/talks";
  try {
    const data = await getJson(url);
    talks = new Map(data.talks.map((talk) => [talk.id, talk]));
    const selected = talkSelect.value;
    const placeholder = data.talks.length ? "Choose a talk…" : "No talks found";
    const options = [new Option(placeholder, "")];
    for (const talk of data.talks) {
      const label = `${clock(talk.start)} · ${roomName(talk.room)} · ${talk.title}`;
      options.push(new Option(label, talk.id));
    }
    talkSelect.replaceChildren(...options);
    if (talks.has(selected)) {
      talkSelect.value = selected;
    } else if (selected) {
      // Setting the value in code doesn't fire "change", so reset by hand.
      clearInterval(refreshTimer);
      statusSection.replaceChildren();
      alternativesSection.replaceChildren();
    }
    showError("");
  } catch {
    showError("Couldn't load the schedule. Please try again.");
  }
}

function renderAlternatives(data) {
  alternativesSection.replaceChildren();
  if (data.crowd_level !== "high" && data.crowd_level !== "full") return;

  alternativesSection.append(element("h2", "", "Less crowded alternatives"));
  const yourRank = CROWD_RANK[data.crowd_level];
  const quieter = data.results.filter(
    (talk) => CROWD_RANK[talk.why.crowd_level] < yourRank,
  );
  if (!quieter.length) {
    alternativesSection.append(
      element("p", "note", "No less crowded talks are running right now."),
    );
    return;
  }
  for (const talk of quieter) {
    const shared = talk.why.shared_tags;
    const why = shared.length ? `Also about ${shared.join(", ")}` : "";
    alternativesSection.append(card(talk, talk.why.crowd_level, why));
  }
}

async function refresh(talkId) {
  try {
    const data = await getJson(`/api/talks/${encodeURIComponent(talkId)}/alternatives`);
    if (talkSelect.value !== talkId) return;
    statusSection.replaceChildren(card(talks.get(talkId), data.crowd_level));
    if (data.source === "fallback") {
      statusSection.append(
        element("p", "note", "Live crowd data is unavailable, so levels may be out of date."),
      );
    }
    renderAlternatives(data);
    showError("");
  } catch {
    if (talkSelect.value === talkId) {
      showError("Couldn't check how crowded this talk is. Retrying…");
    }
  }
}

talkSelect.addEventListener("change", () => {
  clearInterval(refreshTimer);
  statusSection.replaceChildren();
  alternativesSection.replaceChildren();
  const talkId = talkSelect.value;
  if (!talkId) return;
  refresh(talkId);
  refreshTimer = setInterval(() => refresh(talkId), REFRESH_MS);
});

searchInput.addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => loadTalks(searchInput.value.trim()), 250);
});

loadTalks("");

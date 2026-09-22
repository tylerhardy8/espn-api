// Service worker: posts candidate picks to the analyzer. The server only
// marks players that exist in the league's draft pool — 404s are expected
// noise from liberal candidate mining and are silently ignored.
const DEFAULT_APP = "http://localhost:5050";
const posted = new Set();
let markedTotal = 0;

// Clicking the toolbar icon opens the advisor side panel beside the draft room
chrome.runtime.onInstalled.addListener(() => {
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});
});

async function appUrl() {
  try {
    const stored = await chrome.storage.sync.get("appUrl");
    return (stored.appUrl || DEFAULT_APP).replace(/\/$/, "");
  } catch (e) {
    return DEFAULT_APP;
  }
}

function updateBadge(text, color) {
  chrome.action.setBadgeText({ text });
  chrome.action.setBadgeBackgroundColor({ color });
}

async function postJson(path, body) {
  const base = await appUrl();
  const resp = await fetch(`${base}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return resp;
}

async function mark(body, ctx) {
  try {
    const resp = await postJson("/api/mark-drafted", { ...body, league_id: ctx.leagueId, mock: ctx.mock });
    if (resp.ok) {
      const data = await resp.json();
      markedTotal = data.marked ?? markedTotal;
      updateBadge(markedTotal ? String(markedTotal) : "", "#2e9e5b");
      console.log("FFA marked:", body, "->", data.marked, "total");
    } else if (resp.status === 409) {
      // Different league than the analyzer's active profile (e.g. a mock room)
      updateBadge("mock", "#e05252");
    }
  } catch (e) {
    console.warn("FFA app unreachable:", e.message);
  }
}

chrome.runtime.onMessage.addListener((msg) => {
  const ctx = { leagueId: msg.leagueId ?? null, mock: !!msg.mock };
  for (const pick of msg.picks || []) {
    // Re-post when the team becomes known for an already-posted player
    const key = pick.teamId ? `idt:${pick.playerId}` : `id:${pick.playerId}`;
    if (!posted.has(key)) {
      posted.add(key);
      const body = { player_id: pick.playerId };
      if (pick.teamId) body.team_id = pick.teamId;
      if (pick.bid) body.bid_amount = pick.bid;
      mark(body, ctx);
    }
  }
  for (const name of msg.names || []) {
    const key = `name:${name}`;
    if (!posted.has(key)) {
      posted.add(key);
      mark({ name }, ctx);
    }
  }
  for (const row of msg.rows || []) {
    const key = `row:${row.join("|")}`;
    if (!posted.has(key)) {
      posted.add(key);
      mark({ row }, ctx);
    }
  }
  if (msg.auction) {
    postJson("/api/auction-live", { event: msg.auction, league_id: ctx.leagueId, mock: ctx.mock })
      .then((resp) => { if (resp.status === 409) updateBadge("mock", "#e05252"); })
      .catch(() => {});
  }
  if (msg.meta) {
    postJson("/api/auction-live", { meta: msg.meta, league_id: ctx.leagueId, mock: ctx.mock }).catch(() => {});
  }
  if (msg.sample) {
    postJson("/api/debug-frame", { sample: msg.sample, league_id: ctx.leagueId, mock: ctx.mock })
      .catch(() => {});
  }
});

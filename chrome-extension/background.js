// Service worker: posts candidate picks to the analyzer. The server only
// marks players that exist in the league's draft pool — 404s are expected
// noise from liberal candidate mining and are silently ignored.
const DEFAULT_APP = "http://localhost:5050";
const posted = new Set();
let markedTotal = 0;

async function appUrl() {
  try {
    const stored = await chrome.storage.sync.get("appUrl");
    return (stored.appUrl || DEFAULT_APP).replace(/\/$/, "");
  } catch (e) {
    return DEFAULT_APP;
  }
}

function updateBadge() {
  chrome.action.setBadgeText({ text: markedTotal ? String(markedTotal) : "" });
  chrome.action.setBadgeBackgroundColor({ color: "#2e9e5b" });
}

async function post(body) {
  try {
    const base = await appUrl();
    const resp = await fetch(`${base}/api/mark-drafted`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (resp.ok) {
      const data = await resp.json();
      markedTotal = data.marked ?? markedTotal;
      updateBadge();
      console.log("FFA marked:", body, "->", data.marked, "total");
    }
  } catch (e) {
    console.warn("FFA app unreachable:", e.message);
  }
}

chrome.runtime.onMessage.addListener((msg) => {
  for (const id of msg.ids || []) {
    const key = `id:${id}`;
    if (!posted.has(key)) {
      posted.add(key);
      post({ player_id: id });
    }
  }
  for (const name of msg.names || []) {
    const key = `name:${name}`;
    if (!posted.has(key)) {
      posted.add(key);
      post({ name });
    }
  }
});

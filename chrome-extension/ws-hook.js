// Runs in the page's MAIN world at document_start: wraps WebSocket so we can
// observe ESPN's draft-room feed. Every pick-like message is mined for
// candidate player IDs; the analyzer server validates them against the draft
// pool, so false positives are harmless noise (404s).
(() => {
  if (window.__ffaHooked) return;
  window.__ffaHooked = true;

  const seen = new Map();  // playerId -> teamId|null (re-relay when team appears)
  const MAX_IDS_PER_FRAME = 500;  // catch-up snapshots carry a whole draft
  const EXCLUDE = /queue|watchlist|ranking/i;

  // Queue edits also carry playerIds — marking your own queue as drafted
  // would poison the board. Exclusion is structural (skip queue-named
  // subtrees and queue-typed objects) so a big state snapshot that happens
  // to CONTAIN queue data still yields its picks.
  function collectPicks(value, out) {
    if (value == null || out.size >= MAX_IDS_PER_FRAME) return;
    if (Array.isArray(value)) {
      for (const v of value) {
        if (typeof v === "object") collectPicks(v, out);
      }
      return;
    }
    if (typeof value !== "object") return;
    if (typeof value.type === "string" && EXCLUDE.test(value.type)) return;

    let playerId = null;
    let teamId = null;
    for (const [k, v] of Object.entries(value)) {
      if (/playerid/i.test(k) && Number.isInteger(v) && v > 0) playerId = v;
      else if (/^teamid$/i.test(k) && Number.isInteger(v) && v > 0) teamId = v;
    }
    if (playerId) {
      out.set(playerId, teamId);  // teamId sits beside playerId in pick objects
    }
    for (const [k, v] of Object.entries(value)) {
      if (EXCLUDE.test(k)) continue;  // skip queue/watchlist/ranking branches
      if (typeof v === "object") collectPicks(v, out);
    }
  }

  function mine(data) {
    if (typeof data !== "string" || data.length > 2000000) return;
    const out = new Map();  // playerId -> teamId|null
    if (data[0] === "{" || data[0] === "[") {
      if (!/playerid/i.test(data)) return;  // cheap pre-filter
      try { collectPicks(JSON.parse(data), out); } catch (e) { /* not JSON */ }
    } else if (/select|pick|draft/i.test(data) && !EXCLUDE.test(data)) {
      // Token frames: "SELECTED 7 3117251 ..." — plausible id ints, team unknown
      for (const m of (data.match(/\b\d{4,8}\b/g) || []).slice(0, 50)) {
        out.set(parseInt(m, 10), null);
      }
    }
    const fresh = [...out.entries()]
      .filter(([id, teamId]) => !seen.has(id) || (teamId && seen.get(id) === null))
      .map(([id, teamId]) => ({ playerId: id, teamId }));
    if (fresh.length) {
      fresh.forEach((p) => seen.set(p.playerId, p.teamId));
      window.postMessage({ source: "ffa-draft-tracker", picks: fresh }, "*");
    }
  }

  const Original = window.WebSocket;
  window.WebSocket = function (...args) {
    const socket = new Original(...args);
    try {
      socket.addEventListener("message", (event) => {
        try { mine(event.data); } catch (e) { /* never break the page */ }
      });
    } catch (e) { /* ignore */ }
    return socket;
  };
  window.WebSocket.prototype = Original.prototype;
  Object.setPrototypeOf(window.WebSocket, Original);
})();

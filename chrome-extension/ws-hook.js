// Runs in the page's MAIN world at document_start: wraps WebSocket so we can
// observe ESPN's draft-room feed. Every pick-like message is mined for
// candidate player IDs; the analyzer server validates them against the draft
// pool, so false positives are harmless noise (404s).
//
// Auction rooms additionally carry nominations, bids, and sales. Those frames
// are relayed as "auction" events (parsed when the shape is known, raw
// otherwise) and every small frame is sampled to the analyzer's log so the
// protocol can be read from a real room.
(() => {
  if (window.__ffaHooked) return;
  window.__ffaHooked = true;

  const seen = new Map();  // playerId -> teamId|null (re-relay when team appears)
  const MAX_IDS_PER_FRAME = 500;  // catch-up snapshots carry a whole draft
  const EXCLUDE = /queue|watchlist|ranking/i;

  // Debug sampling: small token frames are relayed generously (they are the
  // protocol), JSON snapshots only a handful (they are huge).
  let tokenSamples = 0;
  let jsonSamples = 0;
  let outSamples = 0;
  const MAX_TOKEN_SAMPLES = 400;
  const MAX_JSON_SAMPLES = 8;
  const MAX_OUT_SAMPLES = 80;
  const NOISE = /^\s*(PING|PONG|HEARTBEAT|KEEPALIVE)\b/i;

  function post(payload) {
    window.postMessage({ source: "ffa-draft-tracker", ...payload }, "*");
  }

  function sample(direction, data) {
    const stamp = new Date().toISOString().slice(11, 23);
    post({ sample: `${direction} ${stamp} ${data}` });
  }

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
    let bid = null;
    for (const [k, v] of Object.entries(value)) {
      if (/playerid/i.test(k) && Number.isInteger(v) && v > 0) playerId = v;
      else if (/^teamid$/i.test(k) && Number.isInteger(v) && v > 0) teamId = v;
      else if (/^(bidamount|bid|price|amount)$/i.test(k) && Number.isInteger(v) && v > 0) bid = v;
    }
    if (playerId) {
      out.set(playerId, { teamId, bid });  // teamId/bid sit beside playerId in pick objects
    }
    for (const [k, v] of Object.entries(value)) {
      if (EXCLUDE.test(k)) continue;  // skip queue/watchlist/ranking branches
      if (typeof v === "object") collectPicks(v, out);
    }
  }

  // ESPN's draft room speaks a token protocol. Snake (captured live):
  //   "SELECTED <teamId> <playerId> <n> {memberSwid}"   (playerId < 0 = D/ST)
  //   "AUTOSELECTED ..." same shape; "SELECTING <teamId> <clockMs>" ignored.
  // Auction tokens are parsed provisionally here and refined from a real
  // room's samples; anything unrecognised is still relayed raw.
  const TOKEN_RE = /^\s*([A-Z_]+)\s*(.*)$/s;

  function parseToken(data) {
    const m = data.match(TOKEN_RE);
    if (!m) return null;
    const verb = m[1].toUpperCase();
    const args = m[2].trim().split(/\s+/).filter(Boolean);
    const ints = args.map((a) => (/^-?\d+$/.test(a) ? parseInt(a, 10) : null));
    return { verb, args, ints, raw: data.slice(0, 300) };
  }

  function mine(data) {
    if (typeof data !== "string" || data.length > 2000000) return;
    const out = new Map();  // playerId -> {teamId, bid}
    if (data[0] === "{" || data[0] === "[") {
      if (jsonSamples < MAX_JSON_SAMPLES && (data.length < 600 || /playerid/i.test(data))) {
        jsonSamples += 1;
        sample("IN", data.slice(0, 4000));
      }
      if (!/playerid/i.test(data)) return;  // cheap pre-filter
      try { collectPicks(JSON.parse(data), out); } catch (e) { /* not JSON */ }
    } else {
      if (!NOISE.test(data) && tokenSamples < MAX_TOKEN_SAMPLES) {
        tokenSamples += 1;
        sample("IN", data.slice(0, 600));
      }
      const tok = parseToken(data);
      if (!tok) return;
      if (tok.verb === "SELECTED" || tok.verb === "AUTOSELECTED") {
        // <teamId> <playerId> <n> {swid}  — snake; auction sales may append a price
        const [teamId, playerId] = tok.ints;
        if (Number.isInteger(teamId) && Number.isInteger(playerId) && playerId !== 0) {
          const bid = tok.ints.slice(2).find((n) => Number.isInteger(n) && n > 0 && n < 1000) ?? null;
          out.set(playerId, { teamId, bid });
        }
      } else if (tok.verb !== "SELECTING") {
        // Nomination / bid / sold / clock frames: relay for the auction card.
        // The service worker forwards these to the analyzer, which logs and
        // interprets them once the shapes are known.
        post({ auction: tok });
      }
    }
    const fresh = [...out.entries()]
      .filter(([id, info]) => !seen.has(id) || (info.teamId != null && seen.get(id) == null))
      .map(([id, info]) => ({ playerId: id, teamId: info.teamId, bid: info.bid }));
    if (fresh.length) {
      fresh.forEach((p) => seen.set(p.playerId, p.teamId));
      post({ picks: fresh });
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

  // Outbound frames (our own bids/nominations) show the client side of the
  // protocol — sampled only, never relayed as picks.
  try {
    const originalSend = Original.prototype.send;
    Original.prototype.send = function (data) {
      try {
        if (typeof data === "string" && outSamples < MAX_OUT_SAMPLES && !NOISE.test(data)) {
          outSamples += 1;
          sample("OUT", data.slice(0, 600));
        }
      } catch (e) { /* ignore */ }
      return originalSend.call(this, data);
    };
  } catch (e) { /* ignore */ }
})();

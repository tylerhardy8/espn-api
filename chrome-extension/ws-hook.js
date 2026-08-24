// Runs in the page's MAIN world at document_start: wraps WebSocket so we can
// observe ESPN's draft-room feed. Every pick-like message is mined for
// candidate player IDs; the analyzer server validates them against the draft
// pool, so false positives are harmless noise (404s).
(() => {
  if (window.__ffaHooked) return;
  window.__ffaHooked = true;

  const seen = new Set();
  const MAX_IDS_PER_FRAME = 30;

  // Only mine frames that look like completed selections. Queue edits also
  // carry playerIds — marking your own queue as drafted would poison the
  // board, so anything queue-flavored is skipped outright.
  function isPickFrame(data) {
    if (/queue|watchlist|ranking/i.test(data)) return false;
    return /select|pick|draft/i.test(data);
  }

  function collectIds(value, out) {
    if (value == null || out.size >= MAX_IDS_PER_FRAME) return;
    if (typeof value === "object") {
      for (const [k, v] of Object.entries(value)) {
        if (/playerid/i.test(k) && Number.isInteger(v) && v > 0) out.add(v);
        else collectIds(v, out);
      }
    }
  }

  function mine(data) {
    if (typeof data !== "string" || data.length > 200000) return;
    if (!isPickFrame(data)) return;
    const out = new Set();
    if (data[0] === "{" || data[0] === "[") {
      try { collectIds(JSON.parse(data), out); } catch (e) { /* not JSON */ }
    }
    if (out.size === 0) {
      // Token frames: "SELECTED 7 3117251 ..." — plausible id ints
      for (const m of (data.match(/\b\d{4,8}\b/g) || []).slice(0, MAX_IDS_PER_FRAME)) {
        out.add(parseInt(m, 10));
      }
    }
    const fresh = [...out].filter((id) => !seen.has(id));
    if (fresh.length) {
      fresh.forEach((id) => seen.add(id));
      window.postMessage({ source: "ffa-draft-tracker", ids: fresh }, "*");
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

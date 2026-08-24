// Isolated-world bridge: relays ids from the WebSocket hook to the service
// worker, and scans the pick-history panel DOM for player names as a
// fallback (backfill for picks made before the hook was installed).
(() => {
  const sentNames = new Set();

  window.addEventListener("message", (event) => {
    const msg = event.data;
    if (event.source !== window || !msg || msg.source !== "ffa-draft-tracker") return;
    if (Array.isArray(msg.picks) && msg.picks.length) {
      chrome.runtime.sendMessage({ picks: msg.picks });
    }
    if (msg.sample) {
      chrome.runtime.sendMessage({ sample: msg.sample });
    }
  });

  // Name shaped like "Ja'Marr Chase", "Kenneth Walker III", "A.J. Brown"
  const NAME_RE = /^[A-Z][\w.'-]+ [A-Z][\w.'-]+( [A-Z][\w.]{0,4})?$/;

  function scanDom() {
    const rows = [];
    // History panels only — broader selectors (pick/selection) match the
    // on-the-clock card and queue rows, which must never be auto-marked.
    const panels = document.querySelectorAll('[class*="history" i]');
    for (const panel of panels) {
      if (/queue|watchlist/i.test(panel.className)) continue;
      // A "row" is any element containing a player-shaped name plus other
      // short leaf texts (pick number, team name). The server matches the
      // player against the pool and the team against league team names.
      for (const el of panel.querySelectorAll("*")) {
        const total = (el.textContent || "").trim();
        if (total.length < 5 || total.length > 120) continue;
        const leaves = [];
        for (const leaf of el.querySelectorAll("*")) {
          if (leaf.children.length) continue;
          const t = (leaf.textContent || "").trim();
          if (t && t.length <= 60) leaves.push(t);
        }
        if (!leaves.some((t) => NAME_RE.test(t))) continue;
        const key = leaves.join("|");
        if (sentNames.has(key) || leaves.length < 2) continue;
        sentNames.add(key);
        rows.push(leaves.slice(0, 8));
        if (rows.length >= 250) break;
      }
    }
    if (rows.length) {
      chrome.runtime.sendMessage({ rows });
    }
  }

  setInterval(scanDom, 6000);
  setTimeout(scanDom, 2000);
})();

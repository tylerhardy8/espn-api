// Isolated-world bridge: relays events from the WebSocket hook to the service
// worker, and scans the pick-history panel DOM for player names as a
// fallback (backfill for picks made before the hook was installed).
(() => {
  const sentNames = new Set();

  // Which ESPN league this tab is in (draft room URLs carry leagueId=…).
  // The analyzer refuses marks from a different league than its active
  // profile, so a mock room can never pollute the real board.
  let roomLeagueId = null;  // from the room's TOKEN frame (authoritative)
  let roomTeamId = null;
  function pageLeagueId() {
    if (roomLeagueId) return roomLeagueId;
    const m = location.href.match(/[?&]leagueId=(\d+)/i);
    return m ? parseInt(m[1], 10) : null;
  }
  const isMock = /mock/i.test(location.pathname);

  function send(payload) {
    chrome.runtime.sendMessage({ leagueId: pageLeagueId(), myTeamId: roomTeamId, mock: isMock, ...payload });
  }

  window.addEventListener("message", (event) => {
    const msg = event.data;
    if (event.source !== window || !msg || msg.source !== "ffa-draft-tracker") return;
    if (msg.meta) {
      roomLeagueId = msg.meta.leagueId || roomLeagueId;
      roomTeamId = msg.meta.teamId || roomTeamId;
      send({ meta: msg.meta });
    }
    if (Array.isArray(msg.picks) && msg.picks.length) send({ picks: msg.picks });
    if (msg.auction) send({ auction: msg.auction });
    if (msg.sample) send({ sample: msg.sample });
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
      // short leaf texts (pick number, team name, "$NN" price in auctions).
      // The server matches the player against the pool, the team against
      // league team names, and reads the price from a "$NN" leaf.
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
    if (rows.length) send({ rows });
  }

  setInterval(scanDom, 6000);
  setTimeout(scanDom, 2000);
})();

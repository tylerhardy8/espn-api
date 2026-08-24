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
  });

  // Name shaped like "Ja'Marr Chase", "Kenneth Walker III", "A.J. Brown"
  const NAME_RE = /^[A-Z][\w.'-]+ [A-Z][\w.'-]+( [A-Z][\w.]{0,4})?$/;

  function scanDom() {
    const names = new Set();
    // History panels only — broader selectors (pick/selection) match the
    // on-the-clock card and queue rows, which must never be auto-marked.
    const panels = document.querySelectorAll('[class*="history" i]');
    for (const panel of panels) {
      if (/queue|watchlist/i.test(panel.className)) continue;
      for (const el of panel.querySelectorAll("span, div, a, td")) {
        if (el.children.length) continue;
        const text = (el.textContent || "").trim();
        if (text.length >= 5 && text.length <= 30 && NAME_RE.test(text) &&
            !sentNames.has(text)) {
          names.add(text);
        }
      }
    }
    if (names.size) {
      names.forEach((n) => sentNames.add(n));
      chrome.runtime.sendMessage({ names: [...names] });
    }
  }

  setInterval(scanDom, 6000);
  setTimeout(scanDom, 2000);
})();

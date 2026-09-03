# FFA — Chrome extension (tracker + side panel)

Two things in one extension:

1. **Live pick tracking** — relays picks from ESPN's draft room to your Fantasy
   Football Analyzer the moment they happen (ESPN's API freezes during live
   drafts; this watches the room itself via its WebSocket feed and pick-history
   panel). Auction rooms: sales carry the price, nominations/bids drive the
   on-block card.
2. **The whole app as a side panel** — docked beside the draft room, so you
   never switch windows: **Draft** (board, budget, on-block card with a
   suggested max bid, cash-rich rivals, nomination ideas, Claude advice),
   **Trades**, **Waivers**, and a league switcher in the header.

## Install (one time, ~30 seconds)

1. Chrome → `chrome://extensions`
2. Toggle **Developer mode** (top right)
3. **Load unpacked** → select this `chrome-extension/` folder

After pulling new code: `chrome://extensions` → ↻ on the extension, then reload
any open ESPN draft-room tab.

## Draft-day runbook (auction)

**The night before / morning of**

1. Docker Desktop running, container up. Check identity:
   ```bash
   curl -s localhost:5050/api/me
   ```
   `league` must be your draft's profile and `team_name` your exact ESPN team
   name. Switch leagues in the panel header or on the app's Setup page.
2. App Setup page: Anthropic key present (AI badge green), FantasyPros key
   present.
3. Confirm the draft rules the app sees: open `localhost:5050/api/draft-state`
   — `is_auction: true`, and each team's `remaining` equals the league budget.
4. **Clear marks** (panel → Settings → Clear marks) if you ran a mock draft or
   the badge shows a stale count.

**15 minutes before**

5. Open the app's Live Draft page once (warms the valued pool + league intel),
   press Advise Now once in the panel.
6. Enter the ESPN draft room, then **reload the room tab once inside** so the
   tracker hooks the live feed. Click the toolbar icon → the side panel opens.
   Confirm: green dot, your team name, `$` values on the board, the budget row
   (`$320 left · max bid …`), the **On the block** card.

**During the draft**

- The badge counts relayed picks; it should track the room's sales count.
  Off by a few? Open ESPN's Pick History panel — the scraper backfills with
  prices. Last resort: type a price in the row's `$` box and press **×** (set
  the buying team in "× marks to"). Cmd/Ctrl+Z undoes your last manual mark.
- **On the block**: shows the nominated player, high bid + bidder, and a
  BID/PASS verdict with your suggested max (inflation-adjusted value × need,
  capped at your max bid). If the feed misses a nomination, type the name and
  current high bid and press **set**.
- **Advise now** asks Claude (with a live-news web search when "news" is
  ticked); **auto** re-advises as picks land (throttled to 90s, no search).
- Don't restart the container mid-draft: marks persist on disk, but the pool
  and intel caches would rebuild (a minute of stale board).

## Protocol capture (mock drafts)

The extension samples every small frame from the room to the analyzer's log,
so a new room type (auction) can be read from a real session:

```bash
docker logs ffa 2>&1 | grep -E "FRAME-SAMPLE|AUCTION-EVENT" | tail -200
```

Marks from a mock room are refused (HTTP 409 — the badge turns red "mock"),
so a mock never pollutes your real board. Known shapes (snake, captured live):
`SELECTED <teamId> <playerId> <n> {swid}` (playerId < 0 = D/ST),
`SELECTING <teamId> <clockMs>`.

## Notes

- The panel talks to your analyzer instance; change the URL under Settings if
  you use a hosted instance (also add that host to `host_permissions` in
  `manifest.json`).
- Candidate mining is deliberately liberal: the analyzer validates everything
  against the league's draft pool, so stray numbers/names are ignored 404s.
- Queue, watchlist, and ranking messages are explicitly excluded — adding a
  player to your draft queue will never mark them as drafted.
- Marks live in `~/.fantasy_football_analyzer_marks.json` (the config volume in
  Docker) and survive restarts.

# FFA Draft Tracker — Chrome extension

Relays picks from ESPN's live draft room to your Fantasy Football Analyzer
automatically. ESPN's API freezes during live drafts; this watches the draft
room itself (its WebSocket feed, plus the pick-history panel as a fallback)
and marks every pick in the app the moment it happens.

## Install (one time, ~30 seconds)

1. Chrome → `chrome://extensions`
2. Toggle **Developer mode** (top right)
3. **Load unpacked** → select this `chrome-extension/` folder

## Use on draft day

1. Make sure the analyzer is running (`http://localhost:5050`)
2. Open (or **reload**, if the draft already started) your ESPN draft room tab
   — reloading lets the tracker hook the live feed, and ESPN's catch-up burst
   backfills every pick already made
3. Watch the extension's toolbar badge count picks as they're relayed;
   the app's Live Draft page updates within one poll (~10s)

## Notes

- Candidate mining is deliberately liberal: the analyzer validates everything
  against the league's draft pool, so stray numbers/names are just ignored 404s.
- Queue, watchlist, and ranking messages are explicitly excluded — adding a
  player to your draft queue will never mark them as drafted.
- Pointing at a hosted instance instead of localhost: set `appUrl` in the
  extension's storage (or edit `DEFAULT_APP` in `background.js`) and add the
  host to `host_permissions` in `manifest.json`.

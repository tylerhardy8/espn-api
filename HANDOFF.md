# Handoff: Fantasy Football Analyzer (espn-api fork)

Written 2026-09-11 for whoever picks this up next (human or agent). Read this
first, then `AUCTION_ASSISTANT_NOTES.md` (model details) and
`chrome-extension/README.md` (draft-day runbook).

## What this is

A fork of `cwendt94/espn-api` plus an application package,
`fantasy_football_analyzer/`, that does three things for the owner (Tyler,
GitHub `tylerhardy8`, branch `claude/fantasy-football-analyzer-y969m`,
PR https://github.com/tylerhardy8/espn-api/pull/1):

1. **Live auction/snake draft assistant** — a Flask web app plus a Chrome
   extension whose side panel docks beside ESPN's draft room. ESPN's REST API
   freezes during live drafts, so the extension reads the room's WebSocket
   feed directly and relays picks (with prices) to the app.
2. **Valuation model** — projections → value-over-replacement → auction
   dollars, calibrated against the league's own price history, blended with
   ESPN crowd values and FantasyPros consensus (rankings and projections).
3. **In-season engine** — week-by-week trade analyzer (proposals, target,
   shop, offer evaluation with counters), FAAB bid model, waivers, and a
   Claude (Opus 5) advisor for draft, trades, and waivers.

Everything runs locally in Docker; friends can be given their own instance
(`deploy/`). All state a user cares about lives in a config JSON and a marks
JSON inside the container's `/root` volume.

## The league that matters right now

**Papa Trump League**, ESPN league id `202314`, profile name `League 202314`,
season 2026. Tyler's team is **"Kaitlan Callin' Audibles"** (team_id 14; it
was "Quiet, Piggy" until a mid-draft rename). 12 teams, AUCTION, **$320**
budget (was $300 in 2025). Slots: QB, 2 RB, 2 WR, TE, 2 RB/WR/TE flex, K, LB,
DL, DB, P, HC, 6 BE, 1 IR → **20 rostered, no D/ST slot**. Full PPR with
**TE 1.5/reception** (ESPN stores this as `pointsOverrides` per position with
a base of 0 — see `sources.reception_scoring`). Decimal kicker scoring.
14-week regular season, **8 of 12 make a 3-week single-elimination playoff
(weeks 15–17)**; seeding by total points scored. **FAAB $150** (new this year,
processed Tuesdays 11am ET). **2027 one-keeper franchise tag: only players
DRAFTED and NEVER TRADED are eligible.**

Second profile: **Hardy League** (id 1839119665), snake, already drafted.

### The 2026 draft (done 2026-09-04)

Final roster: Allen $33, Taylor $92, Hall $41, Flowers $37, Pickens $45,
Bowers $58, then fourteen $1 buys (Stafford, Hockenson, Juwan Johnson,
Freiermuth, Schultz, Ferguson, Helm, Noel, Santos K, Sherwood LB, Highsmith
DE, Curl S, Cooke P, Bengals HC). Projected #1 lineup in the league but:

- **Only two running backs (Taylor, Hall).** Any trade moving Hall must
  return an RB or the RB2 slot is empty every week (the engine catches this;
  a human reading "2-for-1" math will not).
- **Week-13 bye pile-up**: Taylor, Hall, Flowers, Bowers all sit.
- Tight ends are the best flex bodies in this league (TE 1.5): Hockenson and
  Juwan Johnson start at flex over any available RB/WR.
- Bowers was tagged OUT by ESPN on 2026-09-11; the engine's availability
  multiplier (0.85) then pushes "sell Bowers" — don't act on that without
  knowing the injury.

### Open trade threads (as of 2026-09-11, week 1 not yet played)

Recommended asks from the engine (see "Trade engine" below):

- **Pat** (Patrick Kenney, "🐑 Sheep 2.0", ~6 trades/yr, RB-deep, QB is
  Caleb Williams): **Josh Allen for Kyren Williams + D'Andre Swift**
  (+11 me / +18 him, 85% acceptance, fixes RB depth and week 13). Fallback:
  Hall for Kyren + Dowdle (even for me, +25 him). He has offered Pickens for
  Evans+Dowdle+Marks and Hall for Swift(+Deebo)(+$30 FAAB); all were
  declines (−36 to −68 for me).
- **Nick** (Nick Radich, "The Champ Is Here", two QBs): **Allen for Hurts +
  McMillan** (+24 / +10, 95%). Competes with Pat's deal for Allen; do Pat
  first. If Pat lands, offer Nick Pickens for Odunze + McMillan (+17 / +5).
- **Joel** (Joel Schilperoort, "AJ Brownout", ~3 trades/yr): **Hall for
  Kenneth Walker III straight** (+1 / +8, 32%); ESPN's raw projection makes
  Hall look better to him. Anything more from him is a no.
- Manager map (names change; go by owner): The Real Mr. Unlimited = Steffen
  Perna (11 trades/yr), Joel's Favorite Team = Rob Munn, Queen Nicki Demure =
  Matt Neighbors, Pat's #1 Trade Partner = Jack Rostas, Harambe's Heroes =
  Steven Sargent, Chase Hawk Tuah's = Bret Mega, Buckley's Blunders = David
  Clark, Kraft McCaf and Cheese = Michael Perna.

## Repository map

```
fantasy_football_analyzer/
  auction.py        pool build, two-segment VBD, blend/finalize, caps, byes, apply_consensus_projections
  lineup.py         slot_profile (single source of roster truth), optimal_lineup, team_context_value
  sources.py        FantasyPros rankings + projections (scored under league rules), Sleeper, scoring detection
  ros.py            rest-of-season projection (blended ESPN/FP, byes, availability, playoff weight)
  trade_engine.py   week-by-week TradeEngine: matches/target/shop/evaluate, owner model, keeper flags
  trades.py         legacy season-total engine (find_trade_matches delegates to TradeEngine), roster strength
  faab.py           FAAB state, winning-bid history, suggest_bid
  waivers.py        free agents, streamers, recommendations (projection fallback pre-season)
  plan.py           auction budget plan / pace
  league_intel.py   4-season history: manager profiles, price curves, positional premiums, market values
  marks.py          persisted drafted-player marks (team, price, seq); mock namespace; league guard
  draft_tracker.py  DraftState: budgets, inflation (value or market basis), scarcity, snake slot math
  ai_advisor.py     Claude prompts/contexts (Opus 5, web search tool, pause_turn loop)
  config.py         profiles {leagues:[...], active, espn_s2, swid, anthropic_api_key}; DEFAULT_YEAR=2026
  web/routes.py     pages + /api/draft-state, /api/mark-drafted, /api/draft-recommendation, /api/me
  web/panel_api.py  side-panel JSON: leagues/switch, mock-mode, auction-live, trades*, waivers*, marks/clear
  web/helpers.py    caches: league (60s), valued pool (600s), intel (12h, background warm)
chrome-extension/   MV3: ws-hook.js (MAIN world), bridge.js, background.js, sidepanel.*
tools/backtest_2025.py   model vs the league's real 2025 sale prices
tests/analyzer/     unittest-style; run with pytest (see Verification)
deploy/             per-friend containers + Caddy
```

## Data flow

1. `connect_league` (espn_api) → `League`. Rosters, draft feed, settings,
   free agents, `recent_activity` (waiver bids), `_get_all_pro_schedule`
   (byes). ESPN provides **only season-total projections pre-season**; weekly
   rows appear in-season.
2. `auction.build_valued_pool`: pool of rostered + free agents →
   `apply_consensus_projections` (FantasyPros stat lines scored by
   `sources.score_stat_line`, 50/50 with ESPN) → `calculate_auction_values`
   (starter baseline from `lineup.slot_profile`; depth allowance
   `DEPTH_DOLLAR_SHARE`) → `enrich_pool` (Sleeper injury/practice/depth/age,
   FP ECR/tier/best/worst → `expert_value`, `ceiling_value`, `floor_value`) →
   `finalize_values` (weights `W_MODEL/W_CROWD/W_EXPERT` = .45/.20/.35 with
   external $ scaled by `budget/200`, `AVAILABILITY` multipliers, K/D-ST caps,
   cash-sum normalization over core slots only).
3. `league_intel` (cached 12h, built off-thread): manager tendencies, per-
   position price curves, `positional_premiums` → `apply_market_values`
   (market price per player, blended with the rank price curve at the top).
4. Draft: extension → `/api/mark-drafted` (id/name/row+"$N") → `marks.py` →
   `DraftState` → `/api/draft-state` (board, budgets, inflation, plan,
   nominations) and `/api/auction-live` (on-block card: BID/STRETCH/PASS with
   reason; lineup-aware need via `lineup.marginal_value`; league price,
   rival read, sale history, plan target, availability, bye collision).
5. In-season: `TradeEngine` (rosters from ESPN, or from the draft board until
   ESPN publishes) values each roster as Σ weekly best lineup (byes removed,
   playoff weeks ×1.5) + bench insurance; owner model = trades/yr,
   positional bias, FAAB share; keeper flags from draft prices.
   `faab.suggest_bid`: tier from points over the 5th-best FA at the position,
   rivals with a hole × their cash, season clock, aggressiveness learned from
   this season's winning bids.

## Run, deploy, verify

- Container: `docker run -d --name ffa -p 5050:5000 -v ffa-config:/root --env-file .env ffa:latest`
  (`.env` holds ANTHROPIC_API_KEY, FANTASYPROS_API_KEY, FLASK_SECRET_KEY —
  never commit or print it). Rebuild from repo root. The committed Dockerfile
  uses `python:3.11-slim`; on 2026-09-04 Docker's credential helper hung, so
  the running image was built from a scratch copy of the Dockerfile on the
  cached `python:3.10-slim` (`docker build --pull=false -f <scratch>/Dockerfile.310`).
  Rollback image from before the model review: `ffa:pre-review`.
- Config lives in the container: `docker exec ffa cat /root/.fantasy_football_analyzer.json`
  (secrets inside). Marks: `/root/.fantasy_football_analyzer_marks.json`.
- Local python has no Flask; run anything app-side with
  `uv run --no-project --with flask --with feedparser --with requests python3 …`
  (add `--with pytest --with requests_mock` for tests).
- Tests: `python -m pytest tests/analyzer tests/football/unit` (116 passing
  as of 2026-09-11). Stub-league smoke scripts lived in the session
  scratchpad (`smoke_panel.py`, `smoke_intel.py`); recreate from
  `tests/analyzer/test_trade_engine.py` fixtures if needed.
- Backtest: `tools/backtest_2025.py --league-id 202314 --year 2025 --config <config.json>`
  (rho(value, price) .824→.839, MAE $9.2→$7.1 after the model review).
- Panel as a plain page: `.claude/launch.json` "sidepanel" serves
  `chrome-extension/` on :8765; browser-pane clicks time out — drive it with
  `javascript_tool` instead.
- Handy live checks (container running, profile active):
  `curl localhost:5050/api/me`, `/api/draft-state?team=<name>`,
  `/api/trades?team=<name>`, `POST /api/trade-eval {"partner","give","receive"}`,
  `/api/waivers?team=<name>`, `/api/trade-target?player=`, `/api/trade-shop?player=`.
  Team names carry emoji and double spaces; URL-encode and match exactly.
  Running a script inside the container needs `-e PYTHONPATH=/app -w /app`.

## Chrome extension / draft room protocol (captured live)

- Content scripts on `fantasy.espn.com`: `ws-hook.js` wraps WebSocket in the
  MAIN world, `bridge.js` relays to the service worker, which POSTs to the
  app. Token frames: snake `SELECTED <teamId> <playerId> <n> {swid}`;
  auction `TOKEN 1:<leagueId>:<myTeamId>:{swid}:…`, `NOMINATION <team> <ms>`,
  `BID <team> <player> <amt> <clockTotal> <clockLeft>`, `CLOCK 2 <msLeft>
  <highTeam> <player> <highBid>`, `SOLD <team> <player> <slot> <price> 0`.
  The room does not replay history; the pick-history DOM panel is scraped
  (rows with "$N"), and pasted history text can be loaded via
  `/api/mark-drafted {"row":[name, team, "$N"]}` (worked live).
- Guards: marks from a different league id, a mock room, or an unidentified
  room are refused (409). **Mock rehearsal mode** (`/api/mock-mode`, panel
  Settings) keeps mock marks on a separate board and persists across
  restarts — make sure the MOCK badge is off on a real draft day.
- Reload the extension at `chrome://extensions` after code changes, then
  reload the room tab (the old content script otherwise stays alive).

## Gotchas learned the hard way

- ESPN `settings.scoring_format` misses per-position overrides; use
  `sources.reception_scoring/describe_scoring/detect_scoring`.
- FantasyPros API: pass `week=0` or premium keys get a 10-player free-tier
  response. Projections endpoint: `/projections?position=RB&week=0`.
- Team names change mid-season (and mid-draft). Resolve teams by owner or
  team_id; the config's `team_name` must match ESPN exactly.
- `routes.py`, `draft_tracker.py`, `league_intel.py`, `trades.py`,
  `waivers.py`, `ai_advisor.py`, `sources.py`, `cli.py`, `helpers.py` use
  CRLF line endings; patch via bytes to avoid whole-file diffs.
- zsh does not word-split unquoted variables; loop API calls in Python.
- Owners judge trades on raw roster totals ("432 vs 292"); the engine scores
  lineups. Explain the slot math when negotiating.
- Env vars from `.env` can be empty strings: read with `or`, not `.get` defaults.

## Suggested next work

1. Nomination-order and "who is nominating" are read from the room; ESPN's
   INIT frame (base64 binary) likely holds the full draft state and could
   backfill history without the DOM scrape — unexplored.
2. Trade engine: FAAB as a tradeable asset (ESPN allows it here), and
   uncertainty (ceiling/floor) in acceptance.
3. Post-draft grade page for auction drafts (exists for snake).
4. Make the committed Dockerfile build again (credential helper) and move
   the 3.10 workaround out of the scratchpad.
5. `draft.build_player_rankings` still ranks by ESPN projection only; point
   it at the valued pool.

# Auction Draft Assistant — Session Handoff Notes

Goal: optimize the fantasy football analyzer for the user's **auction draft** — live draft
assistant with budgets, inflation-adjusted dollar values, and throttled auto AI advice.
Approved plan: `~/.claude/plans/review-this-dataset-and-spicy-candy.md`.

**Nothing is committed yet** — all work sits in the working tree on branch
`claude/fantasy-football-analyzer-y969m`.

## Completed earlier in this session (separate feature, verified)

Dataset fixes + web UI redesign (charts, luck analysis, dashboard stat cards). Verified by
smoke test + browser. Files: `fantasy_football_analyzer/historical.py`, `web/routes.py`,
`web/static/style.css`, `web/static/app.js` (new), `web/templates/base.html`,
`dashboard.html`, `history.html`, `draft.html`, `trades.html`, `waivers.html`.

## Auction assistant — done so far

| Part | Status | Files |
|---|---|---|
| 1. espn_api fields | ✅ done, **tests not yet run** | `espn_api/base_settings.py` (`draft_type`, `auction_budget` from `draftSettings`), `espn_api/football/player.py` (`avg_draft_position`, `auction_value_avg` from `ownership`) |
| 2. Auction engine | ✅ done | `fantasy_football_analyzer/auction.py` (new): `build_draft_pool` (free_agents + rosters), `derive_roster_targets` (from `position_slot_counts`), `calculate_auction_values` (VBD→$, 50/50 blend with ESPN crowd value), `detect_tiers` (gap > 0.75×stdev), `build_valued_pool` one-call helper returning `(pool, budget, targets, roster_size)` |
| 3. Auction DraftState | ✅ done | `fantasy_football_analyzer/draft_tracker.py`: `DraftState(league, pool=, budget=, targets=, roster_size=)`; picks now carry `position`, `expected_value`, `value_delta`; `get_budgets()` (spent/remaining/slots_left/max_bid), `get_inflation()`, `get_available_ranked(limit, position)` (adds `adjusted_value`), real `get_team_needs`, `_detect_position_runs` (≥3 of last 5 → `self.active_run`), `is_auction` (settings or observed bids) |
| 5. AI advisor | ✅ done | `fantasy_football_analyzer/ai_advisor.py`: `_league_settings_info` extracted, `build_auction_context` (budget table, inflation, roster+needs, recent sales w/ deltas, run alert, tiered best-available), `AUCTION_SYSTEM_PROMPT` (max bids, nomination strategy, endgame $1s), `get_ai_recommendation` dispatches to auction path when `state.is_auction and state.pool` |
| 4a. Pool cache | ✅ done | `fantasy_football_analyzer/web/helpers.py`: `get_valued_pool(league, config)` with `_pool_cache`/`_POOL_TTL=600`; `clear_league_cache` clears both |

## STATUS: ALL WORK COMPLETE ✅ (2026-07-12)

Everything below was finished and verified after the pause:
- `web/routes.py`: `_build_draft_state` helper builds DraftState with the valued pool;
  `/api/draft-state` accepts `?team=`, returns `is_auction`, `inflation`, `budgets`,
  `best_available` (top 40), `my_needs`, `active_run`; picks carry position/price/value_delta;
  `api_draft_recommendation` uses the pooled state so auction AI dispatch works.
- `web/templates/live_draft.html`: full auction dashboard — stat cards (remaining/max bid/
  slots/inflation), budget board, best-available with position filter, sales with
  bargain/overpay badges, run alert, auto-AI toggle throttled to 90s.
- Engine fix found during verification: blending with ESPN crowd values broke the cash-sum
  invariant → added normalization in `calculate_auction_values` (rosterable pool total
  rescaled to `teams × budget`, $1 floors kept). Inflation now starts ~1.0 and moves honestly.
- Verified: 28/28 espn_api football unit tests pass; auction smoke test (scratchpad
  `auction_smoke.py`) covers engine invariants, budget/max-bid math, run detection, needs,
  value deltas, API payload, page render, and pool-failure fallback; browser QA confirmed the
  live dashboard, position filter, and badges against a stubbed 8-team auction league.
- Still not committed; still needs one live pass with the user's real league config.

## Original remaining-work notes (now done, kept for reference)

1. **`web/routes.py` — `api_draft_state`** (this was in progress when paused):
   - Call `get_valued_pool(league, config)` (import from `.helpers`), wrap in try/except with
     `({}, None, None, None)` fallback; construct
     `DraftState(league, pool=pool, budget=budget, targets=targets, roster_size=roster_size)`.
   - Accept `?team=` query param (fall back to `config.get("team_name")`).
   - Extend the JSON payload with: `is_auction`, `inflation`, `budgets` (list from
     `get_budgets()`), `best_available` (top ~40 from `get_available_ranked(40)`, each has
     name/position/team/tier/value/espn_value/adjusted_value), `my_needs`
     (`get_team_needs(team)` when team given), `active_run`.
   - `recent` picks already carry `position`/`bid_amount`/`value_delta` via the new pick dicts —
     just pass them through. Add `price`+`position` to the `team_picks` entries.
   - `api_draft_recommendation` needs no change (dispatch happens inside `get_ai_recommendation`),
     but it must also build the state WITH the pool (same as api_draft_state) or the auction path
     never triggers.

2. **`web/templates/live_draft.html` — auction dashboard redesign** on the existing polling
   skeleton (keep `startPolling`/`stopPolling`/`fetchDraftState`):
   - Top stat row (`.stat-card`s): My Remaining $, My Max Bid, Slots Left, Inflation % — populated
     from `budgets` (find my team) + `inflation`.
   - Budget board: every team spent/remaining/max_bid with `.bar-cell` bars, my team highlighted.
   - Best Available board: position filter buttons (All/QB/RB/WR/TE/K/D-ST, client-side filter),
     columns: player (pos-badge), tier badge, $value, ESPN $, adjusted $.
   - Recent sales: price + value-delta badge (green `bargain +$x` / red `overpay -$x`), run alert
     banner when `active_run` (e.g., "3 of last 5 sales were RBs").
   - AI panel: keep on-demand button; add auto-advice toggle (default ON) — trigger
     `getAIRecommendation()` when `summary.total_picks` increases AND ≥90s since last call
     (`lastAiCall` timestamp guard). Hide auction-only cards when `is_auction` is false.
   - Reuse theme components already in `style.css`: `.stat-card`, `.bar-cell`, `.pos-badge`,
     `.rank-medal`. Chart.js not needed here.

3. **Verification** (scratchpad is session-specific — recreate venv):
   ```
   python3 -m venv <scratchpad>/venv && <scratchpad>/venv/bin/pip install flask requests pytest
   <scratchpad>/venv/bin/python -m pytest tests/football/unit   # espn_api regressions
   python3 -m py_compile fantasy_football_analyzer/*.py fantasy_football_analyzer/web/*.py
   ```
   - Extend the smoke-harness pattern (previous one at old scratchpad `smoke.py`, gone in new
     session — rebuild: FakeLeague with `settings.auction_budget=200`, `settings.draft_type='AUCTION'`,
     `position_slot_counts`, picks carrying `bid_amount`, fake `free_agents()`):
     assert `spent + remaining == budget` per team, `max_bid == remaining - (slots_left - 1)`,
     inflation ≈ remaining cash / remaining value, needs shrink as positions drafted,
     `value_delta == expected - bid`; GET `/live-draft` and `/api/draft-state` → 200 with new fields.
   - Browser preview of live_draft page with stub data; verify JS updates + AI throttle fires ≤1/90s.
   - Note: browser-pane screenshots can go black (`visibilityState: hidden` pane bug) — content is
     still verifiable via `read_page` a11y tree; a fresh `navigate` recovers rendering.

## Design decisions already settled

- Dollar model: every rosterable slot ≥ $1; discretionary = `teams×budget − teams×roster_size`,
  distributed by positive-VBD share; blended 50/50 with ESPN `auction_value_avg` when present.
- Inflation = Σ remaining budgets ÷ Σ base values of top-(remaining slots) available players.
- Baselines from league's real slots: flex split RB 40/WR 45/TE 15; bench split QB 10/RB 40/WR 35/TE 10/DST 5.
- AI auto-advice: client-side trigger on new picks, throttled ≥90s, toggle default ON.
- No ESPN config on this machine (`~/.fantasy_football_analyzer.json` absent) — final live test
  needs the user's league id/cookies.

## Model review, Sep 3 2026 — what changed and the backtest

Valuation pipeline (`auction.py`, `lineup.py`, `sources.enrich_pool`):
- Replacement baseline = last **starter** (fixed slots + flex share), not last bench player.
  Two-segment VBD: dollars above the starter line plus a `DEPTH_DOLLAR_SHARE = 0.10` depth
  allowance down to the roster line — no $1 cliff. Slot truth lives in `lineup.slot_profile`
  (recognizes RB/WR/TE, FLEX, RB/WR, WR/TE, OP; positions with no slot are never priced;
  IDP/P/HC slots reserved at `NONCORE_SLOT_PRICE`; normalization runs over core slots only).
- `CAPPED_POSITIONS`: K and D/ST top-2 at $2, the rest $1, applied after blending.
- External dollar signals scaled by `budget / EXTERNAL_BUDGET_BASIS (200)`; explicit blend
  `W_MODEL, W_CROWD, W_EXPERT = 0.45, 0.20, 0.35` (renormalized over signals present).
- `AVAILABILITY` multipliers from ESPN/Sleeper status (IR .35, PUP .65, SUSP .80, OUT .85,
  D .92, Q .97, inactive .20) applied to the blended value; shown on the block.
- Byes from the pro schedule (`bye` on every entry, collision flag on the block); lineup-aware
  need on the block (`lineup.marginal_value` → `need_mult` in [0.5, 1]).
- FantasyPros best/worst → `ceiling_value` / `floor_value` (display + AI context only).

Backtest against the Papa Trump League's real 2025 auction (215 priced picks, $300 budget,
`tools/backtest_2025.py`):

| variant | ρ(value, price) | ρ(value, actual pts) | MAE | top-30 MAE |
|---|---|---|---|---|
| old (bench baseline, unscaled blend) | 0.824 | 0.543 | $9.2 | $26.1 |
| **new defaults** | **0.839** | **0.569** | **$7.1** | **$18.1** |
| new, depth 0.05 | 0.835 | 0.571 | $7.3 | $18.1 |
| new, depth 0.15 | 0.842 | 0.567 | $7.0 | $18.2 |
| new, no K/DST cap | 0.817 | 0.589 | $7.2 | $18.1 |

Reference: the room's own prices predicted 2025 actual points at ρ 0.504; raw ESPN projection
0.727. Model dollar share RB 53 / WR 28 vs the room's RB 40 / WR 46 — the room pays a WR
premium the projections don't support; the market model (`positional_premiums`) carries that
into *price*, while *value* stays projection-based. Caveat: ESPN's 2025 season projection row
may carry in-season updates (applies equally to every variant).

Rollback image for draft day: `ffa:pre-review`
(`docker rm -f ffa; docker run -d --name ffa -p 5050:5000 -v ffa-config:/root --env-file .env ffa:pre-review`).

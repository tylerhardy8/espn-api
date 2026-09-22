# In-season waiver and FAAB advisory model

Papa Trump League (202314, 2026) uses team ID **14**, currently **Kaitlan Callin' Audibles**. This update is on PR #1's existing branch; it does not submit ESPN claims or deploy the Railway application.

## Behavior

- The entire roster participates in `lineup.marginal_value()`. Each candidate also receives separately reported starting/flex gain, depth, no-reserve insurance, and actual future-week bye/injury coverage. Two excellent RBs in two required RB slots still trigger a depth need. A current-week bye does not discard an otherwise useful ROS target.
- Roster counts and targets derive from the league's slots, including two flexes and six bench spots. DL/DB aliases, other IDP, P and HC are supported. D/ST is excluded when there is no eligible slot. Unknown active slots stop bid generation rather than silently becoming another position.
- Identity resolution uses explicit team ID, then the active profile's ID, then an unambiguous legacy name. The response exposes the resolved ID/current name. An invalid configured ID never falls through to another team. Profiles retain `team_id` across saving and switching.
- Positive initial FAAB, enabled FAAB, and reported spending are required. Remaining balance is derived from initial budget minus reported spending and checked against a reported remaining balance when present. Missing counters are distinguishable from a reported zero. Negative, fractional, boolean, nonfinite, contradictory balances, or a Papa budget other than $150 stop bids. A missing rival balance is labeled unknown, not full budget.
- Sparse/out-of-order ESPN slot IDs are mapped directly. Football `refresh()` retains football Settings instead of discarding roster slots. Web/API/AI requests refresh roster/settings before generating bids; there is no response cache serving old bids.
- Weekly values use the current week through the league's final fantasy week. Published ESPN weekly projections override a league-scored season baseline; known byes are zero. ROS points are divided by **remaining playable weeks**, never 17. Full-season baseline / 17 NFL games is a different calculation. ESPN scoring and FantasyPros positional scoring preserve the TE reception premium. Draft and trade valuation helpers are unchanged.
- Available BoxPlayers are enriched with the full NFL schedule when available, so future byes do not depend solely on their current-week flag. Missing bye data is explicitly unknown. The candidate pool is the first 1,000 ESPN free agents/waiver players; every eligible positive-projection player in that response is evaluated. `pool` exposes scope, count and unknown-bye count. Unpublished projections are not evidence that a player is worthless.
- Replacement depends on league size, positional starter/flex targets, rostered counts, bench targets and available pool depth. It is not FA5 everywhere. Output exposes its rank, pool size and per-game value.
- Bid shares respond continuously to starting and bench marginal value. Depth/fragility/coverage contributions are capped; a zero-starting-gain depth add has an 8% adjusted central-budget-share ceiling. Low/recommended/high are ordered and each is capped at verified remaining FAAB. Rivals use their own counts, reserve shortfall, flex opportunity, injury/bye coverage and cash. Ranges are estimates, not predicted winning bids.
- All waiver acquisitions are marked ineligible for the 2027 franchise tag. No draft or trade eligibility behavior is changed.

## Inputs and endpoints

Set `"team_id": 14` in the active Papa league profile. Existing name-only profiles still work when the name uniquely matches. The page's selector submits a stable ID; `/api/waivers` exposes the matched ID so legacy clients can retain it. The extension resets its ID when changing leagues.

- `GET /waivers?team_id=14&max_spend=20`: roster-aware page and draft plan.
- `GET /api/waivers?team_id=14`: structured current-week response, including validated FAAB and a draft claim plan. The default run cap is the remaining balance, **not a recommendation to spend it all**.
- `POST /api/waiver-plan`: refresh and validate a custom ordered list. Body example:

```json
{
  "team_id": 14,
  "max_spend": 20,
  "claims": [
    {"player_id": 500, "bid": 4, "drop_player_id": 42, "role": "primary", "alternative_group": "RB"},
    {"player_id": 501, "bid": 4, "drop_player_id": 41, "role": "fallback", "alternative_group": "RB"}
  ]
}
```

IDs above are **synthetic**. Targets must be in the refreshed eligible pool; drops must be distinct current non-IR roster members. The planner rejects missing drops when at capacity, duplicate targets, invalid bids, or any all-success total over the run cap or remaining balance. Claims retain priority, primary/fallback role, and alternative-group intent, with remaining balance after each success.

**Every listed bid is reserved, including mutually exclusive alternatives.** Group labels do not assert ESPN will enforce conditional claims. Reusing one drop for multiple alternatives is rejected until that behavior can be verified. Enter only one alternative unless ESPN's handling has been confirmed. Automatic drafts propose at most three targets and show each drop's lost ROS points/game. Their add valuations are before drops; review the net roster effect and locks in ESPN.

## Verify in ESPN before Tuesday

1. The active team is ID 14 and the current balance/spending matches ESPN; initial FAAB is $150. This conservative model does not infer traded FAAB or balance adjustments from missing fields. Investigate a discrepancy instead of treating this model as authoritative.
2. Minimum bid (including whether $0 is legal), tied-bid priority, processing order, conditional-claim/drop reuse behavior, player eligibility and locked/cannot-drop players.
3. Current injuries, projected return dates, unpublished future projections and unknown byes; review Week 13 coverage for Taylor, Hall, Flowers and Bowers.
4. The intended processing time is Tuesday **11:00 a.m. America/New_York**. This is configured Papa league metadata from the user, **not API-verified scheduling**. The app neither schedules nor submits claims.
5. Review the all-success spend, every drop, and fallback behavior immediately before entering claims. A refresh is a snapshot, not an account balance reservation.

## Modeling assumptions that affect bids

These coefficients are planning priors and have not been calibrated to this league's winning bids:

- Existing flex/bench target shares; depth premium is 6% of per-game value per missing roster-target player, capped at 20%; no-reserve insurance adds 12%. Coverage uses weekly optimized-lineup gains above baseline, averaged over remaining weeks, capped at 25% of per-game value.
- Replacement rank is unfilled league starter demand plus one quarter of league expected positional reserve demand, rounded up and limited to the observed pool.
- Central base share is 2.5% per starting-gain point plus a capped 6% bench/depth/coverage component and up to 4% replacement premium gated by starting gain. Historical aggressiveness is bounded at 0.5–2.0; rivalry at 1.0–1.2; season clock rises gradually to under 1.6. Final central share has an additional 8% plus 3% per starting-gain-point ceiling, and an absolute 80% ceiling. Estimated bands are 0.7–1.3 times the central amount, rounded and capped to cash.
- Future unpublished weeks use league-scored season projection / 17 NFL games. There is no extrapolated actual season-average or playoff weighting in this waiver path. A missing season projection contributes zero to unpublished weeks. A missing final fantasy-week schedule falls back to Week 17.
- OUT/IR/suspended players are set to zero this week; their future return date is unknown, and future projections are not a recovery prediction. This may overvalue prolonged injuries. Review before spending.
- Available-player scope is the ESPN response, not an exhaustive guarantee about unprojected or omitted players. ESPN scoring totals are trusted; external FantasyPros stats use the existing league-aware scoring implementation (including its kicker approximation).

The UI/API expose these uncertainties and explicit “verify in ESPN” fields. AI receives the validated response and instructions to explain rather than invent balances, rules or new bid amounts. Its prose is advisory and does not execute anything.

## Synthetic acceptance example

See [examples/papa-waivers.json](examples/papa-waivers.json), generated from `tests/analyzer/waiver_fixture.py`. **Not live data:** $150 initial, $47 spent, $103 remaining. Lower-projected RBs lead the recommendations despite Taylor/Hall projecting 20/19 points per game. The example uses a $20 run cap and reports ordered claims, distinct drops and the conservative all-success total. Available-player names, rival rosters and balances are synthetic.

## Validation

Core command requested in the task:

```sh
uv run --no-project --with pytest --with requests --with requests_mock python -m pytest tests/analyzer tests/football/unit
```

Web route/template tests additionally require the application's Flask/feedparser dependencies:

```sh
uv run --no-project --with pytest --with requests --with requests_mock --with flask --with feedparser python -m pytest tests/analyzer tests/football/unit tests/web/test_waiver_api.py
```

Tests cover depth below Taylor/Hall, Week 13, current byes, injuries, identity/migration, invalid balances, exact sparse slots, TE scoring, noncore slots/no DST, monotonic capped bids, thin rivals, all-success planning, refresh failure, structured provenance, API validation and page rendering. Results are recorded in the PR description.

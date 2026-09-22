# League history coverage and integrity

The History page, history CLI command and strategy-history loader now discover **all seasons ESPN advertises**, including earlier seasons advertised by another past-season response. There is no one-season default or four-season strategy cutoff. Explicit year filters still restrict the requested scope. Repeated season IDs are deduplicated.

## What is checked

- By Team joins a franchise by league/team ID across renames and ownership changes. By Manager joins the stable owner-ID group across display-name changes. Co-owners are treated as one ownership group. Identical names with different IDs stay separate. Missing owner IDs are disclosed and do not trigger guessed cross-season merges.
- Titles require completed seasons and an explicit final rank of 1. Current standings do not award a championship. Average finishes and the finish chart use confirmed final ranks only. Half a win is credited for ties.
- Scoring, head-to-head and all-play calculations exclude undecided games and self-opponent playoff byes; valid zero and negative scores count. Matchups align using ESPN matchup-period IDs, so gaps/byes cannot shift teams into different weeks.
- These are **matchup-period totals**, not necessarily weekly scores: a two-week playoff matchup counts once. Season win/loss/PF/PA records come from ESPN and may have a different scope, particularly with playoffs or median-game rules. This distinction is shown in the coverage panel.
- Drafted players missing from final rosters are fetched directly. If their stats cannot be obtained, they are marked unavailable rather than scored zero. Unknown results are excluded from position rankings and draft busts. Bust rankings use completed snake drafts; auction nomination order is not draft priority. Price-per-point ratios are unavailable when draft results are incomplete.
- Missing transaction counters remain unknown, not zero-activity seasons; activity rates use only seasons with reported counters.
- Strategy uses completed seasons only; it now connects current manager IDs to historical profiles. Partial failed season loads are not cached as complete history. Coverage and missing seasons are included in AI history context.

## Coverage is visible

`GET /history` defaults to `years=all`. Its coverage panel lists loaded years, failed years, team/matchup/draft counts, incomplete final results, missing owner IDs and unavailable drafted-player stats.

`GET /api/history` exposes the same coverage plus team records, matchup scoring, activity aggregates, head-to-head, draft results and luck. It is a live read with `Cache-Control: no-store`.

Examples:

- `/history` or `/api/history`: every ESPN-advertised season.
- `/history?years=2020-2025`: only that explicit range.
- `/history?group_by=manager`: stable ownership-group history.

`all_requested_loaded` means every **requested** season loaded. It does not promise every historical data field or transaction is available. `discovery_verified` indicates ESPN provided the previous-season list; failed loads stay listed for retry. Errors do not expose authentication cookies or raw request URLs.

## Limits and live verification

This is retrieval and analysis of ESPN records, **not an immutable archive**. Scores remain subject to ESPN corrections and availability. Historical trade/acquisition/drop counts do not reconstruct individual transactions, involved players, timestamps, FAAB bids, or every weekly roster. The application must not claim that it possesses a complete historical transaction ledger. Different season scoring/lineup rules are not normalized when comparing raw point totals. Activity and points averages shown on the general History page include any selected partial season; they are not pace-adjusted. Strategy-rate calculations exclude partial seasons.

A read-only request to Papa Trump League 202314 in this checkout returned **HTTP 401 without authentication**. The available local configuration/environment contained no ESPN authentication cookies. Therefore this change verifies discovery and calculations using fixtures and mocked ESPN responses; **it does not certify the real league's full season list or validate the Railway instance's credentials**. A successful authenticated load must be checked in the coverage panel before calling the real history complete. No Railway deployment or credential change is included.

## Positional waiver coverage

RB was the motivating thin-roster example, not the only supported depth position. Regression tests now explicitly exercise reserve scarcity and future bye coverage at QB, RB, WR, TE, K, LB, DL, DB, P and HC. Flex slots participate in the shared lineup calculation. Positional targets and capped premiums do not mean a team should carry a bench backup at every position; those spots compete for six bench slots.

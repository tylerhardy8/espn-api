"""Real-time draft tracker that polls ESPN for new picks.

Monitors a live draft, tracks picks as they happen, maintains the board
state (available players, positional runs, team needs), and feeds context
to the Claude AI advisor for intelligent recommendations.
"""

import time
from collections import defaultdict
from datetime import datetime

from espn_api.football import League

from .draft import VBD_BASELINES, ROSTER_TARGETS


class DraftState:
    """Maintains the current state of a live draft.

    When constructed with a valued player pool (see auction.build_valued_pool),
    the state becomes auction-aware: it knows player positions, dollar values,
    per-team budgets, and can compute inflation-adjusted values.
    """

    def __init__(self, league, pool=None, budget=None, targets=None, roster_size=None):
        self.league = league
        self.picks = []  # list of pick dicts in order
        self.available_players = {}  # player_id -> player_name
        self.drafted_ids = set()
        self.team_rosters = defaultdict(list)  # team_name -> [picks]
        self.active_run = None  # {"position", "count"} when a positional run is on
        self.round = 0
        self.pick_number = 0
        self.total_teams = len(league.teams)
        self.total_rounds = 0  # determined from settings or observed

        self.pool = pool or {}
        self.budget = budget or getattr(league.settings, "auction_budget", 0) or 200
        self.is_auction = getattr(league.settings, "draft_type", "") == "AUCTION"
        self.targets = targets or {
            pos: t["total"] for pos, t in ROSTER_TARGETS.items() if pos != "FLEX"
        }
        self.roster_size = roster_size or 16

        if self.pool:
            for pid, entry in self.pool.items():
                self.available_players[pid] = entry["name"]
        else:
            # Fallback: bare name pool from league's player_map
            for pid, name in league.player_map.items():
                if isinstance(pid, int):
                    self.available_players[pid] = name

    def _pool_entry(self, player_id):
        return self.pool.get(player_id, {})

    def apply_picks(self, draft_picks):
        """Apply the league's cumulative draft list (ESPN's full feed).

        Only processes picks beyond what's already tracked. For picks from
        non-cumulative sources (manual marks, reconstruction), use add_picks.
        """
        new_picks = draft_picks[len(self.picks):]
        for pick in new_picks:
            self._ingest(pick)
        self._after_ingest()
        return new_picks

    def add_picks(self, picks):
        """Append picks from a non-cumulative source, skipping known players."""
        added = [p for p in picks if p.playerId not in self.drafted_ids]
        for pick in added:
            self._ingest(pick)
        self._after_ingest()
        return added

    def _ingest(self, pick):
        entry = self._pool_entry(pick.playerId)
        expected = entry.get("value")
        bid = pick.bid_amount or 0
        pick_data = {
            "round": pick.round_num,
            "round_pick": pick.round_pick,
            "overall": (pick.round_num - 1) * self.total_teams + pick.round_pick,
            "player_name": pick.playerName,
            "player_id": pick.playerId,
            "position": entry.get("position", ""),
            "team_name": pick.team.team_name if pick.team else "Unknown",
            "team_id": pick.team.team_id if pick.team else 0,
            "bid_amount": bid,
            "expected_value": expected,
            "value_delta": round(expected - bid, 1) if expected is not None else None,
            "keeper": pick.keeper_status,
            "timestamp": datetime.now().isoformat(),
        }
        self.picks.append(pick_data)
        self.drafted_ids.add(pick.playerId)
        self.available_players.pop(pick.playerId, None)
        self.team_rosters[pick_data["team_name"]].append(pick_data)
        self.round = pick.round_num
        self.pick_number = pick_data["overall"]

    def _after_ingest(self):
        if any(p["bid_amount"] for p in self.picks):
            self.is_auction = True
        self._detect_position_runs()

    def _detect_position_runs(self, window=5, threshold=3):
        """Flag a positional run when >= threshold of the last `window` picks share a position."""
        self.active_run = None
        recent = [p for p in self.picks[-window:] if p.get("position")]
        if len(recent) < threshold:
            return
        counts = defaultdict(int)
        for p in recent:
            counts[p["position"]] += 1
        position, count = max(counts.items(), key=lambda kv: kv[1])
        if count >= threshold:
            self.active_run = {"position": position, "count": count}

    def get_team_needs(self, team_name):
        """Positions a team still needs: derived targets minus drafted counts."""
        drafted = defaultdict(int)
        for pick in self.team_rosters.get(team_name, []):
            if pick.get("position"):
                drafted[pick["position"]] += 1

        needs = {}
        for pos, target in self.targets.items():
            remaining = round(target) - drafted.get(pos, 0)
            if remaining > 0:
                needs[pos] = remaining
        return needs

    def get_budgets(self):
        """Per-team auction budget state, sorted by remaining budget descending."""
        budgets = []
        for team in self.league.teams:
            picks = self.team_rosters.get(team.team_name, [])
            spent = sum(p["bid_amount"] for p in picks)
            slots_left = max(0, self.roster_size - len(picks))
            remaining = max(0, self.budget - spent)
            budgets.append({
                "team": team.team_name,
                "spent": spent,
                "remaining": remaining,
                "slots_left": slots_left,
                "max_bid": max(0, remaining - max(0, slots_left - 1)),
            })
        budgets.sort(key=lambda b: b["remaining"], reverse=True)
        return budgets

    def get_inflation(self):
        """League-wide inflation multiplier for remaining pool values.

        Remaining cash chasing the top remaining players: > 1.0 means prices
        should run above baseline values, < 1.0 means bargains ahead.
        """
        if not self.pool:
            return 1.0
        budgets = self.get_budgets()
        remaining_cash = sum(b["remaining"] for b in budgets)
        remaining_slots = sum(b["slots_left"] for b in budgets)
        if remaining_slots <= 0:
            return 1.0

        available = sorted(
            (e for pid, e in self.pool.items() if pid not in self.drafted_ids),
            key=lambda e: e.get("value", 1.0),
            reverse=True,
        )[:remaining_slots]
        base = sum(max(1.0, e.get("value", 1.0)) for e in available)
        return round(remaining_cash / base, 3) if base else 1.0

    def get_available_ranked(self, limit=25, position=None):
        """Best available players by inflation-adjusted value."""
        inflation = self.get_inflation()
        available = [
            e for pid, e in self.pool.items()
            if pid not in self.drafted_ids
            and (position is None or e.get("position") == position)
        ]
        available.sort(key=lambda e: e.get("value", 0), reverse=True)
        ranked = []
        for e in available[:limit]:
            ranked.append({
                **e,
                "adjusted_value": round(e.get("value", 1.0) * inflation, 1),
            })
        return ranked

    def get_my_slot(self, team_name):
        """(slot, total_slots) for a team in the draft order, or (None, total)."""
        order = getattr(self.league.settings, "draft_pick_order", None) or []
        if not order:
            return None, self.total_teams
        by_id = {t.team_id: t.team_name for t in self.league.teams}
        for slot, team_id in enumerate(order, 1):
            if by_id.get(team_id, "").lower() == team_name.lower():
                return slot, len(order)
        return None, len(order)

    def get_upcoming_picks(self, team_name, count=4):
        """The team's next overall pick numbers in a snake draft.

        Starts after the last completed pick. Returns [] if the draft order
        is unknown.
        """
        slot, total = self.get_my_slot(team_name)
        if slot is None or total <= 0:
            return []
        picks = []
        p = len(self.picks) + 1
        while len(picks) < count and p <= total * 30:  # generous round cap
            rnd = (p - 1) // total + 1
            idx = (p - 1) % total
            slot_at_p = idx + 1 if rnd % 2 == 1 else total - idx
            if slot_at_p == slot:
                picks.append(p)
            p += 1
        return picks

    def get_board_summary(self):
        """Get a summary of the current draft board state."""
        return {
            "total_picks": len(self.picks),
            "current_round": self.round,
            "current_pick": self.pick_number,
            "players_available": len(self.available_players),
            "players_drafted": len(self.drafted_ids),
            "teams": self.total_teams,
        }

    def get_recent_picks(self, count=10):
        """Return the most recent picks."""
        return self.picks[-count:] if self.picks else []

    def get_team_picks(self, team_name):
        """Return all picks for a specific team."""
        return self.team_rosters.get(team_name, [])

    def format_pick(self, pick_data):
        """Format a single pick for display."""
        return (
            f"  Round {pick_data['round']}, Pick {pick_data['round_pick']} "
            f"(#{pick_data['overall']}): {pick_data['player_name']} -> "
            f"{pick_data['team_name']}"
        )


class _SyntheticPick:
    """Mimics BasePick for picks reconstructed from the free-agent pool."""

    def __init__(self, team, player_id, player_name, round_num, round_pick,
                 bid_amount=0):
        self.team = team
        self.playerId = player_id
        self.playerName = player_name
        self.round_num = round_num
        self.round_pick = round_pick
        self.bid_amount = int(bid_amount or 0)
        self.keeper_status = False
        self.nominatingTeam = None


_synth_cache = {}  # league_id -> (frozenset(gone_ids), picks)


def synthesize_picks_from_pool(league, pool):
    """Reconstruct draft picks when ESPN's draft feed lags a live draft.

    Players who have left the free-agent pool are drafted; a player-card
    batch lookup recovers which team took each one (onTeamId). Pick order
    is approximated by ADP (true order isn't available), so round/pick
    numbers are estimates but the drafted set and total count are exact.
    """
    if not pool:
        return []

    try:
        agents = league.free_agents(size=600)
    except Exception:
        return []
    if not agents:
        return []  # empty FA response is an API hiccup, not a fully-drafted pool
    available_ids = {p.playerId for p in agents}
    gone = [pid for pid in pool if pid not in available_ids]
    if not gone:
        return []

    cache_key = getattr(league, "league_id", 0)
    cached = _synth_cache.get(cache_key)
    if cached and cached[0] == frozenset(gone):
        return cached[1]

    # Who has them: batch player cards for onTeamId
    on_team = {}
    for i in range(0, len(gone), 50):
        try:
            fetched = league.player_info(playerId=gone[i:i + 50]) or []
        except Exception:
            continue
        if not isinstance(fetched, list):
            fetched = [fetched]
        for player in fetched:
            on_team[player.playerId] = getattr(player, "onTeamId", 0)

    teams_by_id = {t.team_id: t for t in league.teams}
    num_teams = max(1, len(league.teams))

    # Approximate draft order by ADP (unknown order otherwise)
    def adp_key(pid):
        adp = pool[pid].get("adp") or 0
        return adp if adp > 0 else 9999

    picks = []
    for idx, pid in enumerate(sorted(gone, key=adp_key)):
        team = teams_by_id.get(on_team.get(pid, 0))
        picks.append(_SyntheticPick(
            team,
            pid,
            pool[pid]["name"],
            idx // num_teams + 1,
            idx % num_teams + 1,
        ))

    _synth_cache[cache_key] = (frozenset(gone), picks)
    return picks


class DraftTracker:
    """Polls ESPN API and tracks a live draft in real time."""

    def __init__(self, league, poll_interval=10):
        self.league = league
        self.state = DraftState(league)
        self.poll_interval = poll_interval
        self.is_running = False
        self.on_new_pick = None  # callback: fn(pick_data, state) -> None
        self.on_draft_complete = None  # callback: fn(state) -> None

    def check_for_updates(self):
        """Poll ESPN once for new draft picks. Returns list of new picks."""
        old_count = len(self.league.draft)
        self.league.draft = []  # reset so _fetch_draft appends fresh
        self.league.refresh_draft()

        new_picks = self.state.apply_picks(self.league.draft)
        return new_picks

    def run(self):
        """Start the live draft tracking loop.

        Polls ESPN at the configured interval, processes new picks,
        and invokes callbacks. Runs until draft completes or interrupted.
        """
        self.is_running = True
        print(f"Draft tracker started. Polling every {self.poll_interval}s...")
        print(f"League: {self.league.settings.name if hasattr(self.league.settings, 'name') else self.league.league_id}")
        print(f"Teams: {len(self.league.teams)}")
        print()

        # Initial load
        if self.league.draft:
            new_picks = self.state.apply_picks(self.league.draft)
            if new_picks:
                print(f"Loaded {len(new_picks)} existing pick(s).")
                for pick in self.state.picks[-5:]:
                    print(self.state.format_pick(pick))
                print()

        try:
            while self.is_running:
                new_picks = self.check_for_updates()

                if new_picks:
                    for pick in new_picks:
                        pick_data = self.state.picks[-len(new_picks) + new_picks.index(pick)]
                        print(self.state.format_pick(pick_data))

                        if self.on_new_pick:
                            self.on_new_pick(pick_data, self.state)

                summary = self.state.get_board_summary()
                if summary["total_picks"] > 0 and not new_picks:
                    # Check if draft might be complete
                    pass

                time.sleep(self.poll_interval)

        except KeyboardInterrupt:
            print("\nDraft tracker stopped.")
            self.is_running = False

        if self.on_draft_complete:
            self.on_draft_complete(self.state)

    def stop(self):
        """Stop the tracking loop."""
        self.is_running = False

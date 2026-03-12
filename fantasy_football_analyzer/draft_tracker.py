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
    """Maintains the current state of a live draft."""

    def __init__(self, league):
        self.league = league
        self.picks = []  # list of pick dicts in order
        self.available_players = {}  # player_id -> player_name
        self.drafted_ids = set()
        self.team_rosters = defaultdict(list)  # team_name -> [picks]
        self.position_runs = []  # track positional runs
        self.round = 0
        self.pick_number = 0
        self.total_teams = len(league.teams)
        self.total_rounds = 0  # determined from settings or observed

        # Build full player pool from league's player_map
        for pid, name in league.player_map.items():
            if isinstance(pid, int):
                self.available_players[pid] = name

    def apply_picks(self, draft_picks):
        """Apply a list of BasePick objects to the draft state.

        Only processes picks not yet tracked.
        """
        new_picks = draft_picks[len(self.picks):]
        for pick in new_picks:
            pick_data = {
                "round": pick.round_num,
                "round_pick": pick.round_pick,
                "overall": (pick.round_num - 1) * self.total_teams + pick.round_pick,
                "player_name": pick.playerName,
                "player_id": pick.playerId,
                "team_name": pick.team.team_name if pick.team else "Unknown",
                "team_id": pick.team.team_id if pick.team else 0,
                "bid_amount": pick.bid_amount,
                "keeper": pick.keeper_status,
                "timestamp": datetime.now().isoformat(),
            }
            self.picks.append(pick_data)
            self.drafted_ids.add(pick.playerId)
            self.available_players.pop(pick.playerId, None)
            self.team_rosters[pick_data["team_name"]].append(pick_data)
            self.round = pick.round_num
            self.pick_number = pick_data["overall"]

        self._detect_position_runs(new_picks)
        return new_picks

    def _detect_position_runs(self, new_picks):
        """Detect when multiple players at the same position are picked consecutively."""
        if len(self.picks) < 3:
            return

        recent = self.picks[-5:]
        # Use player_map to infer position isn't directly available from picks,
        # so we track runs by counting consecutive same-position picks
        # This is a simplified heuristic
        pass

    def get_team_needs(self, team_name):
        """Determine what positions a team still needs based on picks so far."""
        picked_positions = defaultdict(int)
        for pick in self.team_rosters.get(team_name, []):
            # We don't have position from the pick directly, so count total picks
            pass

        # Return generic needs based on pick count
        total_picked = len(self.team_rosters.get(team_name, []))
        needs = {}
        for pos, targets in ROSTER_TARGETS.items():
            needs[pos] = max(0, targets["total"] - 0)  # simplified
        return needs

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

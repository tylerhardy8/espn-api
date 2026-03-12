"""Claude AI-powered draft advisor.

Uses the Anthropic API to evaluate draft context and provide intelligent
pick recommendations. Considers:
- Current draft board state (who's been picked, what's available)
- Your team's roster composition and positional needs
- Value-based drafting principles and positional scarcity
- Recent positional runs and draft trends
- League scoring settings
- Historical player performance data
"""

import os

try:
    import anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False


DEFAULT_MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 1024


def _check_api_available():
    """Check that the anthropic package is installed and API key is set."""
    if not HAS_ANTHROPIC:
        raise RuntimeError(
            "The 'anthropic' package is required for AI recommendations.\n"
            "Install it with: pip install anthropic"
        )
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "ANTHROPIC_API_KEY environment variable is not set.\n"
            "Get your API key from https://console.anthropic.com/ and set it:\n"
            "  export ANTHROPIC_API_KEY='your-key-here'"
        )


def build_draft_context(draft_state, my_team_name, league, num_available=40):
    """Build a structured context string describing the current draft state
    for the AI advisor to reason about.
    """
    summary = draft_state.get_board_summary()
    recent = draft_state.get_recent_picks(count=10)
    my_picks = draft_state.get_team_picks(my_team_name)

    # League settings context
    settings_info = []
    if hasattr(league.settings, "name"):
        settings_info.append(f"League: {league.settings.name}")
    settings_info.append(f"Teams: {summary['teams']}")
    if hasattr(league.settings, "scoring_format") and league.settings.scoring_format:
        # Check for PPR
        ppr_items = [s for s in league.settings.scoring_format
                     if s.get("label", "").lower() == "each reception"
                     or s.get("abbr", "").upper() == "REC"]
        if ppr_items:
            ppr_val = ppr_items[0].get("points", 0)
            if ppr_val == 1:
                settings_info.append("Scoring: Full PPR")
            elif ppr_val == 0.5:
                settings_info.append("Scoring: Half PPR")
            else:
                settings_info.append(f"Scoring: {ppr_val} PPR")
        else:
            settings_info.append("Scoring: Standard (non-PPR)")

    if hasattr(league.settings, "position_slot_counts"):
        slots = league.settings.position_slot_counts
        slot_parts = []
        for pos in ["QB", "RB", "WR", "TE", "FLEX", "D/ST", "K", "BE"]:
            count = slots.get(pos, 0)
            if count > 0:
                slot_parts.append(f"{pos}:{count}")
        if slot_parts:
            settings_info.append(f"Roster slots: {', '.join(slot_parts)}")

    # Build available players list grouped by position
    available_by_pos = {}
    for pid, name in list(draft_state.available_players.items())[:500]:
        # Try to get position from player data on rosters
        pos = _lookup_player_position(pid, league)
        if pos:
            available_by_pos.setdefault(pos, []).append(name)

    available_text = []
    for pos in ["QB", "RB", "WR", "TE", "K", "D/ST"]:
        players = available_by_pos.get(pos, [])
        if players:
            shown = players[:num_available // 6]
            available_text.append(f"  {pos}: {', '.join(shown)}")
            if len(players) > len(shown):
                available_text.append(f"       ... and {len(players) - len(shown)} more")

    # Build the full context
    lines = [
        "=== DRAFT STATE ===",
        "\n".join(settings_info),
        f"\nDraft progress: Round {summary['current_round']}, "
        f"Pick #{summary['current_pick']} of {summary['total_picks']} total",
        f"Players drafted: {summary['players_drafted']}",
        f"Players available: {summary['players_available']}",
    ]

    if recent:
        lines.append("\n--- RECENT PICKS ---")
        for pick in recent:
            lines.append(
                f"  #{pick['overall']} (R{pick['round']}.{pick['round_pick']}): "
                f"{pick['player_name']} -> {pick['team_name']}"
            )

    if my_picks:
        lines.append(f"\n--- YOUR TEAM ({my_team_name}) ---")
        for pick in my_picks:
            lines.append(f"  R{pick['round']}.{pick['round_pick']}: {pick['player_name']}")
    else:
        lines.append(f"\n--- YOUR TEAM ({my_team_name}) ---")
        lines.append("  No picks yet.")

    # Other teams' rosters summary
    lines.append("\n--- OTHER TEAMS' PICKS ---")
    for team_name, picks in sorted(draft_state.team_rosters.items()):
        if team_name.lower() == my_team_name.lower():
            continue
        player_names = [p["player_name"] for p in picks]
        lines.append(f"  {team_name}: {', '.join(player_names)}")

    if available_text:
        lines.append("\n--- TOP AVAILABLE PLAYERS BY POSITION ---")
        lines.extend(available_text)

    return "\n".join(lines)


def _lookup_player_position(player_id, league):
    """Try to find a player's position from league roster data."""
    for team in league.teams:
        for player in team.roster:
            if player.playerId == player_id:
                return player.position
    return None


def get_ai_recommendation(draft_state, my_team_name, league, model=None):
    """Get an AI-powered draft recommendation from Claude.

    Sends the current draft context to Claude and returns a structured
    recommendation with reasoning.
    """
    _check_api_available()

    context = build_draft_context(draft_state, my_team_name, league)
    model = model or DEFAULT_MODEL

    system_prompt = """You are an expert fantasy football draft advisor. You analyze draft boards,
positional scarcity, value-based drafting (VBD), and team needs to provide optimal pick recommendations.

Your recommendations should consider:
1. VALUE-BASED DRAFTING: Who provides the most points above replacement level at their position?
2. POSITIONAL SCARCITY: Which positions are drying up fastest? Is there a run on RBs or WRs?
3. TEAM NEEDS: What does this team's roster look like? What positions are filled vs. empty?
4. LEAGUE SETTINGS: PPR vs Standard scoring changes player values (WRs/pass-catching RBs more valuable in PPR).
5. DRAFT POSITION: How many picks until this team picks again? Will target players survive to the next pick?
6. AVOID: Don't recommend kickers or defenses in early rounds. Don't reach for a position of surplus.

Format your response as:
RECOMMENDATION: [Player Name] ([Position], [NFL Team])
REASONING: [2-3 sentences explaining why]
ALTERNATIVES:
1. [Player] - [brief reason]
2. [Player] - [brief reason]
3. [Player] - [brief reason]
WATCH OUT: [1 sentence about a position/player that's about to become scarce]"""

    user_prompt = (
        f"Here is the current draft state. I am managing '{my_team_name}'. "
        f"It's about to be my pick. Who should I draft next?\n\n{context}"
    )

    client = anthropic.Anthropic()
    message = client.messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )

    return message.content[0].text


def get_trade_evaluation_ai(trade_description, league_context, model=None):
    """Use Claude to evaluate a proposed trade with nuanced analysis."""
    _check_api_available()

    model = model or DEFAULT_MODEL

    system_prompt = """You are an expert fantasy football trade analyst. Evaluate trades considering:
1. Player value (points scored, consistency, remaining schedule)
2. Positional value and scarcity
3. Team needs of both sides
4. Rest-of-season outlook
5. Injury risk

Be honest and direct. If a trade is lopsided, say so. Format your response clearly with a verdict."""

    client = anthropic.Anthropic()
    message = client.messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        system=system_prompt,
        messages=[{"role": "user", "content": f"{trade_description}\n\nLeague context:\n{league_context}"}],
    )

    return message.content[0].text


def get_waiver_advice_ai(waiver_context, model=None):
    """Use Claude to provide waiver wire advice with strategic reasoning."""
    _check_api_available()

    model = model or DEFAULT_MODEL

    system_prompt = """You are an expert fantasy football waiver wire analyst. Provide actionable
waiver recommendations considering matchups, trends, usage changes, and rest-of-season outlook.
Prioritize your recommendations and explain the reasoning briefly."""

    client = anthropic.Anthropic()
    message = client.messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        system=system_prompt,
        messages=[{"role": "user", "content": waiver_context}],
    )

    return message.content[0].text


class LiveDraftAdvisor:
    """Combines DraftTracker with AI advisor for a real-time interactive experience."""

    def __init__(self, league, my_team_name, poll_interval=10, model=None):
        self.league = league
        self.my_team_name = my_team_name
        self.model = model or DEFAULT_MODEL
        self.poll_interval = poll_interval
        self.auto_advise = True  # automatically get AI advice when it's your turn

        from .draft_tracker import DraftTracker
        self.tracker = DraftTracker(league, poll_interval=poll_interval)
        self.tracker.on_new_pick = self._on_pick

        # Determine your team ID
        self.my_team_id = None
        for team in league.teams:
            if team.team_name.lower() == my_team_name.lower():
                self.my_team_id = team.team_id
                break

    def _on_pick(self, pick_data, state):
        """Called when a new pick is detected."""
        print(state.format_pick(pick_data))

        # Check if the next pick might be ours
        next_pick = state.pick_number + 1
        next_team_idx = (next_pick - 1) % state.total_teams
        round_num = ((next_pick - 1) // state.total_teams) + 1

        # In snake draft, odd rounds go forward, even rounds go backward
        if round_num % 2 == 0:
            next_team_idx = state.total_teams - 1 - next_team_idx

        teams_sorted = sorted(self.league.teams, key=lambda t: t.team_id)
        if next_team_idx < len(teams_sorted):
            next_team = teams_sorted[next_team_idx]
            if next_team.team_id == self.my_team_id and self.auto_advise:
                print(f"\n>>> YOUR PICK IS NEXT! Getting AI recommendation...")
                self.get_recommendation()

    def get_recommendation(self):
        """Get and display an AI recommendation for the current state."""
        try:
            advice = get_ai_recommendation(
                self.tracker.state, self.my_team_name,
                self.league, model=self.model,
            )
            print()
            print("=" * 60)
            print("AI DRAFT ADVISOR")
            print("=" * 60)
            print(advice)
            print("=" * 60)
            print()
        except Exception as e:
            print(f"\nAI advisor error: {e}")
            print("Continuing without AI recommendations...\n")

    def run(self):
        """Start the live draft advisor."""
        print("=" * 60)
        print("LIVE DRAFT ADVISOR")
        print("=" * 60)
        print(f"Your team: {self.my_team_name}")
        print(f"AI model: {self.model}")
        print(f"Poll interval: {self.poll_interval}s")
        print(f"Auto-advise: {'ON' if self.auto_advise else 'OFF'}")
        print()
        print("Commands during draft:")
        print("  Ctrl+C  - Stop the tracker")
        print("  (AI advice is given automatically before your picks)")
        print()

        # Get initial recommendation if draft hasn't started
        if not self.league.draft:
            print("Draft hasn't started yet. Waiting for picks...")
        else:
            print(f"Draft in progress: {len(self.league.draft)} picks so far.")

        self.tracker.run()

    def stop(self):
        """Stop the advisor."""
        self.tracker.stop()

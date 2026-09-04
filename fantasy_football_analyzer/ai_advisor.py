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


DEFAULT_MODEL = "claude-opus-5"
MAX_TOKENS = 2048


def _check_api_available(api_key=None):
    """Check that the anthropic package is installed and an API key is available."""
    if not HAS_ANTHROPIC:
        raise RuntimeError(
            "The 'anthropic' package is required for AI recommendations.\n"
            "Install it with: pip install anthropic"
        )
    if not api_key and not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "No Anthropic API key configured.\n"
            "Get your API key from https://console.anthropic.com/ and either paste it\n"
            "on the Setup page (AI section) or set the ANTHROPIC_API_KEY environment variable."
        )


def _make_client(api_key=None):
    return anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()


def _league_settings_info(league, summary):
    """Shared league-settings lines (name, size, scoring, roster slots)."""
    settings_info = []
    if hasattr(league.settings, "name"):
        settings_info.append(f"League: {league.settings.name}")
    settings_info.append(f"Teams: {summary['teams']}")
    try:
        from .sources import describe_scoring
        settings_info.append(f"Scoring: {describe_scoring(league)}")
    except Exception:
        pass

    if hasattr(league.settings, "position_slot_counts"):
        slots = league.settings.position_slot_counts
        slot_parts = []
        for pos in ["QB", "RB", "WR", "TE", "FLEX", "D/ST", "K", "BE"]:
            count = slots.get(pos, 0)
            if count > 0:
                slot_parts.append(f"{pos}:{count}")
        if slot_parts:
            settings_info.append(f"Roster slots: {', '.join(slot_parts)}")

    return settings_info


def build_draft_context(draft_state, my_team_name, league, num_available=40):
    """Build a structured context string describing the current draft state
    for the AI advisor to reason about.
    """
    summary = draft_state.get_board_summary()
    recent = draft_state.get_recent_picks(count=10)
    my_picks = draft_state.get_team_picks(my_team_name)

    settings_info = _league_settings_info(league, summary)

    # Best-available: rich (valued pool) when present, bare names otherwise
    available_text = []
    if getattr(draft_state, "pool", None):
        available_text.append(
            "  Format: Name T<tier> (proj <season pts>, ADP <avg draft position>) [flags]"
        )
        for pos in ["QB", "RB", "WR", "TE", "K", "D/ST"]:
            top = draft_state.get_available_ranked(limit=6, position=pos)
            if top:
                parts = [_format_snake_entry(e) for e in top]
                available_text.append(f"  {pos}: " + " | ".join(parts))
    else:
        available_by_pos = {}
        for pid, name in list(draft_state.available_players.items())[:500]:
            pos = _lookup_player_position(pid, league)
            if pos:
                available_by_pos.setdefault(pos, []).append(name)
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

    # Draft position: where I pick, and how far away my turns are
    slot, total_slots = draft_state.get_my_slot(my_team_name)
    if slot is not None:
        upcoming = draft_state.get_upcoming_picks(my_team_name)
        next_overall = len(draft_state.picks) + 1
        lines.append("\n--- MY DRAFT POSITION ---")
        lines.append(f"  I draft from slot {slot} of {total_slots} (snake order).")
        if upcoming:
            away = upcoming[0] - next_overall
            lines.append(
                f"  My upcoming picks: {', '.join('#' + str(p) for p in upcoming)} — "
                f"next one is {away} pick(s) away (current pick on the clock: #{next_overall})."
            )
            lines.append(
                "  Judge who survives to my picks by comparing player ADP to those pick numbers."
            )

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

    if getattr(draft_state, "pool", None):
        top_available = draft_state.get_available_ranked(limit=30)
        news_lines = _player_news_lines(top_available)
        if news_lines:
            lines.append("\n--- PLAYER NEWS (recent headlines for available players) ---")
            lines.extend(news_lines)
        lines.append("\n--- DATA SOURCES IN THIS CONTEXT ---")
        lines.append("  " + _sources_summary())

    return "\n".join(lines)


def _risk_flags(e):
    """Availability / bye / ceiling flags shared by both entry formatters."""
    flags = []
    avail = e.get("availability")
    if avail is not None and avail < 1:
        flags.append(f"avail {avail:.2f}")
    if e.get("bye"):
        flags.append(f"bye {e['bye']}")
    ceil, floor = e.get("ceiling_value"), e.get("floor_value")
    if ceil and floor and ceil > floor:
        flags.append(f"ceiling ${ceil:.0f}/floor ${floor:.0f}")
    return flags


def _format_snake_entry(e):
    """'Name T2 (proj 285, ADP 14) [Q hamstring, ECR#12]' for snake contexts."""
    flags = []
    injury = (e.get("injury_status") or "").upper()
    if injury and injury not in ("ACTIVE", "NORMAL", ""):
        part = e.get("injury_body_part")
        flags.append(f"{injury}{' ' + part if part else ''}")
    if e.get("practice"):
        flags.append(f"practice {e['practice']}")
    if e.get("depth_chart"):
        flags.append(e["depth_chart"])
    if e.get("fp_ecr"):
        flags.append(f"ECR#{e['fp_ecr']}")
    flags += _risk_flags(e)
    adp = e.get("adp")
    adp_txt = f", ADP {adp:.0f}" if adp and adp > 0 else ""
    flag_txt = f" [{', '.join(flags)}]" if flags else ""
    return (f"{e['name']} T{e.get('tier', '?')} "
            f"(proj {e.get('projected_points', 0):.0f}{adp_txt}){flag_txt}")


def _lookup_player_position(player_id, league):
    """Try to find a player's position from league roster data."""
    for team in league.teams:
        for player in team.roster:
            if player.playerId == player_id:
                return player.position
    return None


def build_auction_context(draft_state, my_team_name, league):
    """Build a structured context string for an auction draft: budgets, max
    bids, inflation, tiered values, recent sales with bargain/overpay deltas.
    """
    summary = draft_state.get_board_summary()
    settings_info = _league_settings_info(league, summary)
    inflation = draft_state.get_inflation()
    budgets = draft_state.get_budgets()

    lines = [
        "=== AUCTION DRAFT STATE ===",
        "\n".join(settings_info),
        f"Auction budget per team: ${draft_state.budget} | Roster size: {draft_state.roster_size}",
        f"Players sold: {summary['players_drafted']} | "
        f"Inflation: {inflation:.2f}x "
        f"({'prices running HOT' if inflation > 1.05 else 'bargains available' if inflation < 0.95 else 'near value'})",
    ]

    lines.append("\n--- TEAM BUDGETS ---")
    lines.append(f"{'Team':<28} {'Spent':>6} {'Left':>6} {'MaxBid':>7} {'Slots':>6}")
    for b in budgets:
        marker = "  <== ME" if b["team"].lower() == my_team_name.lower() else ""
        lines.append(
            f"{b['team']:<28} ${b['spent']:>5} ${b['remaining']:>5} "
            f"${b['max_bid']:>6} {b['slots_left']:>6}{marker}"
        )

    my_picks = draft_state.get_team_picks(my_team_name)
    lines.append(f"\n--- MY ROSTER ({my_team_name}) ---")
    if my_picks:
        for p in my_picks:
            lines.append(f"  {p['player_name']} ({p.get('position') or '?'}) — ${p['bid_amount']}")
    else:
        lines.append("  No players won yet.")

    needs = draft_state.get_team_needs(my_team_name)
    if needs:
        lines.append("  Needs: " + ", ".join(f"{pos} x{n}" for pos, n in sorted(needs.items())))

    recent = draft_state.get_recent_picks(count=10)
    if recent:
        lines.append("\n--- RECENT SALES ---")
        for p in recent:
            delta = p.get("value_delta")
            tag = ""
            if delta is not None:
                tag = f" ({'bargain' if delta > 0 else 'overpay'} {delta:+.0f})"
            lines.append(
                f"  {p['player_name']} ({p.get('position') or '?'}) -> "
                f"{p['team_name']} ${p['bid_amount']}{tag}"
            )

    if draft_state.active_run:
        run = draft_state.active_run
        lines.append(f"\n!! POSITION RUN: {run['count']} of the last 5 sales were {run['position']}s")

    lines.append("\n--- BEST AVAILABLE (inflation-adjusted values) ---")
    lines.append("  Format: Name T<tier> $<adj value> [flags]. Flags: injury/practice status, "
                 "depth chart slot, ECR#<FantasyPros expert consensus rank>, trending adds")
    top_available = []
    for pos in ["QB", "RB", "WR", "TE", "K", "D/ST"]:
        top = draft_state.get_available_ranked(limit=6, position=pos)
        if not top:
            continue
        top_available.extend(top)
        parts = [_format_available_entry(e) for e in top]
        lines.append(f"  {pos}: " + " | ".join(parts))

    # Recent player news matched to the best available players
    news_lines = _player_news_lines(top_available)
    if news_lines:
        lines.append("\n--- PLAYER NEWS (recent headlines for available players) ---")
        lines.extend(news_lines)

    lines.append("\n--- DATA SOURCES IN THIS CONTEXT ---")
    lines.append("  " + _sources_summary())

    return "\n".join(lines)


def _format_available_entry(e):
    """'Name T2 $34 [Q hamstring, RB1, ECR#14]'"""
    flags = []
    injury = (e.get("injury_status") or "").upper()
    if injury and injury not in ("ACTIVE", "NORMAL", ""):
        part = e.get("injury_body_part")
        flags.append(f"{injury}{' ' + part if part else ''}")
    if e.get("practice"):
        flags.append(f"practice {e['practice']}")
    if e.get("depth_chart"):
        flags.append(e["depth_chart"])
    if e.get("fp_ecr"):
        flags.append(f"ECR#{e['fp_ecr']}")
    if e.get("trending_adds"):
        flags.append(f"trending +{e['trending_adds']}")
    flags += _risk_flags(e)
    if e.get("market_price"):
        flags.append(f"mkt ${e['market_price']:.0f}")
    flag_txt = f" [{', '.join(flags)}]" if flags else ""
    return f"{e['name']} T{e.get('tier', '?')} ${e['adjusted_value']:.0f}{flag_txt}"


def _player_news_lines(entries, max_lines=15):
    """Match recent RSS headlines to the given players; returns formatted lines."""
    try:
        from .rss_news import fetch_news, match_news_to_players
        news = fetch_news(max_items=60)
        if not news:
            return []
        names = [e["name"] for e in entries if e.get("position") != "D/ST"]
        matches = match_news_to_players(news, names)
    except Exception:
        return []

    out = []
    for name, items in matches.items():
        for item in items[:2]:
            out.append(f"  - {name}: {item.get('title', '')} ({item.get('source', '')})")
            if len(out) >= max_lines:
                return out
    return out


def _sources_summary():
    try:
        from .sources import get_sources_status
        s = get_sources_status()
    except Exception:
        s = {}
    parts = ["ESPN projections (league scoring) + ESPN crowd auction values"]
    if s.get("sleeper"):
        parts.append(f"Sleeper injuries/practice/depth charts ({s.get('trending', 0)} trending adds)")
    if s.get("fantasypros"):
        parts.append(f"FantasyPros ECR ({s.get('fp_matched', 0)} players matched, blended into $ values)")
    parts.append("RSS player news (Rotowire, FantasyPros, NBC)")
    return "; ".join(parts)


AUCTION_SYSTEM_PROMPT = """You are an expert fantasy football AUCTION draft advisor. You analyze budgets,
player dollar values, inflation, and tier scarcity to maximize projected points per dollar spent.

Your advice must consider:
1. VALUE DISCIPLINE: Compare listed (inflation-adjusted) values to likely prices. Recommend a max bid for each target and insist on walking away above it.
2. TIER URGENCY: If a tier is about to empty at a position of need, paying slight premiums beats getting shut out.
3. BUDGET LEVERAGE: Track who can outbid whom (max bids). If opponents are cash-poor, targets can be had at discounts; if one team hoards cash, expect late-draft sniping.
4. NOMINATION STRATEGY: Nominate players you DON'T want while others still have money — drain budgets on positions you've filled or players you're out on.
5. ENDGAME: When budgets thin out, identify the $1-2 players worth rostering and the slots to save for them.
6. AVOID: overpaying early out of excitement, leaving money unspent at the end, and bidding wars at positions of surplus.

Format your response as:
TARGETS NOW: [2-3 players with max bid each, e.g., "Player X — bid up to $23"]
REASONING: [2-3 sentences: values vs. room prices, tier and budget situation]
NOMINATE NEXT: [1 player to nominate and why]
BUDGET CHECK: [1 sentence on your spending pace vs. remaining needs]
WATCH OUT: [1 sentence on a run, a cash-rich rival, or a tier about to vanish]"""


# Tool cap is higher than the prompted count so Claude never trips the limit
# mid-thought (a max_uses_exceeded error block gets narrated into the advice).
WEB_SEARCH_MAX_USES = 5
WEB_SEARCH_PROMPT = """

LIVE WEB SEARCH: You have a web search tool. Before finalizing, run up to 3 quick
searches for the very latest news (injury, holdout, suspension, depth chart change,
contract) on your top 2-3 targets — the draft data above can lag same-day news.
Fold anything material into your advice and say in one short line what you checked.
If a search errors or returns nothing, just continue with the data above — do not
mention search problems in your advice."""


def _web_search_tool(model):
    """Pick the web search tool variant the model supports."""
    modern = any(tag in model for tag in ("4-6", "4-7", "4-8", "sonnet-5", "opus-5", "fable-5"))
    return {
        "type": "web_search_20260209" if modern else "web_search_20250305",
        "name": "web_search",
        "max_uses": WEB_SEARCH_MAX_USES,
    }


def _complete(client, model, system_prompt, user_prompt, web_search=False):
    """Run one advice request; with web search, continue through pause_turn stops."""
    messages = [{"role": "user", "content": user_prompt}]
    kwargs = dict(model=model, max_tokens=MAX_TOKENS, system=system_prompt)
    if web_search:
        kwargs["tools"] = [_web_search_tool(model)]

    text_parts = []
    for _ in range(4):  # initial call + up to 3 pause_turn continuations
        message = client.messages.create(messages=messages, **kwargs)
        text_parts.extend(block.text for block in message.content if block.type == "text")
        if message.stop_reason != "pause_turn":
            break
        messages.append({"role": "assistant", "content": message.content})

    return "\n".join(t for t in text_parts if t).strip()


def get_ai_recommendation(draft_state, my_team_name, league, model=None, intel_text=None,
                          web_search=False, api_key=None):
    """Get an AI-powered draft recommendation from Claude.

    Sends the current draft context to Claude and returns a structured
    recommendation with reasoning. Auction drafts (detected from league
    settings or observed bids) get auction-specific context and strategy.
    `intel_text` optionally appends a league-history intelligence block;
    `web_search=True` lets Claude check live news on its targets.
    """
    _check_api_available(api_key)

    model = model or DEFAULT_MODEL
    client = _make_client(api_key)

    if getattr(draft_state, "is_auction", False) and getattr(draft_state, "pool", None):
        context = build_auction_context(draft_state, my_team_name, league)
        try:
            from .plan import build_budget_plan, format_plan_for_ai
            plan_text = format_plan_for_ai(build_budget_plan(draft_state, my_team_name))
            if plan_text:
                context += "\n\n" + plan_text
        except Exception:
            pass
        if intel_text:
            context += "\n\n" + intel_text
        system_prompt = AUCTION_SYSTEM_PROMPT + (WEB_SEARCH_PROMPT if web_search else "")
        user_prompt = (
            f"Here is the current auction draft state. I am managing '{my_team_name}'. "
            f"Advise me on my next moves.\n\n{context}"
        )
        return _complete(client, model, system_prompt, user_prompt, web_search=web_search)

    context = build_draft_context(draft_state, my_team_name, league)
    if intel_text:
        context += "\n\n" + intel_text

    system_prompt = """You are an expert fantasy football draft advisor. You analyze draft boards,
positional scarcity, value-based drafting (VBD), and team needs to provide optimal pick recommendations.

Your recommendations should consider:
1. VALUE-BASED DRAFTING: Who provides the most points above replacement level at their position?
2. POSITIONAL SCARCITY: Which positions are drying up fastest? Is there a run on RBs or WRs?
3. TEAM NEEDS: What does this team's roster look like? What positions are filled vs. empty?
4. LEAGUE SETTINGS: PPR vs Standard scoring changes player values (WRs/pass-catching RBs more valuable in PPR).
5. DRAFT POSITION: Use the MY DRAFT POSITION section — compare each target's ADP to my upcoming
   pick numbers. If a target's ADP is well before my next pick, take them now or move on; if their
   ADP is after my next pick, they will likely survive and I can wait. At a snake turn (back-to-back
   picks), plan both picks together.
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

    if web_search:
        system_prompt += WEB_SEARCH_PROMPT
    return _complete(client, model, system_prompt, user_prompt, web_search=web_search)


def get_trade_evaluation_ai(trade_description, league_context, model=None, api_key=None):
    """Use Claude to evaluate a proposed trade with nuanced analysis."""
    _check_api_available(api_key)

    model = model or DEFAULT_MODEL

    system_prompt = """You are an expert fantasy football trade analyst. Evaluate trades considering:
1. Player value (points scored, consistency, remaining schedule)
2. Positional value and scarcity
3. Team needs of both sides
4. Rest-of-season outlook
5. Injury risk

Be honest and direct. If a trade is lopsided, say so. Format your response clearly with a verdict."""

    client = _make_client(api_key)
    message = client.messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        system=system_prompt,
        messages=[{"role": "user", "content": f"{trade_description}\n\nLeague context:\n{league_context}"}],
    )

    return message.content[0].text


def get_waiver_advice_ai(waiver_context, model=None, api_key=None):
    """Use Claude to provide waiver wire advice with strategic reasoning."""
    _check_api_available(api_key)

    model = model or DEFAULT_MODEL

    system_prompt = """You are an expert fantasy football waiver wire analyst. Provide actionable
waiver recommendations considering matchups, trends, usage changes, and rest-of-season outlook.
Prioritize your recommendations and explain the reasoning briefly."""

    client = _make_client(api_key)
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

        # Snake: is the next pick ours? Uses the league's real draft order
        # (settings.draft_pick_order) via the tracker's slot math.
        if getattr(state, "is_auction", False) or not self.auto_advise:
            return
        upcoming = state.get_upcoming_picks(self.my_team_name, count=1)
        if upcoming and upcoming[0] == len(state.picks) + 1:
            print("\n>>> YOUR PICK IS NEXT! Getting AI recommendation...")
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

"""Rest-of-season (ROS) player values for in-season decisions.

Pre-season this collapses to the season projection. Once games are played
it prorates the projection over the weeks left, blends in the player's
actual scoring pace, and weights the league's playoff weeks — for a league
where most teams make the playoffs, what a player does in weeks 15-17
matters more than an early-season week.
"""

from .auction import availability_multiplier

SEASON_WEEKS = 17
# How much of a player's ROS value comes from the playoff weeks specifically
PLAYOFF_WEIGHT = 0.4
# Blend of realized per-game pace into the remaining-weeks projection
ACTUAL_PACE_WEIGHT = 0.3


def playoff_weeks(league):
    """[scoring periods] of the playoffs, from the league's schedule settings."""
    settings = getattr(league, "settings", None)
    reg = getattr(settings, "reg_season_count", 0) or 0
    total = len(getattr(settings, "matchup_periods", None) or {}) or SEASON_WEEKS
    if not reg or total <= reg:
        return []
    return list(range(reg + 1, total + 1))


def weekly_projections(player):
    """{week: projected_points} where ESPN has published them (in-season
    ESPN usually only publishes the coming week; pre-season nothing)."""
    stats = getattr(player, "stats", None) or {}
    out = {}
    for week, row in stats.items():
        if isinstance(week, int) and week > 0 and isinstance(row, dict):
            proj = row.get("projected_points")
            if proj is not None:
                out[week] = float(proj)
    return out


def ros_projection(player, league=None):
    """Rest-of-season projected points for a player.

    remaining weeks x per-week projection, where the per-week projection is
    the season projection prorated, blended with the actual pace once games
    have been played, using ESPN's published weekly projection for any week
    that has one. Availability (injury / roster status) scales the result,
    with the playoff weeks weighted by PLAYOFF_WEIGHT.
    """
    season_proj = float(getattr(player, "projected_total_points", 0) or 0)
    current_week = int(getattr(league, "current_week", 0) or 0) if league else 0
    if current_week <= 1:
        current_week = 1
    remaining = list(range(current_week, SEASON_WEEKS + 1))
    if not remaining:
        return 0.0

    per_week = season_proj / SEASON_WEEKS
    total = float(getattr(player, "total_points", 0) or 0)
    games = max(0, current_week - 1)
    if games and total > 0:
        actual_pace = total / games
        per_week = (1 - ACTUAL_PACE_WEIGHT) * per_week + ACTUAL_PACE_WEIGHT * actual_pace

    published = weekly_projections(player)
    bye = None
    schedule = getattr(player, "schedule", None) or {}
    if schedule:
        bye = next((w for w in range(1, 15) if str(w) not in schedule and w not in schedule), None)

    entry = {"injury_status": getattr(player, "injuryStatus", "") or ""}
    avail = availability_multiplier(entry)
    playoffs = set(playoff_weeks(league)) if league else set()

    reg_pts, po_pts = 0.0, 0.0
    for week in remaining:
        pts = published.get(week, per_week)
        if bye and week == bye:
            pts = 0.0
        if week in playoffs:
            po_pts += pts
        else:
            reg_pts += pts
    if playoffs:
        # Re-weight so the playoff weeks carry PLAYOFF_WEIGHT of the value
        # per week relative to their natural share
        n_reg = sum(1 for w in remaining if w not in playoffs) or 1
        n_po = sum(1 for w in remaining if w in playoffs) or 1
        natural = n_po / (n_reg + n_po)
        boost = PLAYOFF_WEIGHT / natural if natural else 1.0
        value = reg_pts + po_pts * boost
        value *= (n_reg + n_po) / (n_reg + n_po * boost)  # keep the same total scale
    else:
        value = reg_pts + po_pts
    return round(value * avail, 2)

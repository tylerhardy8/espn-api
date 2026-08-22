"""Configuration management for the Fantasy Football Analyzer.

Supports multiple league profiles. On disk the config looks like:

    {
      "leagues": [
        {"name": "Main", "league_id": 123, "year": 2025, "team_name": "My Team"},
        {"name": "Work", "league_id": 456, "year": 2025, "team_name": "Other"}
      ],
      "active": "Main",
      "espn_s2": "...",   # ESPN cookies are per account, shared by all leagues
      "swid": "..."
    }

`load_config()` returns that structure with the *active* profile's fields
(league_id, year, team_name) also flattened to the top level, so callers can
keep reading `config["league_id"]` etc. `save_config()` folds any top-level
edits back into the active profile before writing. Legacy single-league
configs (flat league_id/year/team_name) are migrated transparently.
"""

import json
import os

DEFAULT_CONFIG_PATH = os.path.expanduser("~/.fantasy_football_analyzer.json")
DEFAULT_YEAR = 2025
PROFILE_FIELDS = ("league_id", "year", "team_name")


def _normalize(raw):
    """Return a config dict with `leagues`, `active`, and the active profile flattened."""
    cfg = dict(raw or {})
    leagues = [dict(l) for l in (cfg.get("leagues") or []) if l.get("league_id")]

    # Migrate a legacy flat config into a single profile
    if not leagues and cfg.get("league_id"):
        leagues = [{
            "name": cfg.get("league_name") or f"League {cfg['league_id']}",
            "league_id": cfg["league_id"],
            "year": cfg.get("year", DEFAULT_YEAR),
            "team_name": cfg.get("team_name", ""),
        }]

    cfg["leagues"] = leagues
    profile = get_active_profile(cfg)
    if profile:
        cfg["active"] = profile["name"]
        for field in PROFILE_FIELDS:
            cfg[field] = profile.get(field, DEFAULT_YEAR if field == "year" else "")
    else:
        cfg["active"] = None
    return cfg


def get_active_profile(config):
    """The active league profile dict, or None if no leagues are configured."""
    leagues = config.get("leagues") or []
    if not leagues:
        return None
    active = config.get("active")
    return next((l for l in leagues if l.get("name") == active), leagues[0])


def load_config(path=None):
    """Load configuration from a JSON file (normalized, see module docstring)."""
    path = path or DEFAULT_CONFIG_PATH
    raw = {}
    if os.path.exists(path):
        with open(path, "r") as f:
            raw = json.load(f)
    return _normalize(raw)


def save_config(config, path=None):
    """Save configuration, folding top-level league fields into the active profile."""
    path = path or DEFAULT_CONFIG_PATH
    cfg = _normalize(config)

    profile = get_active_profile(cfg)
    if profile:
        for field in PROFILE_FIELDS:
            if field in config and config[field] not in (None, ""):
                profile[field] = config[field]

    canonical = {k: v for k, v in cfg.items() if k not in PROFILE_FIELDS and k != "league_name"}
    with open(path, "w") as f:
        json.dump(canonical, f, indent=2)


def get_league_config(config):
    """Extract league connection parameters for the active profile."""
    return {
        "league_id": config.get("league_id"),
        "espn_s2": config.get("espn_s2"),
        "swid": config.get("swid"),
    }


# ---------------------------------------------------------------------------
# Profile management
# ---------------------------------------------------------------------------

def add_league(config, name, league_id, year=None, team_name="", make_active=True):
    """Add (or replace by name) a league profile. Returns the normalized config."""
    name = (name or f"League {league_id}").strip()
    leagues = [l for l in (config.get("leagues") or []) if l.get("name") != name]
    leagues.append({
        "name": name,
        "league_id": int(league_id),
        "year": int(year or DEFAULT_YEAR),
        "team_name": (team_name or "").strip(),
    })
    config["leagues"] = leagues
    if make_active or not config.get("active"):
        config["active"] = name
    for field in PROFILE_FIELDS:
        config.pop(field, None)
    return _normalize(config)


def remove_league(config, name):
    """Remove a league profile by name. Returns the normalized config."""
    config["leagues"] = [l for l in (config.get("leagues") or []) if l.get("name") != name]
    if config.get("active") == name:
        config["active"] = config["leagues"][0]["name"] if config["leagues"] else None
    for field in PROFILE_FIELDS:
        config.pop(field, None)
    return _normalize(config)


def set_active_league(config, name):
    """Switch the active profile. Returns the normalized config, or None if unknown."""
    if not any(l.get("name") == name for l in (config.get("leagues") or [])):
        return None
    config["active"] = name
    for field in PROFILE_FIELDS:
        config.pop(field, None)
    return _normalize(config)

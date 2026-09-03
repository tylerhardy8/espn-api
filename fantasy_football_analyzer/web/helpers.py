"""Shared helpers for the web routes."""

import os
import time
from flask import flash, redirect, url_for

from ..config import load_config, get_league_config
from ..league_connector import connect_league

# Simple in-memory cache for league objects (avoid reconnecting every request)
_league_cache = {}
_CACHE_TTL = 60  # seconds

# Valued auction pool cache — building it costs a free_agents API call, and
# dollar values don't shift during a draft, so cache longer than the league.
_pool_cache = {}
_POOL_TTL = 600  # seconds

# League history intel cache — building it connects several past seasons, and
# history doesn't change, so cache effectively for the whole session.
_intel_cache = {}
_INTEL_TTL = 12 * 3600  # seconds
INTEL_SEASONS_BACK = 4


def get_ai_key(config=None):
    """The Anthropic API key: per-instance config first, then environment."""
    if config is None:
        config = load_config()
    return (config.get("anthropic_api_key") or "").strip() or os.environ.get("ANTHROPIC_API_KEY")


def ai_available(config=None):
    """Check if an Anthropic API key is configured (config or environment)."""
    return bool(get_ai_key(config))


def get_league_or_redirect(config=None):
    """Load config, connect to league, or redirect to setup on failure.

    Returns (config, league, error_redirect).
    If error_redirect is not None, the caller should return it.
    """
    if config is None:
        config = load_config()

    league_cfg = get_league_config(config)
    if not league_cfg.get("league_id"):
        flash("No league configured. Please set up your league first.", "warning")
        return None, None, redirect(url_for("main.setup"))

    year = config.get("year", 2025)
    cache_key = f"{league_cfg['league_id']}_{year}"

    # Check cache
    if cache_key in _league_cache:
        cached_league, cached_time = _league_cache[cache_key]
        if time.time() - cached_time < _CACHE_TTL:
            return config, cached_league, None

    try:
        league = connect_league(
            league_cfg["league_id"], year,
            league_cfg.get("espn_s2"), league_cfg.get("swid")
        )
        _league_cache[cache_key] = (league, time.time())
        return config, league, None
    except Exception as e:
        flash(f"Could not connect to league: {e}", "danger")
        return None, None, redirect(url_for("main.setup"))


def get_valued_pool(league, config=None):
    """Build (and cache) the valued auction pool for a league.

    Returns (pool, budget, targets, roster_size) from auction.build_valued_pool.
    """
    from ..auction import build_valued_pool

    key = f"{league.league_id}_{getattr(league, 'year', '')}"
    if key in _pool_cache:
        cached, cached_time = _pool_cache[key]
        if time.time() - cached_time < _POOL_TTL:
            return cached

    budget = (config or {}).get("auction_budget")
    result = build_valued_pool(league, budget=budget)
    _pool_cache[key] = (result, time.time())
    return result


def get_league_intel(config):
    """Build (and cache) the league history intelligence profile.

    Connects the previous INTEL_SEASONS_BACK seasons and mines draft, trade,
    and results patterns. Returns the intel dict, or None if no history loads.
    """
    from ..league_connector import connect_multi_year
    from ..league_intel import build_league_intel

    league_cfg = get_league_config(config)
    if not league_cfg.get("league_id"):
        return None

    year = config.get("year", 2025)
    key = f"{league_cfg['league_id']}_{year}"
    if key in _intel_cache:
        cached, cached_time = _intel_cache[key]
        if time.time() - cached_time < _INTEL_TTL:
            return cached

    years = list(range(year - INTEL_SEASONS_BACK, year))
    leagues = connect_multi_year(
        league_cfg["league_id"], years,
        league_cfg.get("espn_s2"), league_cfg.get("swid"),
    )
    intel = build_league_intel(leagues) if leagues else None
    _intel_cache[key] = (intel, time.time())
    return intel


_intel_building = set()


def _intel_key(config):
    league_cfg = get_league_config(config)
    return f"{league_cfg.get('league_id')}_{config.get('year', 2025)}"


def get_league_intel_cached(config):
    """The intel profile if already built (never blocks); else None."""
    cached = _intel_cache.get(_intel_key(config))
    if cached and time.time() - cached[1] < _INTEL_TTL:
        return cached[0]
    return None


def warm_league_intel(config):
    """Build the intel profile in a background thread (idempotent)."""
    import threading

    key = _intel_key(config)
    if get_league_intel_cached(config) is not None or key in _intel_building:
        return
    _intel_building.add(key)

    def run():
        try:
            get_league_intel(config)
        except Exception:
            pass
        finally:
            _intel_building.discard(key)

    threading.Thread(target=run, daemon=True).start()


def clear_league_cache():
    """Clear the cached league connections."""
    _league_cache.clear()
    _pool_cache.clear()
    _intel_cache.clear()


def parse_year_range(year_str):
    """Parse a year range like '2020-2024' into a list of years."""
    if "-" in year_str:
        start, end = year_str.split("-")
        return list(range(int(start), int(end) + 1))
    return [int(y.strip()) for y in year_str.split(",")]

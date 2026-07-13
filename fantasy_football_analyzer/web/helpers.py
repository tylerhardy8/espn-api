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


def ai_available():
    """Check if the Anthropic API key is configured."""
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


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


def clear_league_cache():
    """Clear the cached league connections."""
    _league_cache.clear()
    _pool_cache.clear()


def parse_year_range(year_str):
    """Parse a year range like '2020-2024' into a list of years."""
    if "-" in year_str:
        start, end = year_str.split("-")
        return list(range(int(start), int(end) + 1))
    return [int(y.strip()) for y in year_str.split(",")]

"""Configuration management for the Fantasy Football Analyzer."""

import json
import os

DEFAULT_CONFIG_PATH = os.path.expanduser("~/.fantasy_football_analyzer.json")


def load_config(path=None):
    """Load configuration from a JSON file."""
    path = path or DEFAULT_CONFIG_PATH
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return {}


def save_config(config, path=None):
    """Save configuration to a JSON file."""
    path = path or DEFAULT_CONFIG_PATH
    with open(path, "w") as f:
        json.dump(config, f, indent=2)


def get_league_config(config):
    """Extract league connection parameters from config."""
    return {
        "league_id": config.get("league_id"),
        "espn_s2": config.get("espn_s2"),
        "swid": config.get("swid"),
    }

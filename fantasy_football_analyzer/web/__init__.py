"""Flask web application for the Fantasy Football Analyzer."""

import os
from flask import Flask


def create_app():
    """Create and configure the Flask application."""
    app = Flask(__name__)
    # `or` (not a .get default) so an empty FLASK_SECRET_KEY= from an env file
    # still falls back instead of leaving sessions/flash messages broken.
    app.secret_key = os.environ.get("FLASK_SECRET_KEY") or "fantasy-football-dev-key"

    from .routes import bp
    app.register_blueprint(bp)

    @app.context_processor
    def inject_league_profiles():
        """Expose league profiles to every template for the navbar switcher."""
        from ..config import load_config
        try:
            config = load_config()
        except Exception:
            config = {}
        return {
            "league_profiles": config.get("leagues") or [],
            "active_league": config.get("active"),
        }

    return app

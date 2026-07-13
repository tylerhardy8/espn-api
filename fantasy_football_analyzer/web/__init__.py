"""Flask web application for the Fantasy Football Analyzer."""

import os
from flask import Flask


def create_app():
    """Create and configure the Flask application."""
    app = Flask(__name__)
    app.secret_key = os.environ.get("FLASK_SECRET_KEY", "fantasy-football-dev-key")

    from .routes import bp
    app.register_blueprint(bp)

    return app

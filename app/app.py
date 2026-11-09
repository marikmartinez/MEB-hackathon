"""Crowd-aware conference app (Flask).

Keep listening on $PORT and keep /healthz and /readyz: the manifests in
deploy/ probe them.
"""
import os

from flask import Flask, jsonify, render_template

import occupancy

TEAM = os.environ.get("TEAM_NAME", "your-team")
VERSION = os.environ.get("APP_VERSION", "dev")

SESSIONS = [
    {"id": 1, "time": "10:00", "room": "Main", "title": "Kickoff and sponsor talks"},
    {"id": 2, "time": "11:00", "room": "Everywhere", "title": "Build"},
    {"id": 3, "time": "14:00", "room": "Main", "title": "Panel"},
    {"id": 4, "time": "17:30", "room": "Main", "title": "Submissions close"},
]


def create_app(occupancy_bp=None) -> Flask:
    app = Flask(__name__)
    app.register_blueprint(occupancy_bp or occupancy.blueprint())

    @app.get("/healthz")
    @app.get("/readyz")
    def health():
        return "ok\n", 200, {"Content-Type": "text/plain"}

    @app.get("/api/sessions")
    def sessions():
        return jsonify(version=VERSION, sessions=SESSIONS)

    @app.get("/")
    def index():
        return render_template("index.html", team=TEAM, version=VERSION, sessions=SESSIONS)

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))

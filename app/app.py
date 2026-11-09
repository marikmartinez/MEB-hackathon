"""Crowd-aware conference app.

Keep listening on $PORT and keep /healthz and /readyz, and the manifests in
deploy/ keep working.
"""
import os

from flask import Flask, jsonify, render_template

SESSIONS = [
    {"id": 1, "time": "10:00", "room": "Main", "title": "Kickoff and sponsor talks"},
    {"id": 2, "time": "11:00", "room": "Everywhere", "title": "Build"},
    {"id": 3, "time": "14:00", "room": "Main", "title": "Panel"},
    {"id": 4, "time": "17:30", "room": "Main", "title": "Submissions close"},
]


def create_app():
    app = Flask(__name__)
    team = os.environ.get("TEAM_NAME", "your-team")
    version = os.environ.get("APP_VERSION", "dev")

    @app.get("/healthz")
    def healthz():
        return "ok\n", 200, {"Content-Type": "text/plain"}

    @app.get("/readyz")
    def readyz():
        # Deliberately doesn't check Elasticsearch: if it's down, pods stay up
        # and recommendations fall back.
        return "ok\n", 200, {"Content-Type": "text/plain"}

    @app.get("/api/sessions")
    def api_sessions():
        return jsonify(version=version, sessions=SESSIONS)

    @app.get("/")
    def attendee():
        return render_template(
            "attendee.html", team=team, version=version, sessions=SESSIONS
        )

    @app.get("/organizer")
    def organizer():
        return render_template(
            "organizer.html", team=team, version=version, sessions=SESSIONS
        )

    return app


app = create_app()

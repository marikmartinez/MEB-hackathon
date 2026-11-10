"""Crowd-aware conference app.

Keep listening on $PORT and keep /healthz and /readyz, and the manifests in
deploy/ keep working.
"""
import hmac
import os
from datetime import datetime, timezone

from flask import Flask, jsonify, render_template, request

from search import history
from search.levels import level_for
from search.now import room_states
from search.occupancy import load_rooms, record_occupancy
from search.recommend import TalkNotFound, list_talks, recommend

import occupancy

TEAM = os.environ.get("TEAM_NAME", "your-team")
VERSION = os.environ.get("APP_VERSION", "dev")

SESSIONS = [
    {"id": 1, "time": "10:00", "room": "Main", "title": "Kickoff and sponsor talks"},
    {"id": 2, "time": "11:00", "room": "Everywhere", "title": "Build"},
    {"id": 3, "time": "14:00", "room": "Main", "title": "Panel"},
    {"id": 4, "time": "17:30", "room": "Main", "title": "Submissions close"},
]


def create_app(occupancy_bp=None):
    app = Flask(__name__)
    app.register_blueprint(occupancy_bp or occupancy.blueprint())
    team = os.environ.get("TEAM_NAME", "your-team")
    version = os.environ.get("APP_VERSION", "dev")
    rooms = load_rooms()

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

    @app.get("/api/talks")
    def api_talks():
        return jsonify(list_talks(request.args.get("q", "").strip() or None))

    @app.get("/api/talks/<talk_id>/alternatives")
    def api_alternatives(talk_id):
        try:
            return jsonify(recommend(talk_id))
        except TalkNotFound:
            return jsonify(error="talk not found"), 404

    @app.get("/api/now")
    def api_now():
        return jsonify(room_states(rooms))

    @app.post("/api/occupancy")
    def api_occupancy():
        # This route is on the public URL, so refuse writes without a token.
        token = os.environ.get("OCCUPANCY_TOKEN")
        if not token:
            return jsonify(error="occupancy writes aren't configured"), 503
        supplied = request.headers.get("Authorization", "").encode()
        if not hmac.compare_digest(supplied, f"Bearer {token}".encode()):
            return jsonify(error="unauthorized"), 401

        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify(error="expected a JSON object"), 400
        area = data.get("area")
        if not isinstance(area, str) or area not in rooms:
            return jsonify(error="area must be a room id from venue.json"), 400
        count = data.get("count")
        if type(count) is not int or count < 0:
            return jsonify(error="count must be a non-negative integer"), 400
        capacity = data.get("capacity", rooms[area]["capacity"])
        if type(capacity) is not int or capacity <= 0:
            return jsonify(error="capacity must be a positive integer"), 400

        doc = record_occupancy(area, count, capacity)
        if doc is None:
            return jsonify(error="couldn't store the reading"), 503
        return jsonify(doc)

    @app.get("/")
    def attendee():
        room_names = {room_id: room["name"] for room_id, room in rooms.items()}
        return render_template(
            "attendee.html", team=team, version=version, rooms=room_names
        )

    @app.get("/organizer")
    def organizer():
        return render_template("organizer.html", team=team, version=version)

    @app.get("/api/organizer/history")
    def api_organizer_history():
        range_name = request.args.get("range", history.DEFAULT_RANGE)
        if range_name not in (*history.RANGES, history.DEFAULT_RANGE):
            return jsonify(error="range must be day, 4h or 1h"), 400
        now = datetime.now(timezone.utc)
        start, end = history.window(range_name, list_talks()["talks"], now)
        data = history.room_history(start, end, rooms)
        series = (data or {}).get("rooms", {})
        return jsonify(
            source="elasticsearch" if data is not None else "unavailable",
            range=range_name,
            start=int(start.timestamp() * 1000),
            end=int(end.timestamp() * 1000),
            interval=(data or {}).get("interval"),
            rooms=[
                {
                    "id": room_id,
                    "name": room["name"],
                    "capacity": room["capacity"],
                    "points": series.get(room_id, []),
                }
                for room_id, room in rooms.items()
            ],
        )

    @app.get("/api/organizer/talks")
    def api_organizer_talks():
        now = datetime.now(timezone.utc)
        talks = list_talks()["talks"]
        stats = history.talk_stats(talks, now)
        out = []
        for talk in talks:
            room = rooms.get(talk["room"], {})
            capacity = room.get("capacity")
            s = (stats or {}).get(talk["id"], {})
            peak_ratio = s.get("peak_ratio")
            out.append({
                "id": talk["id"],
                "title": talk["title"],
                "speaker": talk["speaker"],
                "track": talk.get("track"),
                "room": talk["room"],
                "room_name": room.get("name", talk["room"]),
                "capacity": capacity,
                "start": talk["start"],
                "end": talk["end"],
                "status": history.talk_status(talk, now),
                "readings": s.get("readings", 0),
                "avg_ratio": s.get("avg_ratio"),
                "avg_people": s.get("avg_people"),
                "peak_ratio": peak_ratio,
                # From the smoothed peak, so one bad frame can't set it.
                "peak_people": (
                    round(peak_ratio * capacity)
                    if peak_ratio is not None and capacity else None
                ),
                "peak_level": level_for(peak_ratio),
            })
        return jsonify(
            source="elasticsearch" if stats is not None else "unavailable",
            now=int(now.timestamp() * 1000),
            talks=out,
        )

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))

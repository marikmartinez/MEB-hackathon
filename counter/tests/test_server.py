import io
import json
import shutil
import struct
import threading
import zlib
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from PIL import Image

from counter.model import PersonCounter
from counter.replay import Replayer
from counter.server import create_app

SAMPLES = Path(__file__).parent.parent / "samples"


@pytest.fixture
def frames(tmp_path):
    for area, names in {"hall-a": ["bus.jpg", "zidane.jpg"], "corridor-1": ["zidane.jpg"]}.items():
        (tmp_path / area).mkdir()
        for i, name in enumerate(names, 1):
            shutil.copy(SAMPLES / name, tmp_path / area / f"{i:04d}.jpg")
    return tmp_path


@pytest.fixture
def client(frames, monkeypatch):
    monkeypatch.setenv("FRAMES_DIR", str(frames))
    app = create_app(replay=False)
    return app.test_client(), app.extensions["replayer"]


def test_health(client):
    c, _ = client
    assert c.get("/healthz").status_code == 200


def test_count_raw_body_and_multipart(client):
    c, _ = client
    data = (SAMPLES / "bus.jpg").read_bytes()
    assert c.post("/count", data=data).get_json()["count"] == 4
    # curl --data-binary sends this content type by default
    form_typed = c.post("/count", data=data, content_type="application/x-www-form-urlencoded")
    assert form_typed.get_json()["count"] == 4
    resp = c.post("/count", data={"image": (SAMPLES.joinpath("zidane.jpg").open("rb"), "z.jpg")})
    assert resp.get_json()["count"] == 2


def test_count_rejects_bad_input(client):
    c, _ = client
    assert c.post("/count", data=b"").status_code == 400
    assert c.post("/count", data=b"not an image").status_code == 400


def png_claiming(width: int, height: int) -> bytes:
    """A tiny PNG whose header claims width x height (header is all PIL reads on open)."""
    buf = io.BytesIO()
    Image.new("L", (1, 1)).save(buf, format="PNG")
    data = bytearray(buf.getvalue())
    # IHDR chunk: 8-byte signature, 4 length, 4 type, then width/height at 16..24.
    data[16:24] = struct.pack(">II", width, height)
    data[29:33] = struct.pack(">I", zlib.crc32(bytes(data[12:29])))
    return bytes(data)


def test_count_rejects_huge_pixel_count_before_decoding(client):
    c, _ = client
    # ~169M pixels: below PIL's own bomb error, would decode to ~500 MB as RGB
    resp = c.post("/count", data=png_claiming(13000, 13000))
    assert resp.status_code == 413
    assert 'counts_rejected_total{reason="too_large"} 1.0' in c.get("/metrics").get_data(as_text=True)


def test_count_maps_decompression_bomb_to_400(client):
    c, _ = client
    # > 2 x PIL's MAX_IMAGE_PIXELS: Image.open itself raises DecompressionBombError
    assert c.post("/count", data=png_claiming(20000, 20000)).status_code == 400


def test_count_accepts_4k_frame(client):
    c, _ = client
    buf = io.BytesIO()
    Image.new("RGB", (3840, 2160), (40, 40, 40)).save(buf, format="JPEG")
    assert c.post("/count", data=buf.getvalue()).get_json()["count"] == 0


def test_tick_loops_frames_and_serves_last(client):
    c, replayer = client
    first = {s.area: s.count for s in replayer.tick()}
    assert first == {"corridor-1": 2, "hall-a": 4}
    second = {s.area: s.count for s in replayer.tick()}
    assert second == {"corridor-1": 2, "hall-a": 2}  # hall-a moved to its next frame
    third = {s.area: s.count for s in replayer.tick()}
    assert third["hall-a"] == 4  # and looped back

    last = c.get("/last").get_json()
    assert last["tick_seconds"] == 30
    assert last["areas"]["hall-a"]["count"] == 4
    assert [n for _, n in last["areas"]["hall-a"]["history"]] == [4, 2, 4]
    img = c.get("/last/hall-a.jpg")
    assert img.status_code == 200 and img.mimetype == "image/jpeg"
    assert c.get("/last/nowhere.jpg").status_code == 404

    metrics = c.get("/metrics").get_data(as_text=True)
    assert 'people_count{area="hall-a"} 4.0' in metrics
    assert "inference_latency_seconds_bucket" in metrics


def test_tick_posts_occupancy(frames):
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(204)
            self.end_headers()

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/api/occupancy"
        Replayer(PersonCounter(), frames, occupancy_url=url).tick()
    finally:
        server.shutdown()
    assert sorted((r["area"], r["count"]) for r in received) == [("corridor-1", 2), ("hall-a", 4)]


def test_unreachable_app_does_not_break_counting(frames):
    snaps = Replayer(PersonCounter(), frames, occupancy_url="http://127.0.0.1:9/nope", post_timeout=0.5).tick()
    assert len(snaps) == 2

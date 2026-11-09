"""End-to-end through the HTTP API in demo mode."""
import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import settings
from app.demo_data import demo_isbn
from app.main import app


def jpeg(w=1200, h=900):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (120, 90, 60)).save(buf, "JPEG")
    return buf.getvalue()


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def test_status(client):
    s = client.get("/api/status").json()
    assert s["full_demo"] is True and s["password_required"] is False


def test_scan_demo_shelf(client):
    r = client.post("/api/scan", files={"image": ("s.jpg", jpeg(), "image/jpeg")},
                    data={"profile": json.dumps({"cost_per_book": 1, "min_profit": 5})})
    assert r.status_code == 200, r.text
    res = r.json()
    tiers = {b["spine"]["title"]: b["tier"] for b in res["books"]}
    assert tiers["Campbell Biology"] == "pull"
    assert tiers["A Pattern Language"] == "pull"
    assert tiers["The Da Vinci Code"] == "skip"
    assert tiers["Sapiens"] == "skip"
    assert tiers["The Silmarillion"] == "skip"          # book club edition
    assert tiers["The Art of Electronics"] == "check"   # two editions, very different value
    assert tiers["Infinite Jest"] == "check"            # possible collectible
    assert tiers["Moby-Dick"] == "check"                # fine binding, no ISBN
    order = [b["tier"] for b in res["books"]]
    assert order == sorted(order, key=["pull", "check", "skip"].index)
    assert all(b["headline"] for b in res["books"])
    pull = [b for b in res["books"] if b["tier"] == "pull"]
    assert all(b["best"]["max_buy"] >= 1 for b in pull)


def test_higher_cost_turns_pulls_into_skips(client):
    r = client.post("/api/scan", files={"image": ("s.jpg", jpeg(), "image/jpeg")},
                    data={"profile": json.dumps({"cost_per_book": 15, "min_profit": 10})})
    tiers = {b["spine"]["title"]: b["tier"] for b in r.json()["books"]}
    assert tiers["Gödel, Escher, Bach"] != "pull"
    assert tiers["Campbell Biology"] == "pull"


def test_isbn_lookup(client):
    r = client.post("/api/isbn", json={"isbn": demo_isbn(3)})
    assert r.status_code == 200
    b = r.json()["book"]
    assert b["tier"] == "pull" and b["best"]["max_buy"] > 10
    assert client.post("/api/isbn", json={"isbn": "12345"}).status_code == 422


def test_bad_upload(client):
    r = client.post("/api/scan", files={"image": ("x.jpg", b"not an image", "image/jpeg")})
    assert r.status_code == 400


def test_password(client):
    object.__setattr__(settings, "app_password", "hunter2")
    try:
        assert client.post("/api/isbn", json={"isbn": demo_isbn(3)}).status_code == 401
        ok = client.post("/api/isbn", json={"isbn": demo_isbn(3)}, headers={"X-App-Password": "hunter2"})
        assert ok.status_code == 200
    finally:
        object.__setattr__(settings, "app_password", "")

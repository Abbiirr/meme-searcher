from __future__ import annotations

import sys
import types
from contextlib import contextmanager

from fastapi.testclient import TestClient

import vidsearch.api.main as api_main
from vidsearch.storage import minio as minio_store
from vidsearch.storage import pg as pg_store
from vidsearch.storage import qdrant as qdrant_store

app = api_main.app


@contextmanager
def _dummy_cursor():
    yield object()


def _stub_startup_seed(monkeypatch):
    fake_module = types.ModuleType("vidsearch.ingest.fingerprints")
    fake_module.seed_model_versions = lambda *args, **kwargs: {}
    monkeypatch.setitem(sys.modules, "vidsearch.ingest.fingerprints", fake_module)


def test_startup_hook_spawns_background_bookkeeping_once(monkeypatch):
    starts: list[tuple[object, str, bool]] = []

    class DummyThread:
        def __init__(self, *, target, name, daemon):
            self.target = target
            self.name = name
            self.daemon = daemon

        def start(self):
            starts.append((self.target, self.name, self.daemon))

    monkeypatch.setattr(api_main, "_startup_background_started", False)
    monkeypatch.setattr(api_main.threading, "Thread", DummyThread)

    api_main._seed_model_versions_on_startup()
    api_main._seed_model_versions_on_startup()

    assert starts == [
        (api_main._background_startup_bookkeeping, "vidsearch-startup-bookkeeping", True)
    ]


def test_background_startup_bookkeeping_runs_seed_then_prewarm(monkeypatch):
    calls: list[str] = []

    fake_fingerprints = types.ModuleType("vidsearch.ingest.fingerprints")
    fake_fingerprints.seed_model_versions = lambda *args, **kwargs: calls.append("seed") or {}
    monkeypatch.setitem(sys.modules, "vidsearch.ingest.fingerprints", fake_fingerprints)

    fake_retrieve = types.ModuleType("vidsearch.query.retrieve_images")
    fake_retrieve.warm_retrieval_runtime = lambda: calls.append("warm")
    monkeypatch.setitem(sys.modules, "vidsearch.query.retrieve_images", fake_retrieve)

    monkeypatch.setattr(api_main, "PREWARM_RETRIEVAL", True)

    api_main._background_startup_bookkeeping()

    assert calls == ["seed", "warm"]


def test_openapi_published():
    from pytest import MonkeyPatch
    monkeypatch = MonkeyPatch()
    _stub_startup_seed(monkeypatch)
    with TestClient(app) as client:
        resp = client.get("/openapi.json")
    monkeypatch.undo()
    assert resp.status_code == 200
    data = resp.json()
    assert "/search" in data["paths"]
    assert "/thumbnail/{image_id}.webp" in data["paths"]


def test_search_validation_error():
    from pytest import MonkeyPatch
    monkeypatch = MonkeyPatch()
    _stub_startup_seed(monkeypatch)
    with TestClient(app) as client:
        resp = client.post("/search", json={"query": "", "limit": 10})
    monkeypatch.undo()
    assert resp.status_code == 422


def test_search_uses_public_thumbnail_url(monkeypatch):
    _stub_startup_seed(monkeypatch)
    fake_module = types.ModuleType("vidsearch.query.retrieve_images")
    fake_module.retrieve_images = lambda query, limit=10: {
        "query": query,
        "intent": "semantic_description",
        "hits": [
            {
                "rank": 1,
                "image_id": "img_123",
                "source_uri": "data/meme/example.jpg",
                "thumbnail_uri": "minio://thumbnails/img_123.webp",
                "ocr_excerpt": "",
                "retrieval_score": 0.7,
                "rerank_score": 0.9,
            }
        ],
    }
    monkeypatch.setitem(sys.modules, "vidsearch.query.retrieve_images", fake_module)
    monkeypatch.setattr(
        api_main.feedback_service,
        "log_search_impressions",
        lambda **kwargs: {
            "search_id": "11111111-1111-1111-1111-111111111111",
            "ranker_version_id": "baseline",
            "none_correct_url": "http://127.0.0.1:8000/feedback/confirm/none",
            "impressions": [
                {
                    "image_id": "img_123",
                    "impression_id": "22222222-2222-2222-2222-222222222222",
                    "select_url": "http://127.0.0.1:8000/feedback/confirm/select",
                    "reject_url": "http://127.0.0.1:8000/feedback/confirm/reject",
                    "undo_url": "http://127.0.0.1:8000/feedback/confirm/undo",
                }
            ],
        },
    )

    with TestClient(app) as client:
        resp = client.post("/search", json={"query": "example meme", "limit": 5})

    assert resp.status_code == 200
    body = resp.json()
    assert body["hits"][0]["thumbnail_uri"].endswith("/thumbnail/img_123.webp")
    assert body["search_id"] == "11111111-1111-1111-1111-111111111111"
    assert body["feedback_enabled"] is True
    assert body["hits"][0]["impression_id"] == "22222222-2222-2222-2222-222222222222"
    assert body["hits"][0]["feedback_select_url"].endswith("/feedback/confirm/select")


def test_thumbnail_endpoint_downloads_bytes(monkeypatch):
    _stub_startup_seed(monkeypatch)
    monkeypatch.setattr(pg_store, "get_cursor", _dummy_cursor)
    monkeypatch.setattr(
        pg_store,
        "get_image_by_id",
        lambda cur, image_id: {"image_id": image_id, "thumbnail_uri": "minio://thumbs/abcd/img_1.webp"},
    )
    monkeypatch.setattr(minio_store, "download_thumbnail", lambda uri: b"webp-bytes")

    with TestClient(app) as client:
        resp = client.get("/thumbnail/img_1.webp")

    assert resp.status_code == 200
    assert resp.content == b"webp-bytes"
    assert resp.headers["content-type"].startswith("image/webp")


def test_thumbnail_endpoint_404_without_thumbnail(monkeypatch):
    _stub_startup_seed(monkeypatch)
    monkeypatch.setattr(pg_store, "get_cursor", _dummy_cursor)
    monkeypatch.setattr(pg_store, "get_image_by_id", lambda cur, image_id: {"image_id": image_id, "thumbnail_uri": None})

    with TestClient(app) as client:
        resp = client.get("/thumbnail/img_1.webp")

    assert resp.status_code == 404


def test_delete_image_removes_thumbnail_qdrant_and_pg(monkeypatch):
    _stub_startup_seed(monkeypatch)
    monkeypatch.setattr(pg_store, "get_cursor", _dummy_cursor)
    monkeypatch.setattr(
        pg_store,
        "get_image_by_id",
        lambda cur, image_id: {"image_id": image_id, "thumbnail_uri": "minio://thumbs/img_1.webp"},
    )

    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(minio_store, "delete_object", lambda uri: calls.append(("minio", uri)))
    monkeypatch.setattr(qdrant_store, "delete_point", lambda image_id: calls.append(("qdrant", image_id)))
    monkeypatch.setattr(pg_store, "delete_image", lambda cur, image_id: calls.append(("pg", image_id)) or True)

    with TestClient(app) as client:
        resp = client.delete("/image/img_1")

    assert resp.status_code == 200
    assert resp.json() == {"image_id": "img_1", "deleted": True, "message": ""}
    assert calls == [
        ("minio", "minio://thumbs/img_1.webp"),
        ("qdrant", "img_1"),
        ("pg", "img_1"),
    ]


def test_feedback_confirm_get_does_not_write_and_sets_csrf(monkeypatch):
    _stub_startup_seed(monkeypatch)
    calls: list[str] = []

    monkeypatch.setattr(
        "vidsearch.feedback.tokens.verify_feedback_token",
        lambda token: {"action": "select", "search_id": "search"},
    )
    monkeypatch.setattr(api_main.feedback_service, "record_judgment_from_token", lambda token: calls.append(token))

    with TestClient(app) as client:
        resp = client.get("/feedback/confirm/test-token")

    assert resp.status_code == 200
    assert "vidsearch_feedback_csrf" in resp.cookies
    assert "method=\"post\" action=\"/feedback/judgment\"" in resp.text
    assert calls == []


def test_feedback_judgment_post_requires_csrf(monkeypatch):
    _stub_startup_seed(monkeypatch)
    monkeypatch.setattr(api_main.feedback_service, "record_judgment_from_token", lambda token: {"status": "recorded"})

    with TestClient(app) as client:
        resp = client.post("/feedback/judgment", json={"token": "test-token", "csrf_token": "missing-cookie"})

    assert resp.status_code == 403


def test_feedback_judgment_post_records_once_with_csrf(monkeypatch):
    _stub_startup_seed(monkeypatch)
    calls: list[str] = []

    def fake_record(token):
        calls.append(token)
        return {
            "status": "recorded",
            "judgment_id": "33333333-3333-3333-3333-333333333333",
            "search_id": "11111111-1111-1111-1111-111111111111",
            "impression_id": "22222222-2222-2222-2222-222222222222",
            "pairs_created": 4,
        }

    monkeypatch.setattr(api_main.feedback_service, "record_judgment_from_token", fake_record)

    with TestClient(app) as client:
        client.cookies.set("vidsearch_feedback_csrf", "csrf")
        resp = client.post(
            "/feedback/judgment",
            json={"token": "test-token", "csrf_token": "csrf"},
        )

    assert resp.status_code == 200
    assert resp.json()["status"] == "recorded"
    assert resp.json()["pairs_created"] == 4
    assert calls == ["test-token"]

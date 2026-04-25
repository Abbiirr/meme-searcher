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

    with TestClient(app) as client:
        resp = client.post("/search", json={"query": "example meme", "limit": 5})

    assert resp.status_code == 200
    body = resp.json()
    assert body["hits"][0]["thumbnail_uri"].endswith("/thumbnail/img_123.webp")


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

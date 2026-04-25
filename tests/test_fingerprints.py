"""Pure-function tests for vidsearch.ingest.fingerprints.

No network, no Postgres — only filesystem + hashlib. The DB seed path
is exercised indirectly by the integration tests during the small-ingest
cycle.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from vidsearch.ingest import fingerprints as fp


# ---------------------------------------------------------------------------
# compute_local_fingerprint
# ---------------------------------------------------------------------------


def _mk_model_dir(root: Path, files: dict[str, bytes]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    d = root / "model-a"
    d.mkdir()
    for name, data in files.items():
        (d / name).write_bytes(data)
    return d


def test_local_fingerprint_stable_across_calls(tmp_path):
    d = _mk_model_dir(
        tmp_path,
        {
            "config.json": b'{"model_type":"siglip"}',
            "model.safetensors": b"W" * 1024,
            "preprocessor_config.json": b'{"size":384}',
        },
    )
    a = fp.compute_local_fingerprint(d)
    b = fp.compute_local_fingerprint(d)
    assert a is not None
    assert a == b
    assert len(a) == 16


def test_local_fingerprint_changes_when_weights_change(tmp_path):
    d1 = _mk_model_dir(
        tmp_path / "r1",
        {"config.json": b"{}", "model.safetensors": b"A" * 256, "preprocessor_config.json": b"{}"},
    )
    d2 = _mk_model_dir(
        tmp_path / "r2",
        {"config.json": b"{}", "model.safetensors": b"B" * 256, "preprocessor_config.json": b"{}"},
    )
    assert fp.compute_local_fingerprint(d1) != fp.compute_local_fingerprint(d2)


def test_local_fingerprint_without_preprocessor_still_valid(tmp_path):
    """Not every model ships a preprocessor_config.json (e.g. rerankers)."""
    d = _mk_model_dir(
        tmp_path,
        {"config.json": b"{}", "model.safetensors": b"X" * 128},
    )
    result = fp.compute_local_fingerprint(d)
    assert result is not None
    assert len(result) == 16


def test_local_fingerprint_requires_config_json(tmp_path):
    d = tmp_path / "no-config"
    d.mkdir()
    (d / "model.safetensors").write_bytes(b"Y" * 128)
    # Missing config.json → returns None (not a valid model dir)
    assert fp.compute_local_fingerprint(d) is None


def test_local_fingerprint_missing_dir(tmp_path):
    assert fp.compute_local_fingerprint(tmp_path / "does-not-exist") is None


def test_local_fingerprint_differs_from_config_only_delta(tmp_path):
    """Changing config.json alone should flip the fingerprint — we hash all
    three files, not just the weights."""
    d1 = _mk_model_dir(
        tmp_path / "r1",
        {"config.json": b'{"v":1}', "model.safetensors": b"W" * 64, "preprocessor_config.json": b"{}"},
    )
    d2 = _mk_model_dir(
        tmp_path / "r2",
        {"config.json": b'{"v":2}', "model.safetensors": b"W" * 64, "preprocessor_config.json": b"{}"},
    )
    assert fp.compute_local_fingerprint(d1) != fp.compute_local_fingerprint(d2)


# ---------------------------------------------------------------------------
# compute_gateway_fingerprint / seed_model_versions
# ---------------------------------------------------------------------------


def test_gateway_fingerprint_without_key(monkeypatch):
    monkeypatch.delenv("LITELLM_MASTER_KEY", raising=False)
    assert fp.fetch_gateway_state() is None


def test_gateway_fingerprint_network_error(monkeypatch):
    monkeypatch.setenv("LITELLM_MASTER_KEY", "sk-test")
    monkeypatch.setenv("LITELLM_URL", "http://127.0.0.1:1")  # closed port
    # Should swallow the connection error and return None, not raise.
    assert fp.fetch_gateway_state() is None


class _FakeResponse:
    def __init__(self, payload: object, *, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self.content = json.dumps(payload, sort_keys=True).encode("utf-8")

    def json(self):
        return self._payload


def test_gateway_fingerprint_success_uses_alias_upstream_and_build(monkeypatch):
    monkeypatch.setenv("LITELLM_MASTER_KEY", "sk-test")
    monkeypatch.setenv("LITELLM_URL", "http://127.0.0.1:4000")

    models_payload = {
        "data": [
            {"id": "fast", "object": "model", "owned_by": "openai"},
            {"id": "paddle-ocr", "object": "model", "owned_by": "openai"},
        ]
    }
    readiness_payload = {"status": "healthy", "litellm_version": "1.82.2"}

    def fake_get(url, headers=None, timeout=5):
        if url.endswith("/v1/models"):
            return _FakeResponse(models_payload)
        if url.endswith("/health/readiness"):
            return _FakeResponse(readiness_payload)
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(fp.requests, "get", fake_get)

    state = fp.fetch_gateway_state()
    assert state is not None
    assert state.build_revision == "1.82.2"

    a = fp.compute_gateway_fingerprint("meme_ocr", "paddle-ocr", state)
    b = fp.compute_gateway_fingerprint("meme_synthesis", "fast", state)
    assert a is not None
    assert b is not None
    assert len(a) == 16
    assert len(b) == 16
    assert a != b


def test_gateway_fingerprint_falls_back_to_models_body_hash(monkeypatch):
    monkeypatch.setenv("LITELLM_MASTER_KEY", "sk-test")
    monkeypatch.setenv("LITELLM_URL", "http://127.0.0.1:4000")

    models_payload = {"data": [{"id": "paddle-ocr", "object": "model"}]}

    def fake_get(url, headers=None, timeout=5):
        if url.endswith("/v1/models"):
            return _FakeResponse(models_payload)
        if url.endswith("/health/readiness"):
            raise fp.requests.RequestException("boom")
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(fp.requests, "get", fake_get)

    state = fp.fetch_gateway_state()
    assert state is not None
    assert state.build_revision == fp.hashlib.sha256(
        json.dumps(models_payload, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    assert fp.compute_gateway_fingerprint("meme_ocr", "paddle-ocr", state) is not None


def test_gateway_fingerprint_returns_none_for_missing_upstream(monkeypatch):
    state = fp.GatewayState(
        api_base="http://127.0.0.1:4000",
        build_revision="1.82.2",
        model_records={"fast": {"id": "fast"}},
    )
    assert fp.compute_gateway_fingerprint("meme_ocr", "paddle-ocr", state) is None


def test_seed_model_versions_assigns_distinct_gateway_revisions(monkeypatch, tmp_path):
    monkeypatch.setenv("VIDSEARCH_CAPTION_MODEL", "vision")
    monkeypatch.setenv("VIDSEARCH_OCR_MODEL", "glm-ocr-wrapper")
    root = tmp_path / "models"
    bge = root / "embeddings" / "bge-m3"
    siglip = root / "embeddings" / "siglip2-so400m-patch16-384"
    reranker = root / "embeddings" / "jina-reranker-v2-base-multilingual"
    for directory in (bge, siglip, reranker):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "config.json").write_text("{}")
        (directory / "model.safetensors").write_bytes(b"weights")
    (siglip / "preprocessor_config.json").write_text("{}")

    state = fp.GatewayState(
        api_base="http://127.0.0.1:4000",
        build_revision="1.82.2",
        model_records={
            "vision": {"id": "vision", "object": "model"},
            "glm-ocr-wrapper": {"id": "glm-ocr-wrapper", "object": "model"},
            "fast": {"id": "fast", "object": "model"},
            "thinking": {"id": "thinking", "object": "model"},
        },
    )
    monkeypatch.setattr(fp, "fetch_gateway_state", lambda: state)

    rows = []

    class _DummyCursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(
        fp,
        "pg_store",
        SimpleNamespace(
            get_cursor=lambda: _DummyCursor(),
            upsert_model_version=lambda cur, key, family, version, revision, config: rows.append(
                {
                    "key": key,
                    "family": family,
                    "version": version,
                    "revision": revision,
                    "config": config,
                }
            ),
        ),
    )

    results = fp.seed_model_versions(root)

    gateway_rows = {row["key"]: row for row in rows if row["family"] == "litellm-gateway"}
    assert gateway_rows["meme_vlm_captioner"]["revision"] != gateway_rows["meme_ocr"]["revision"]
    assert gateway_rows["meme_synthesis"]["revision"] != gateway_rows["meme_controller"]["revision"]
    assert all(row["revision"] for row in gateway_rows.values())
    assert results["meme_ocr"] == gateway_rows["meme_ocr"]["revision"]


def test_seed_model_versions_reuses_process_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("VIDSEARCH_CAPTION_MODEL", "vision")
    monkeypatch.setenv("VIDSEARCH_OCR_MODEL", "glm-ocr-wrapper")
    monkeypatch.setenv("LITELLM_URL", "http://127.0.0.1:4000")
    monkeypatch.setattr(fp, "_seed_cache_signature", None)
    monkeypatch.setattr(fp, "_seed_cache_results", None)

    root = tmp_path / "models"
    bge = root / "embeddings" / "bge-m3"
    siglip = root / "embeddings" / "siglip2-so400m-patch16-384"
    reranker = root / "embeddings" / "jina-reranker-v2-base-multilingual"
    for directory in (bge, siglip, reranker):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "config.json").write_text("{}")
        (directory / "model.safetensors").write_bytes(b"weights")
    (siglip / "preprocessor_config.json").write_text("{}")

    fetch_calls = {"count": 0}

    def fake_fetch_gateway_state():
        fetch_calls["count"] += 1
        return fp.GatewayState(
            api_base="http://127.0.0.1:4000",
            build_revision="1.82.2",
            model_records={
                "vision": {"id": "vision", "object": "model"},
                "glm-ocr-wrapper": {"id": "glm-ocr-wrapper", "object": "model"},
                "fast": {"id": "fast", "object": "model"},
                "thinking": {"id": "thinking", "object": "model"},
            },
        )

    upserts = []

    class _DummyCursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(fp, "fetch_gateway_state", fake_fetch_gateway_state)
    monkeypatch.setattr(
        fp,
        "pg_store",
        SimpleNamespace(
            get_cursor=lambda: _DummyCursor(),
            upsert_model_version=lambda cur, key, family, version, revision, config: upserts.append(key),
        ),
    )

    first = fp.seed_model_versions(root)
    second = fp.seed_model_versions(root)

    assert first == second
    assert fetch_calls["count"] == 1
    assert len(upserts) == len(fp._model_rows())


def test_build_point_model_versions_only_includes_active_revisioned_features():
    revisions = {
        "text_dense": "dense-rev",
        "text_sparse": "sparse-rev",
        "visual": "visual-rev",
        "meme_vlm_captioner": "caption-rev",
        "meme_ocr": None,
    }
    out = fp.build_point_model_versions(
        revisions,
        has_text_dense=True,
        has_text_sparse=True,
        has_visual=False,
        has_caption=True,
        has_ocr=True,
    )
    assert out == {
        "text_dense": "dense-rev",
        "text_sparse": "sparse-rev",
        "meme_vlm_captioner": "caption-rev",
    }

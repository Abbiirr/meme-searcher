from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from qdrant_client import QdrantClient

import infra.qdrant.bootstrap as qdrant_bootstrap
import vidsearch.api.main as api_main
import vidsearch.ingest.images as images_mod
import vidsearch.query.encoders as encoders_mod
import vidsearch.query.retrieve_images as retrieve_mod
import vidsearch.storage.minio as minio_store
import vidsearch.storage.pg as pg_store
import vidsearch.storage.qdrant as qdrant_store
from vidsearch.ingest.caption import Captions


pytestmark = pytest.mark.skipif(
    os.environ.get("VIDSEARCH_RUN_LIVE_INTEGRATION") != "1",
    reason="set VIDSEARCH_RUN_LIVE_INTEGRATION=1 to run live Phase 0 integration coverage",
)


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_SQL = (ROOT / "infra" / "postgres" / "001_schema.sql").read_text(encoding="utf-8")
CAPTIONS_SQL = (ROOT / "infra" / "postgres" / "002_captions.sql").read_text(encoding="utf-8")
FIXTURE_IMAGE = ROOT / "data" / "meme" / "Old Memes" / "AhgHtnP.jpg"
POSTGRES_ADMIN_URL = "postgresql://vidsearch:vidsearch@localhost:5432/postgres"


def _dense_vector(seed: int, size: int) -> list[float]:
    values = [0.0] * size
    values[seed % size] = 1.0
    values[(seed * 7 + 3) % size] = 0.5
    return values


def _sparse_vector(seed: int) -> dict[int, float]:
    return {
        seed % 2048: 1.0,
        (seed * 13 + 11) % 2048: 0.5,
    }


def _seed_from_text(text: str) -> int:
    return sum(ord(ch) for ch in text) % 100000


def _apply_schema(db_url: str) -> None:
    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
            cur.execute(CAPTIONS_SQL)
        conn.commit()


@contextmanager
def _scratch_db():
    db_name = f"vidsearch_it_{uuid.uuid4().hex[:8]}"
    admin = psycopg.connect(POSTGRES_ADMIN_URL)
    admin.autocommit = True
    try:
        with admin.cursor() as cur:
            cur.execute(f'CREATE DATABASE "{db_name}"')
        db_url = f"postgresql://vidsearch:vidsearch@localhost:5432/{db_name}"
        _apply_schema(db_url)
        yield db_url
    finally:
        with admin.cursor() as cur:
            cur.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s",
                (db_name,),
            )
            cur.execute(f'DROP DATABASE IF EXISTS "{db_name}"')
        admin.close()


@contextmanager
def _scratch_qdrant():
    suffix = uuid.uuid4().hex[:8]
    alias = f"memes_it_{suffix}"
    collection = f"memes_v1_it_{suffix}"

    old_alias = qdrant_store.MEME_COLLECTION
    old_boot_alias = qdrant_bootstrap.MEME_COLLECTION
    old_boot_collection = qdrant_bootstrap.MEME_COLLECTION_V1
    old_ready = qdrant_store._collections_ready
    old_client = qdrant_store._client

    qdrant_store.MEME_COLLECTION = alias
    qdrant_bootstrap.MEME_COLLECTION = alias
    qdrant_bootstrap.MEME_COLLECTION_V1 = collection
    qdrant_store._collections_ready = False
    qdrant_store._client = None

    client = QdrantClient(url=qdrant_store.QDRANT_URL)
    try:
        qdrant_store.ensure_collections()
        yield alias, collection
    finally:
        try:
            client.delete_alias(alias_name=alias)
        except Exception:
            pass
        try:
            client.delete_collection(collection_name=collection)
        except Exception:
            pass
        qdrant_store.MEME_COLLECTION = old_alias
        qdrant_bootstrap.MEME_COLLECTION = old_boot_alias
        qdrant_bootstrap.MEME_COLLECTION_V1 = old_boot_collection
        qdrant_store._collections_ready = old_ready
        qdrant_store._client = old_client


@contextmanager
def _scratch_minio():
    bucket = f"thumbs-it-{uuid.uuid4().hex[:8]}"
    old_bucket = minio_store.MINIO_BUCKET_THUMBNAILS
    old_client = minio_store._client
    minio_store.MINIO_BUCKET_THUMBNAILS = bucket
    minio_store._client = None
    client = minio_store.get_client()
    try:
        minio_store.ensure_bucket(bucket)
        yield bucket
    finally:
        try:
            for obj in client.list_objects(bucket, recursive=True):
                client.remove_object(bucket, obj.object_name)
            client.remove_bucket(bucket)
        except Exception:
            pass
        minio_store.MINIO_BUCKET_THUMBNAILS = old_bucket
        minio_store._client = old_client


def test_live_ingest_search_delete_roundtrip(monkeypatch):
    assert FIXTURE_IMAGE.exists(), f"fixture image missing: {FIXTURE_IMAGE}"

    with _scratch_db() as db_url, _scratch_qdrant(), _scratch_minio():
        monkeypatch.setattr(pg_store, "DATABASE_URL", db_url, raising=False)
        monkeypatch.setattr(api_main.pg_store, "DATABASE_URL", db_url, raising=False)

        monkeypatch.setattr(images_mod, "seed_model_versions", lambda: None)
        monkeypatch.setattr(
            images_mod,
            "run_ocr",
            lambda _path: [{"text": "The secret ingredient is crime.", "conf": 0.99}],
        )
        monkeypatch.setattr(
            images_mod,
            "caption_image",
            lambda _path: Captions(
                literal="A man speaks on a city street.",
                figurative="A darkly humorous public confession.",
                template="unknown",
                tags=["man", "street", "crime"],
            ),
        )

        monkeypatch.setattr(
            encoders_mod,
            "encode_text",
            lambda text: (
                _dense_vector(_seed_from_text(text), 1024),
                _sparse_vector(_seed_from_text(text)),
            ),
        )
        monkeypatch.setattr(
            encoders_mod,
            "encode_visual",
            lambda _image: _dense_vector(17, 1152),
        )
        monkeypatch.setattr(retrieve_mod, "encode_text", encoders_mod.encode_text)
        monkeypatch.setattr(retrieve_mod, "encode_text_visual", lambda _text: [])
        monkeypatch.setattr(
            retrieve_mod,
            "rerank",
            lambda query, documents, top_k=10: [(index, float(len(documents) - index)) for index in range(min(top_k, len(documents)))],
        )

        result = images_mod.ingest_image(FIXTURE_IMAGE, force=False)
        assert result["status"] == "ingested"
        image_id = result["image_id"]

        with TestClient(api_main.app) as client:
            search_resp = client.post("/search", json={"query": "\"the secret ingredient is crime\"", "limit": 5})
            assert search_resp.status_code == 200
            hits = search_resp.json()["hits"]
            assert hits[0]["image_id"] == image_id

            thumb_resp = client.get(f"/thumbnail/{image_id}.webp")
            assert thumb_resp.status_code == 200
            assert thumb_resp.content

            delete_resp = client.delete(f"/image/{image_id}")
            assert delete_resp.status_code == 200
            assert delete_resp.json()["deleted"] is True

            missing_thumb = client.get(f"/thumbnail/{image_id}.webp")
            assert missing_thumb.status_code == 404

        with pg_store.get_cursor(db_url) as cur:
            assert pg_store.get_image_by_id(cur, image_id) is None
            cur.execute("SELECT COUNT(*) FROM ops.ingest_steps WHERE image_id = %s", (image_id,))
            assert cur.fetchone()[0] == 0

        assert qdrant_store.get_point(image_id) is None

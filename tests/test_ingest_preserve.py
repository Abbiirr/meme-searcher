from __future__ import annotations

import sys
import types
from contextlib import contextmanager
from types import SimpleNamespace

from vidsearch.ingest import images


@contextmanager
def _dummy_cursor():
    yield object()


def test_force_reingest_preserves_existing_search_state(monkeypatch, tmp_path):
    path = tmp_path / "meme.png"
    path.write_bytes(b"fake-image-bytes")

    existing_row = {
        "thumbnail_uri": "minio://thumbs/img_existing.webp",
        "ocr_text": "nobody:",
        "ocr_full_text": "nobody:",
        "has_ocr": True,
        "has_caption": True,
        "caption_literal": "person staring at screen",
        "caption_figurative": "silent judgment",
        "template_name": "side eyeing chloe",
        "tags": ["reaction", "judgment"],
        "retrieval_text": (
            "literal: person staring at screen\n"
            "figurative: silent judgment\n"
            "template: side eyeing chloe\n"
            "tags: reaction, judgment\n"
            "text: nobody:"
        ),
    }
    existing_point = SimpleNamespace(
        payload={
            "image_id": "img_existing",
            "model_version": {
                "text_dense": "td-old",
                "text_sparse": "ts-old",
                "visual": "vis-old",
                "meme_vlm_captioner": "cap-old",
                "meme_ocr": "ocr-old",
            },
        },
        vector={
            "text-dense": [0.11, 0.22],
            "text-sparse": SimpleNamespace(indices=[1, 9], values=[0.5, 0.8]),
            "visual": [0.33, 0.44],
        },
    )

    steps: list[tuple[str, str, dict | None]] = []
    captured_item: dict = {}
    captured_point: dict = {}

    def record_step(cur, image_id, step, state, meta=None):
        steps.append((step, state, meta))

    def record_item(
        cur,
        image_id,
        thumbnail_uri,
        ocr_text,
        ocr_full_text,
        ocr_boxes,
        has_ocr,
        **kwargs,
    ):
        captured_item.update(
            {
                "image_id": image_id,
                "thumbnail_uri": thumbnail_uri,
                "ocr_text": ocr_text,
                "ocr_full_text": ocr_full_text,
                "ocr_boxes": ocr_boxes,
                "has_ocr": has_ocr,
                **kwargs,
            }
        )

    monkeypatch.setattr(images, "seed_model_versions", lambda: None)
    monkeypatch.setattr(images, "ENABLE_CAPTIONS", True)
    monkeypatch.setattr(images, "decode_image", lambda in_path: (object(), 640, 480, "png"))

    def fail_thumbnail(_img):
        raise RuntimeError("thumbnail service unavailable")

    monkeypatch.setattr(images, "generate_thumbnail", fail_thumbnail)

    def fail_ocr(_path):
        raise RuntimeError("gateway offline")

    monkeypatch.setattr(images, "run_ocr", fail_ocr)

    def fail_caption(_path):
        raise RuntimeError("caption gateway offline")

    monkeypatch.setattr(images, "caption_image", fail_caption)

    fake_encoders = types.ModuleType("vidsearch.query.encoders")

    def fail_text(_text):
        raise RuntimeError("text encoder unavailable")

    fake_encoders.encode_text = fail_text
    fake_encoders.encode_visual = lambda _img: [0.9, 0.1]
    monkeypatch.setitem(sys.modules, "vidsearch.query.encoders", fake_encoders)

    monkeypatch.setattr(images.pg_store, "get_cursor", _dummy_cursor)
    monkeypatch.setattr(images.pg_store, "get_image_by_id", lambda cur, image_id: existing_row)
    monkeypatch.setattr(images.pg_store, "upsert_ingest_step", record_step)
    monkeypatch.setattr(images.pg_store, "upsert_image", lambda *args, **kwargs: None)
    monkeypatch.setattr(images.pg_store, "upsert_image_item", record_item)
    monkeypatch.setattr(
        images.pg_store,
        "get_model_revisions",
        lambda cur, keys: {
            "text_dense": "td-new",
            "text_sparse": "ts-new",
            "visual": "vis-new",
            "meme_vlm_captioner": "cap-new",
            "meme_ocr": "ocr-new",
        },
    )
    monkeypatch.setattr(images.qdrant_store, "get_point", lambda image_id, with_vectors=False: existing_point)
    monkeypatch.setattr(images.qdrant_store, "upsert_point", lambda **kwargs: captured_point.update(kwargs))
    monkeypatch.setattr(images, "log_event", lambda *args, **kwargs: None)

    result = images.ingest_image(path, force=True)

    assert result["status"] == "ingested"
    assert captured_item["thumbnail_uri"] == existing_row["thumbnail_uri"]
    assert captured_item["ocr_text"] == existing_row["ocr_text"]
    assert captured_item["has_ocr"] is True
    assert captured_item["caption_literal"] == existing_row["caption_literal"]
    assert captured_item["caption_figurative"] == existing_row["caption_figurative"]
    assert captured_item["template_name"] == existing_row["template_name"]
    assert captured_item["tags"] == existing_row["tags"]
    assert "person staring at screen" in captured_item["retrieval_text"]
    assert "silent judgment" in captured_item["retrieval_text"]
    assert "side eyeing chloe" in captured_item["retrieval_text"]
    assert "nobody:" in captured_item["retrieval_text"]

    assert captured_point["thumbnail_uri"] == existing_row["thumbnail_uri"]
    assert captured_point["has_ocr"] is True
    assert captured_point["has_caption"] is True
    assert captured_point["text_dense"] == existing_point.vector["text-dense"]
    assert captured_point["text_sparse"] == {1: 0.5, 9: 0.8}
    assert captured_point["visual"] == [0.9, 0.1]
    assert captured_point["model_version"] == {
        "text_dense": "td-old",
        "text_sparse": "ts-old",
        "visual": "vis-new",
        "meme_vlm_captioner": "cap-old",
        "meme_ocr": "ocr-old",
    }

    step_map = {step: (state, meta or {}) for step, state, meta in steps}
    assert step_map["thumbnail"] == ("done", {"preserved": True, "error": "thumbnail service unavailable"})
    assert step_map["ocr"] == ("done", {"preserved": True, "error": "gateway offline"})
    assert step_map["caption"] == ("done", {"preserved": True, "error": "caption gateway offline"})
    assert step_map["embed_text"] == ("done", {"preserved": True, "error": "text encoder unavailable"})
    assert step_map["embed_visual"] == ("done", {})
    assert step_map["upsert_qdrant"] == ("done", {"preserved": True})


def test_force_reingest_does_not_preserve_placeholder_ocr(monkeypatch, tmp_path):
    path = tmp_path / "meme.png"
    path.write_bytes(b"placeholder-ocr-image")

    existing_row = {
        "thumbnail_uri": "minio://thumbs/img_existing.webp",
        "ocr_text": "there is no text in the image.",
        "ocr_full_text": "There is no text in the image.",
        "has_ocr": True,
        "has_caption": False,
        "caption_literal": None,
        "caption_figurative": None,
        "template_name": None,
        "tags": None,
        "retrieval_text": "",
    }

    captured_item: dict = {}
    captured_point: dict = {}

    def record_item(
        cur,
        image_id,
        thumbnail_uri,
        ocr_text,
        ocr_full_text,
        ocr_boxes,
        has_ocr,
        **kwargs,
    ):
        captured_item.update(
            {
                "thumbnail_uri": thumbnail_uri,
                "ocr_text": ocr_text,
                "ocr_full_text": ocr_full_text,
                "has_ocr": has_ocr,
                **kwargs,
            }
        )

    monkeypatch.setattr(images, "seed_model_versions", lambda: None)
    monkeypatch.setattr(images, "ENABLE_CAPTIONS", False)
    monkeypatch.setattr(images, "decode_image", lambda in_path: (object(), 640, 480, "png"))
    monkeypatch.setattr(images, "generate_thumbnail", lambda _img: b"thumb")
    monkeypatch.setattr(images.minio_store, "upload_thumbnail", lambda image_id, thumb: f"minio://thumbnails/{image_id}.webp")
    monkeypatch.setattr(
        images,
        "run_ocr",
        lambda _path: [{"text": "There is no text in the image.", "conf": 1.0, "bbox": [0, 0, 0, 0]}],
    )

    fake_encoders = types.ModuleType("vidsearch.query.encoders")
    fake_encoders.encode_text = lambda _text: ([], {})
    fake_encoders.encode_visual = lambda _img: [0.25, 0.75]
    monkeypatch.setitem(sys.modules, "vidsearch.query.encoders", fake_encoders)

    monkeypatch.setattr(images.pg_store, "get_cursor", _dummy_cursor)
    monkeypatch.setattr(images.pg_store, "get_image_by_id", lambda cur, image_id: existing_row)
    monkeypatch.setattr(images.pg_store, "upsert_ingest_step", lambda *args, **kwargs: None)
    monkeypatch.setattr(images.pg_store, "upsert_image", lambda *args, **kwargs: None)
    monkeypatch.setattr(images.pg_store, "upsert_image_item", record_item)
    monkeypatch.setattr(images.pg_store, "get_model_revisions", lambda cur, keys: {})
    monkeypatch.setattr(images.qdrant_store, "get_point", lambda image_id, with_vectors=False: None)
    monkeypatch.setattr(images.qdrant_store, "upsert_point", lambda **kwargs: captured_point.update(kwargs))
    monkeypatch.setattr(images, "log_event", lambda *args, **kwargs: None)

    result = images.ingest_image(path, force=True)

    assert result["status"] == "ingested"
    assert captured_item["ocr_text"] is None
    assert captured_item["ocr_full_text"] is None
    assert captured_item["has_ocr"] is False
    assert captured_point["has_ocr"] is False

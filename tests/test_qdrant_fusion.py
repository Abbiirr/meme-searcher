from __future__ import annotations

from types import SimpleNamespace

from vidsearch.storage import qdrant as qdrant_store


def _pt(image_id: str):
    return SimpleNamespace(id=image_id, payload={"image_id": image_id}, score=1.0)


def test_get_intent_weights_matches_retrieval_plan_table():
    assert qdrant_store.get_intent_weights("exact_text") == {
        "text-dense": 1.0,
        "text-sparse": 1.5,
        "visual": 0.3,
    }
    assert qdrant_store.get_intent_weights("semantic_description") == {
        "text-dense": 1.3,
        "text-sparse": 0.7,
        "visual": 0.8,
    }


def test_weighted_rrf_prefers_sparse_for_exact_text():
    rankings = {
        "text-dense": [_pt("img_dense"), _pt("img_sparse")],
        "text-sparse": [_pt("img_sparse"), _pt("img_dense")],
        "visual": [],
    }
    fused = qdrant_store.weighted_rrf_fuse(
        rankings,
        weights=qdrant_store.get_intent_weights("exact_text"),
        limit=2,
    )
    assert [point.id for point in fused] == ["img_sparse", "img_dense"]


def test_weighted_rrf_prefers_dense_for_semantic_queries():
    rankings = {
        "text-dense": [_pt("img_dense"), _pt("img_sparse")],
        "text-sparse": [_pt("img_sparse"), _pt("img_dense")],
        "visual": [],
    }
    fused = qdrant_store.weighted_rrf_fuse(
        rankings,
        weights=qdrant_store.get_intent_weights("semantic_description"),
        limit=2,
    )
    assert [point.id for point in fused] == ["img_dense", "img_sparse"]


def test_weighted_rrf_deduplicates_same_image_across_legs():
    rankings = {
        "text-dense": [_pt("img_a")],
        "text-sparse": [_pt("img_a")],
        "visual": [_pt("img_a")],
    }
    fused = qdrant_store.weighted_rrf_fuse(rankings, weights=qdrant_store.get_intent_weights(None), limit=10)
    assert len(fused) == 1
    assert fused[0].id == "img_a"


def test_ensure_collections_bootstraps_once(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(qdrant_store, "_collections_ready", False)
    monkeypatch.setattr(qdrant_store, "bootstrap_qdrant", lambda url=None: calls.append(url))

    qdrant_store.ensure_collections()
    qdrant_store.ensure_collections()

    assert calls == [qdrant_store.QDRANT_URL]

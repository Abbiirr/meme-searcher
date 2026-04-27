from __future__ import annotations

from vidsearch.query import retrieve_images as retrieve_mod


def test_retrieve_images_skips_visual_leg_when_disabled(monkeypatch):
    monkeypatch.setattr(retrieve_mod, "ENABLE_VISUAL_QUERY", False)
    monkeypatch.setattr(retrieve_mod, "classify_intent", lambda query: "semantic_description")
    monkeypatch.setattr(retrieve_mod, "encode_text", lambda query: ([0.1], {1: 0.4}))
    monkeypatch.setattr(
        retrieve_mod,
        "encode_text_visual",
        lambda query: (_ for _ in ()).throw(AssertionError("visual leg should be skipped")),
    )
    monkeypatch.setattr(retrieve_mod.qdrant_store, "search_hybrid", lambda **kwargs: [])

    out = retrieve_mod.retrieve_images("orange food items on a tray", limit=5)

    assert out["intent"] == "semantic_description"
    assert out["hits"] == []


def test_retrieve_images_uses_configured_rerank_cap(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr(retrieve_mod, "ENABLE_VISUAL_QUERY", False)
    monkeypatch.setattr(retrieve_mod, "classify_intent", lambda query: "semantic_description")
    monkeypatch.setattr(retrieve_mod, "encode_text", lambda query: ([0.1], {1: 0.4}))
    monkeypatch.setattr(retrieve_mod, "_RERANK_TOP_K_BY_INTENT", {"semantic_description": 10})

    def fake_search_hybrid(*, text_dense, text_sparse, visual, limit, intent):
        captured["visual"] = visual
        captured["limit"] = limit
        captured["intent"] = intent
        return []

    monkeypatch.setattr(retrieve_mod.qdrant_store, "search_hybrid", fake_search_hybrid)

    retrieve_mod.retrieve_images("orange food items on a tray", limit=5)

    assert captured == {
        "visual": [],
        "limit": 10,
        "intent": "semantic_description",
    }


def test_retrieve_images_uses_requested_limit_as_candidate_floor(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr(retrieve_mod, "ENABLE_VISUAL_QUERY", False)
    monkeypatch.setattr(retrieve_mod, "classify_intent", lambda query: "semantic_description")
    monkeypatch.setattr(retrieve_mod, "encode_text", lambda query: ([0.1], {1: 0.4}))
    monkeypatch.setattr(retrieve_mod, "_RERANK_TOP_K_BY_INTENT", {"semantic_description": 10})

    def fake_search_hybrid(*, text_dense, text_sparse, visual, limit, intent):
        captured["limit"] = limit
        return []

    monkeypatch.setattr(retrieve_mod.qdrant_store, "search_hybrid", fake_search_hybrid)

    retrieve_mod.retrieve_images("orange food items on a tray", limit=20)

    assert captured["limit"] == 20


def test_warm_retrieval_runtime_skips_visual_warmup_when_disabled(monkeypatch):
    calls: list[str] = []

    monkeypatch.setattr(retrieve_mod, "ENABLE_VISUAL_QUERY", False)
    monkeypatch.setattr(retrieve_mod.qdrant_store, "ensure_collections", lambda: calls.append("collections"))
    monkeypatch.setattr(retrieve_mod, "_get_bge", lambda: calls.append("bge"))
    monkeypatch.setattr(retrieve_mod, "_get_reranker", lambda: calls.append("reranker"))
    monkeypatch.setattr(retrieve_mod, "_get_tokenizer", lambda: calls.append("tokenizer"))
    monkeypatch.setattr(
        retrieve_mod,
        "_get_siglip",
        lambda: (_ for _ in ()).throw(AssertionError("visual warmup should be skipped")),
    )

    retrieve_mod.warm_retrieval_runtime()

    assert calls == ["collections", "bge", "reranker", "tokenizer"]

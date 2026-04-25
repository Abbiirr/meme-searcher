from __future__ import annotations

from types import SimpleNamespace

from vidsearch.query import rerank_images as rerank_mod


def test_get_reranker_uses_trust_remote_code_for_local_model(monkeypatch, tmp_path):
    model_dir = tmp_path / "embeddings" / "jina-reranker-v2-base-multilingual"
    model_dir.mkdir(parents=True)
    monkeypatch.setattr(rerank_mod, "MODEL_ROOT", str(tmp_path))
    monkeypatch.setattr(rerank_mod, "_reranker", None)

    calls: list[tuple[str, bool | None]] = []

    class FakeAutoModel:
        @staticmethod
        def from_pretrained(path, trust_remote_code=None):
            calls.append((path, trust_remote_code))
            return SimpleNamespace()

    import sys

    fake_transformers = SimpleNamespace(AutoModelForSequenceClassification=FakeAutoModel)
    monkeypatch.setitem(sys.modules, "transformers", fake_transformers)

    model = rerank_mod._get_reranker()

    assert model is not None
    assert calls == [(str(model_dir), True)]


def test_get_tokenizer_uses_trust_remote_code_for_local_model(monkeypatch, tmp_path):
    model_dir = tmp_path / "embeddings" / "jina-reranker-v2-base-multilingual"
    model_dir.mkdir(parents=True)
    monkeypatch.setattr(rerank_mod, "MODEL_ROOT", str(tmp_path))
    monkeypatch.setattr(rerank_mod, "_tokenizer", None)

    calls: list[tuple[str, bool | None]] = []

    class FakeAutoTokenizer:
        @staticmethod
        def from_pretrained(path, trust_remote_code=None):
            calls.append((path, trust_remote_code))
            return SimpleNamespace()

    import sys

    fake_transformers = SimpleNamespace(AutoTokenizer=FakeAutoTokenizer)
    monkeypatch.setitem(sys.modules, "transformers", fake_transformers)

    tokenizer = rerank_mod._get_tokenizer()

    assert tokenizer is not None
    assert calls == [(str(model_dir), True)]

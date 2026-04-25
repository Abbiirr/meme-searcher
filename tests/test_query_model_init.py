from __future__ import annotations

import sys
import threading
import time
from types import SimpleNamespace

from vidsearch.query import encoders
from vidsearch.query import rerank_images


def test_get_bge_initializes_once_under_concurrency(monkeypatch, tmp_path):
    monkeypatch.setattr(encoders, "_bge_model", None)
    monkeypatch.setattr(encoders, "_bge_lock", threading.Lock())

    calls: list[tuple[str, bool]] = []
    created = object()

    class FakeBGE:
        def __init__(self, path, use_fp16):
            time.sleep(0.05)
            calls.append((path, use_fp16))

        def __repr__(self):
            return "FakeBGE"

    fake_module = SimpleNamespace(BGEM3FlagModel=lambda path, use_fp16=True: created if not calls.append((path, use_fp16)) else created)
    # Replace with a real callable that sleeps before returning the singleton.
    class SlowBGEFactory:
        def __call__(self, path, use_fp16=True):
            time.sleep(0.05)
            calls.append((path, use_fp16))
            return created

    monkeypatch.setitem(sys.modules, "FlagEmbedding", SimpleNamespace(BGEM3FlagModel=SlowBGEFactory()))

    results: list[object] = []

    def worker():
        results.append(encoders._get_bge(str(tmp_path)))

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert calls == [("BAAI/bge-m3", True)]
    assert results == [created, created, created, created]


def test_get_siglip_initializes_once_under_concurrency(monkeypatch, tmp_path):
    model_dir = tmp_path / "embeddings" / "siglip2-so400m-patch16-384"
    model_dir.mkdir(parents=True)
    (model_dir / "config.json").write_text("{}")

    monkeypatch.setattr(encoders, "_siglip_processor", None)
    monkeypatch.setattr(encoders, "_siglip_model", None)
    monkeypatch.setattr(encoders, "_siglip_lock", threading.Lock())

    processor_calls: list[str] = []
    model_calls: list[str] = []

    class FakeProcessor:
        @staticmethod
        def from_pretrained(path):
            time.sleep(0.05)
            processor_calls.append(path)
            return SimpleNamespace()

    class FakeModel:
        def __init__(self):
            self.device = "cpu"

        def to(self, device):
            self.device = device
            return self

        def eval(self):
            return self

        @staticmethod
        def from_pretrained(path):
            time.sleep(0.05)
            model_calls.append(path)
            return FakeModel()

    fake_torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False))
    fake_transformers = SimpleNamespace(AutoProcessor=FakeProcessor, AutoModel=FakeModel)
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setitem(sys.modules, "transformers", fake_transformers)

    results: list[tuple[object, object]] = []

    def worker():
        results.append(encoders._get_siglip(str(tmp_path)))

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert processor_calls == [str(model_dir)]
    assert model_calls == [str(model_dir)]
    first = results[0]
    assert results == [first, first, first, first]


def test_get_reranker_initializes_once_under_concurrency(monkeypatch, tmp_path):
    model_dir = tmp_path / "embeddings" / "jina-reranker-v2-base-multilingual"
    model_dir.mkdir(parents=True)
    monkeypatch.setattr(rerank_images, "MODEL_ROOT", str(tmp_path))
    monkeypatch.setattr(rerank_images, "_reranker", None)
    monkeypatch.setattr(rerank_images, "_reranker_lock", threading.Lock())

    calls: list[tuple[str, bool | None]] = []
    created = SimpleNamespace()

    class FakeAutoModel:
        @staticmethod
        def from_pretrained(path, trust_remote_code=None):
            time.sleep(0.05)
            calls.append((path, trust_remote_code))
            return created

    monkeypatch.setitem(
        sys.modules,
        "transformers",
        SimpleNamespace(AutoModelForSequenceClassification=FakeAutoModel),
    )

    results: list[object] = []

    def worker():
        results.append(rerank_images._get_reranker())

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert calls == [(str(model_dir), True)]
    assert results == [created, created, created, created]


def test_get_tokenizer_initializes_once_under_concurrency(monkeypatch, tmp_path):
    model_dir = tmp_path / "embeddings" / "jina-reranker-v2-base-multilingual"
    model_dir.mkdir(parents=True)
    monkeypatch.setattr(rerank_images, "MODEL_ROOT", str(tmp_path))
    monkeypatch.setattr(rerank_images, "_tokenizer", None)
    monkeypatch.setattr(rerank_images, "_tokenizer_lock", threading.Lock())

    calls: list[tuple[str, bool | None]] = []
    created = SimpleNamespace()

    class FakeAutoTokenizer:
        @staticmethod
        def from_pretrained(path, trust_remote_code=None):
            time.sleep(0.05)
            calls.append((path, trust_remote_code))
            return created

    monkeypatch.setitem(
        sys.modules,
        "transformers",
        SimpleNamespace(AutoTokenizer=FakeAutoTokenizer),
    )

    results: list[object] = []

    def worker():
        results.append(rerank_images._get_tokenizer())

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert calls == [(str(model_dir), True)]
    assert results == [created, created, created, created]

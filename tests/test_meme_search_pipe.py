from __future__ import annotations

import importlib.util
from pathlib import Path


PIPE_PATH = (
    Path(__file__).resolve().parents[1]
    / "infra"
    / "open_webui"
    / "functions"
    / "meme_search_pipe.py"
)


def load_pipe_module():
    spec = importlib.util.spec_from_file_location("meme_search_pipe", PIPE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_extract_query_reads_last_user_text():
    module = load_pipe_module()
    body = {
        "messages": [
            {"role": "system", "content": "ignored"},
            {"role": "user", "content": [{"type": "text", "text": "first prompt"}]},
            {"role": "assistant", "content": "ignored"},
            {"role": "user", "content": "orange food items on a tray"},
        ]
    }
    assert module._extract_query(body) == "orange food items on a tray"


def test_format_search_markdown_renders_inline_image(monkeypatch):
    module = load_pipe_module()
    monkeypatch.setenv("VIDSEARCH_PUBLIC_BASE_URL", "http://127.0.0.1:8000")
    result = {
        "intent": "semantic_description",
        "hits": [
            {
                "rank": 1,
                "image_id": "abc123",
                "source_uri": "data/meme/10933027.png",
                "thumbnail_uri": "http://api:8000/thumbnail/abc123.webp",
                "ocr_excerpt": "",
                "retrieval_score": 0.81,
                "rerank_score": 0.94,
                "feedback_select_url": "http://127.0.0.1:8000/feedback/confirm/select-token",
                "feedback_reject_url": "http://127.0.0.1:8000/feedback/confirm/reject-token",
                "feedback_undo_url": "http://127.0.0.1:8000/feedback/confirm/undo-token",
            }
        ],
        "feedback_none_correct_url": "http://127.0.0.1:8000/feedback/confirm/none-token",
    }

    rendered = module.format_search_markdown(result, "orange food items on a tray")

    assert "Intent: `semantic_description`" in rendered
    assert "![meme](http://127.0.0.1:8000/thumbnail/abc123.webp)" in rendered
    assert "[Open full image](http://127.0.0.1:8000/image/abc123)" in rendered
    assert "Source: `data/meme/10933027.png`" in rendered
    assert "Rank `1` | Retrieval `0.8100` | Rerank `0.9400`" in rendered
    assert "Feedback: [Select](http://127.0.0.1:8000/feedback/confirm/select-token)" in rendered
    assert "[Reject](http://127.0.0.1:8000/feedback/confirm/reject-token)" in rendered
    assert "[Undo](http://127.0.0.1:8000/feedback/confirm/undo-token)" in rendered
    assert "[None of these are correct](http://127.0.0.1:8000/feedback/confirm/none-token)" in rendered


def test_search_sends_owui_user_id(monkeypatch):
    module = load_pipe_module()
    captured = {}

    class DummyResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def read(self):
            return b'{"intent":"semantic_description","hits":[]}'

    def fake_urlopen(req, timeout):
        captured["payload"] = req.data
        captured["timeout"] = timeout
        return DummyResponse()

    monkeypatch.setattr(module.request, "urlopen", fake_urlopen)

    module._search("jobs meme", 3, {"id": "owui-user-1"})

    assert b'"owui_user_id": "owui-user-1"' in captured["payload"]
    assert captured["timeout"] == 240


def test_format_search_markdown_handles_no_hits():
    module = load_pipe_module()
    rendered = module.format_search_markdown({"intent": "fuzzy_text", "hits": []}, "jobs meme")
    assert "No local meme match found for `jobs meme`." in rendered

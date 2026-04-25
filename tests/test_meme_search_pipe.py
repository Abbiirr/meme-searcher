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
            }
        ],
    }

    rendered = module.format_search_markdown(result, "orange food items on a tray")

    assert "Intent: `semantic_description`" in rendered
    assert "![meme](http://127.0.0.1:8000/thumbnail/abc123.webp)" in rendered
    assert "[Open full image](http://127.0.0.1:8000/image/abc123)" in rendered
    assert "Source: `data/meme/10933027.png`" in rendered
    assert "Rank `1` | Retrieval `0.8100` | Rerank `0.9400`" in rendered


def test_format_search_markdown_handles_no_hits():
    module = load_pipe_module()
    rendered = module.format_search_markdown({"intent": "fuzzy_text", "hits": []}, "jobs meme")
    assert "No local meme match found for `jobs meme`." in rendered

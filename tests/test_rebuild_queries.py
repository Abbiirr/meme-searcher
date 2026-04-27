from __future__ import annotations

import uuid

from vidsearch.eval.rebuild_queries import (
    Candidate,
    _build_exact,
    _build_fuzzy,
    _build_mixed,
    _build_semantic,
    _mostly_ascii,
)


def _candidate(**overrides) -> Candidate:
    data = {
        "image_id": "img_123",
        "source_uri": "data/meme/example.jpg",
        "ocr_full_text": "The secret ingredient is crime.",
        "caption_literal": "A man speaks on a city street.",
        "caption_figurative": "A darkly humorous public confession.",
        "template_name": "unknown",
        "tags": ["man", "street", "crime"],
    }
    data.update(overrides)
    return Candidate(**data)


def test_build_exact_wraps_phrase_in_quotes():
    query = _build_exact(_candidate())
    assert query is not None
    uuid.UUID(query["query_id"])
    assert query["intent"] == "exact_text"
    assert query["text"] == '"the secret ingredient is crime"'


def test_build_fuzzy_marks_query_as_text_reference():
    query = _build_fuzzy(_candidate())
    assert query is not None
    assert query["intent"] == "fuzzy_text"
    assert query["text"].startswith("text says something like ")


def test_build_semantic_uses_meme_about_prefix():
    query = _build_semantic(_candidate())
    assert query is not None
    assert query["intent"] == "semantic_description"
    assert query["text"].startswith("meme about ")


def test_build_mixed_prefers_template_name_when_present():
    query = _build_mixed(_candidate(template_name="doge"))
    assert query is not None
    assert query["intent"] == "mixed_visual_description"
    assert query["text"].startswith("doge meme about ")


def test_build_mixed_falls_back_to_visual_anchor():
    query = _build_mixed(_candidate(template_name="unknown", caption_literal="A dog looks smug in a forest."))
    assert query is not None
    assert query["text"].startswith("dog meme about ")


def test_build_exact_skips_mostly_non_ascii_ocr():
    query = _build_exact(_candidate(ocr_full_text="এটা একদম বাংলা লেখা।"))
    assert query is None


def test_mostly_ascii_threshold():
    assert _mostly_ascii("hello world")
    assert not _mostly_ascii("এটা বাংলা")

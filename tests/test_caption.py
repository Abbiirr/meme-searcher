"""Pure-function tests for vidsearch.ingest.caption.

No network, no gateway, no Postgres — only the template whitelist, the
retrieval_text builder, and the small parsing helpers.
"""

from __future__ import annotations

import pytest
import requests

from vidsearch.ingest import caption as cap


# ---------------------------------------------------------------------------
# Template whitelist validation
# ---------------------------------------------------------------------------


def test_validate_template_exact_match():
    assert cap._validate_template("drake") == "drake"
    assert cap._validate_template("this is fine") == "this is fine"


def test_validate_template_case_and_punct_normalised():
    assert cap._validate_template("  Distracted-Boyfriend  ") == "distracted boyfriend"
    assert cap._validate_template("WOMAN YELLING AT CAT!") == "woman yelling at cat"


def test_validate_template_unknown_returns_sentinel():
    assert cap._validate_template("never seen before meme") == cap.UNKNOWN_TEMPLATE
    assert cap._validate_template("") == cap.UNKNOWN_TEMPLATE
    assert cap._validate_template("unknown") == cap.UNKNOWN_TEMPLATE


def test_validate_template_hyphen_variant_matches():
    # 'grus plan' and 'gru's plan' are both in the whitelist; either spelling
    # should survive normalisation.
    assert cap._validate_template("gru's plan") in {"grus plan", "gru s plan", "gru plan"}


# ---------------------------------------------------------------------------
# Clipping helpers
# ---------------------------------------------------------------------------


def test_clip_sentence_truncates_multi_line():
    raw = 'A man stands in a field.\nA second sentence that should not land.\nThird.'
    clipped = cap._clip_sentence(raw)
    assert clipped == "A man stands in a field."


def test_clip_sentence_strips_quotes():
    assert cap._clip_sentence('"quoted phrase"') == "quoted phrase"
    assert cap._clip_sentence("'single quoted'") == "single quoted"


def test_parse_tags_dedupes_and_caps_at_six():
    raw = "cat, cat, table, indoor, shocked, angry, crumb, extra, tooMany"
    tags = cap._parse_tags(raw)
    assert len(tags) <= cap.MAX_TAGS
    assert "cat" in tags and tags.count("cat") == 1


def test_parse_tags_drops_empty_and_sanitises():
    raw = "cat , , TABLE!, in-door\n ignored line"
    tags = cap._parse_tags(raw)
    assert tags[0] == "cat"
    assert "table" in tags
    assert "in-door" in tags
    assert "ignored" not in tags  # second line is dropped


def test_parse_tags_empty():
    assert cap._parse_tags("") == []
    assert cap._parse_tags("   \n  ") == []


# ---------------------------------------------------------------------------
# build_retrieval_text — the BGE-M3 input format from retrieval plan §2.3
# ---------------------------------------------------------------------------


def test_build_retrieval_text_full():
    captions = cap.Captions(
        literal="A man smiles while clearly in pain.",
        figurative="pretending to be fine",
        template="hide the pain harold",
        tags=["man", "smile", "pain"],
    )
    out = cap.build_retrieval_text(captions, ocr_text="HIDE THE PAIN")
    assert "[CAP_LIT] A man smiles while clearly in pain." in out
    assert "[CAP_FIG] pretending to be fine" in out
    assert "[TEMPLATE] hide the pain harold" in out
    assert "[TAGS] man, smile, pain" in out
    assert "[OCR] HIDE THE PAIN" in out
    # Order matters — retrieval plan §2.3 fixes the sequence.
    lines = out.splitlines()
    order = [next(i for i, ln in enumerate(lines) if ln.startswith(tag))
             for tag in ["[CAP_LIT]", "[CAP_FIG]", "[TEMPLATE]", "[TAGS]", "[OCR]"]]
    assert order == sorted(order), f"channels out of order: {lines}"


def test_build_retrieval_text_skips_empty_channels():
    captions = cap.Captions(literal="", figurative="", template=cap.UNKNOWN_TEMPLATE, tags=[])
    out = cap.build_retrieval_text(captions, ocr_text="just ocr")
    assert out == "[OCR] just ocr"


def test_build_retrieval_text_no_unknown_template_in_output():
    captions = cap.Captions(
        literal="foo",
        template=cap.UNKNOWN_TEMPLATE,
    )
    out = cap.build_retrieval_text(captions, ocr_text=None)
    assert "[TEMPLATE]" not in out
    assert out == "[CAP_LIT] foo"


def test_build_retrieval_text_all_empty():
    assert cap.build_retrieval_text(cap.Captions(), ocr_text="") == ""
    assert cap.build_retrieval_text(cap.Captions(), ocr_text=None) == ""


def test_captions_populated_flag():
    assert not cap.Captions().populated
    assert cap.Captions(literal="x").populated
    assert cap.Captions(tags=["a"]).populated
    assert cap.Captions(template="drake").populated
    assert not cap.Captions(template=cap.UNKNOWN_TEMPLATE).populated


def test_caption_image_retries_fallback_model(monkeypatch):
    monkeypatch.setattr(cap, "CAPTION_MODEL", "broken-primary")
    monkeypatch.setattr(cap, "CAPTION_MODEL_FALLBACK", "vision")
    monkeypatch.setattr(cap, "_image_request_url", lambda path: "data:image/png;base64,abc")

    calls: list[str] = []

    def fake_bundle(data_url, model):
        calls.append(model)
        if model == "broken-primary":
            raise requests.HTTPError("boom")
        return cap.Captions(literal="literal", figurative="figurative", template="drake", tags=["meme"])

    monkeypatch.setattr(cap, "_run_caption_bundle", fake_bundle)

    result = cap.caption_image("example.png")

    assert result.literal == "literal"
    assert calls == ["broken-primary", "vision"]


def test_caption_image_returns_empty_when_all_models_fail(monkeypatch):
    monkeypatch.setattr(cap, "CAPTION_MODEL", "broken-primary")
    monkeypatch.setattr(cap, "CAPTION_MODEL_FALLBACK", "still-broken")
    monkeypatch.setattr(cap, "_image_request_url", lambda path: "data:image/png;base64,abc")
    monkeypatch.setattr(cap, "_run_caption_bundle", lambda data_url, model: (_ for _ in ()).throw(requests.HTTPError("boom")))

    result = cap.caption_image("example.png")

    assert result == cap.Captions()


def test_image_request_url_uses_file_mode_when_path_is_under_data_root(monkeypatch, tmp_path):
    data_root = tmp_path / "data"
    image_path = data_root / "meme" / "example.png"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"png")

    monkeypatch.setenv("VIDSEARCH_MEDIA_URL_MODE", "file")
    monkeypatch.setenv("VIDSEARCH_DATA_ROOT", str(data_root))

    assert cap._image_request_url(image_path) == "file://meme/example.png"


def test_image_request_url_transcodes_webp_to_cached_png_in_file_mode(monkeypatch, tmp_path):
    from PIL import Image

    data_root = tmp_path / "data"
    image_path = data_root / "meme" / "example.webp"
    image_path.parent.mkdir(parents=True)
    Image.new("RGB", (8, 8), "red").save(image_path, format="WEBP")

    monkeypatch.setenv("VIDSEARCH_MEDIA_URL_MODE", "file")
    monkeypatch.setenv("VIDSEARCH_DATA_ROOT", str(data_root))

    url = cap._image_request_url(image_path)

    assert url.startswith("file://.vidsearch_media_cache/")
    cache_path = data_root / url.replace("file://", "").replace("/", "\\")
    assert cache_path.exists()
    assert cache_path.suffix.lower() == ".png"

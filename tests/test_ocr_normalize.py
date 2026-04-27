from __future__ import annotations

from vidsearch.ingest.caption import Captions, build_retrieval_text
from vidsearch.ingest.ocr_normalize import normalize_ocr_text, repair_mojibake_text


def _mojibake(text: str) -> str:
    return text.encode("utf-8").decode("latin1")


def test_repair_mojibake_text_recovers_bangla_utf8_latin1():
    mojibake = _mojibake("এই কথা আর কাউরে কইবা না")

    repaired = repair_mojibake_text(mojibake)

    assert "এই কথা" in repaired
    assert "à¦" not in repaired


def test_normalize_ocr_text_repairs_mojibake_before_embedding():
    boxes = [{"text": _mojibake("এই কথা"), "conf": 0.99}]

    embed_text, full_text, _ = normalize_ocr_text(boxes)

    assert embed_text == "এই কথা"
    assert full_text == "এই কথা"


def test_build_retrieval_text_repairs_mojibake_ocr():
    text = build_retrieval_text(Captions(literal="A Bangla meme."), _mojibake("এই কথা"))

    assert "[OCR] এই কথা" in text

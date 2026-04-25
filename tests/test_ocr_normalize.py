from vidsearch.ingest.ocr_normalize import normalize_ocr_text


def test_normalize_drops_low_confidence_from_embed():
    boxes = [
        {"text": "Hello", "conf": 0.9},
        {"text": "World", "conf": 0.3},
        {"text": "Meme", "conf": 0.8},
    ]
    embed, full, _ = normalize_ocr_text(boxes, confidence_threshold=0.6)
    assert "hello" in embed
    assert "meme" in embed
    assert "world" not in embed
    assert "Hello" in full
    assert "World" in full
    assert "Meme" in full


def test_normalize_keeps_all_in_full_text():
    boxes = [
        {"text": "A", "conf": 0.1},
        {"text": "B", "conf": 0.9},
    ]
    _, full, _ = normalize_ocr_text(boxes)
    assert "A" in full
    assert "B" in full


def test_normalize_collapses_whitespace():
    boxes = [
        {"text": "  hello   world  ", "conf": 0.9},
    ]
    embed, _, _ = normalize_ocr_text(boxes)
    assert "  " not in embed


def test_normalize_empty():
    embed, full, boxes = normalize_ocr_text([])
    assert embed == ""
    assert full == ""


def test_normalize_drops_no_text_placeholders():
    boxes = [
        {"text": "There is no text in the image.", "conf": 1.0},
        {"text": "No visible text", "conf": 1.0},
    ]
    embed, full, kept = normalize_ocr_text(boxes)
    assert embed == ""
    assert full == ""
    assert kept == boxes

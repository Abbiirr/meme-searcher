import io
import logging
import re
import unicodedata

logger = logging.getLogger(__name__)

_NO_TEXT_MARKERS = {
    "the image contains no text",
    "image contains no text",
    "there is no text in the image",
    "there is no text",
    "there is no visible text",
    "no text",
    "no visible text",
}


def _normalized_token(text: str) -> str:
    cleaned = unicodedata.normalize("NFKC", str(text or ""))
    cleaned = cleaned.strip().lower()
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = re.sub(r"[.!?]+$", "", cleaned)
    return cleaned.strip()


def is_placeholder_ocr_text(text: str) -> bool:
    return _normalized_token(text) in _NO_TEXT_MARKERS


def normalize_ocr_text(raw_boxes: list[dict], confidence_threshold: float = 0.6) -> tuple[str, str, list[dict]]:
    all_tokens = []
    embed_tokens = []

    for box in raw_boxes:
        text = box.get("text", "").strip()
        if not text or is_placeholder_ocr_text(text):
            continue
        conf = box.get("conf", 0.0)
        all_tokens.append(text)
        if conf >= confidence_threshold:
            embed_tokens.append(text)

    full_text = " ".join(t for t in all_tokens if t)

    embed_text = " ".join(embed_tokens)
    embed_text = unicodedata.normalize("NFKC", embed_text)
    embed_text = embed_text.lower()
    embed_text = re.sub(r"\s+", " ", embed_text).strip()

    return embed_text, full_text, raw_boxes

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

_MOJIBAKE_MARKERS = ("à¦", "à§", "à¥")
_BANGLA_RE = re.compile(r"[\u0980-\u09ff]")


def _bangla_chars(text: str) -> int:
    return len(_BANGLA_RE.findall(text or ""))


def repair_mojibake_text(text: str) -> str:
    """Repair common UTF-8-as-Latin-1 mojibake seen in Bangla OCR output."""
    value = str(text or "")
    if not value or not any(marker in value for marker in _MOJIBAKE_MARKERS):
        return value
    try:
        repaired = value.encode("latin1").decode("utf-8")
    except UnicodeError:
        return value
    if _bangla_chars(repaired) > _bangla_chars(value):
        return repaired
    return value


def _normalized_token(text: str) -> str:
    cleaned = unicodedata.normalize("NFKC", repair_mojibake_text(str(text or "")))
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
        text = repair_mojibake_text(box.get("text", "")).strip()
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

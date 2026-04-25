"""OCR pass for Phase 0 meme ingest.

Two backends, selected by `VIDSEARCH_OCR_BACKEND`:

    - gateway (default): routes to OCR-capable models exposed behind LiteLLM
    - local: loads PaddleOCR in-process from the machine's pip install

The gateway path is the primary Phase 0 route. The local path stays as an
offline fallback so a dev without gateway access can still make progress.

Both paths return the same shape: `list[dict]` with keys `text`, `conf`,
`bbox`. The gateway path synthesizes zero-area bboxes because the current
gateway wrappers do not expose geometry.
"""

from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path
from typing import Any

from vidsearch.config import _load_dotenv_defaults
from vidsearch.ingest.media_urls import image_request_url

_load_dotenv_defaults()

logger = logging.getLogger(__name__)

OCR_BACKEND = os.environ.get("VIDSEARCH_OCR_BACKEND", "gateway").strip().lower()
LITELLM_URL = os.environ.get("LITELLM_URL", "http://127.0.0.1:4000").rstrip("/")
LITELLM_MASTER_KEY = os.environ.get("LITELLM_MASTER_KEY", "")
OCR_MODEL = os.environ.get("VIDSEARCH_OCR_MODEL", "glm-ocr-wrapper")
OCR_MODEL_FALLBACK = os.environ.get("VIDSEARCH_OCR_MODEL_FALLBACK", "glm-ocr")
OCR_FALLBACK_MODEL = os.environ.get("VIDSEARCH_OCR_FALLBACK_MODEL", "glm-ocr")
OCR_FALLBACK_MODEL_FALLBACK = os.environ.get("VIDSEARCH_OCR_FALLBACK_MODEL_FALLBACK", "glm-ocr")
OCR_TIMEOUT_S = float(os.environ.get("VIDSEARCH_OCR_TIMEOUT_S", "30"))
OCR_MAX_TOKENS = int(os.environ.get("VIDSEARCH_OCR_MAX_TOKENS", "512"))
OCR_RETRIES = int(os.environ.get("VIDSEARCH_OCR_RETRIES", "1"))
OCR_RETRY_BACKOFF_S = float(os.environ.get("VIDSEARCH_OCR_RETRY_BACKOFF_S", "2"))

_OCR_PROMPT = (
    "Extract every piece of text visible in this image, preserving the "
    "reading order (top-to-bottom, left-to-right). Return the text only, "
    "one line per distinct caption block, no commentary, no translation."
)

_ROLE_PREFIX_RE = re.compile(r"^\**\s*(user|assistant|system)\s*:\s*", re.IGNORECASE)
_NO_TEXT_MARKERS = {
    "the image contains no text",
    "image contains no text",
    "no text",
    "no text visible",
    "no visible text",
    "there is no text",
    "there is no visible text",
}


# ---------------------------------------------------------------------------
# Local PaddleOCR backend (legacy / fallback)
# ---------------------------------------------------------------------------

_ocr_engine = None


def _get_local_engine():
    global _ocr_engine
    if _ocr_engine is not None:
        return _ocr_engine
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
    from paddleocr import PaddleOCR

    _ocr_engine = PaddleOCR(
        ocr_version="PP-OCRv5",
        lang="en",
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
    )
    return _ocr_engine


def _run_ocr_local(image_path: str) -> list[dict]:
    engine = _get_local_engine()
    result = engine.ocr(image_path)

    boxes: list[dict] = []
    if result and result[0]:
        for line in result[0]:
            bbox = line[0]
            text = line[1][0]
            conf = float(line[1][1])
            x_coords = [p[0] for p in bbox]
            y_coords = [p[1] for p in bbox]
            boxes.append(
                {
                    "text": text,
                    "conf": conf,
                    "bbox": [
                        min(x_coords),
                        min(y_coords),
                        max(x_coords),
                        max(y_coords),
                    ],
                }
            )
    return boxes


# ---------------------------------------------------------------------------
# Gateway backend (primary)
# ---------------------------------------------------------------------------

def _image_request_url(path: str | Path) -> str:
    return image_request_url(path, component="ocr")


def _ocr_models() -> list[str]:
    models: list[str] = []
    for candidate in (
        OCR_MODEL,
        OCR_MODEL_FALLBACK,
        OCR_FALLBACK_MODEL,
        OCR_FALLBACK_MODEL_FALLBACK,
    ):
        value = (candidate or "").strip()
        if value and value not in models:
            models.append(value)
    return models


def _call_gateway_ocr(data_url: str, model: str) -> str:
    import requests

    if not LITELLM_MASTER_KEY:
        raise RuntimeError("LITELLM_MASTER_KEY not set; gateway OCR unavailable")

    payload: dict[str, Any] = {
        "model": model,
        "temperature": 0.0,
        "max_tokens": OCR_MAX_TOKENS,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_url}},
                    {"type": "text", "text": _OCR_PROMPT},
                ],
            }
        ],
    }
    last_error: Exception | None = None
    for attempt in range(1, OCR_RETRIES + 2):
        try:
            response = requests.post(
                f"{LITELLM_URL}/v1/chat/completions",
                json=payload,
                headers={
                    "Authorization": f"Bearer {LITELLM_MASTER_KEY}",
                    "Content-Type": "application/json",
                },
                timeout=OCR_TIMEOUT_S,
            )
            response.raise_for_status()
            data = response.json()
            choices = data.get("choices") or []
            if not choices:
                return ""
            message = choices[0].get("message") or {}
            content = message.get("content") or ""
            if isinstance(content, list):
                content = " ".join(
                    part.get("text", "")
                    for part in content
                    if isinstance(part, dict) and part.get("type") == "text"
                )
            return str(content)
        except requests.HTTPError as error:
            status = getattr(error.response, "status_code", None)
            retryable = status == 429 or (status is not None and status >= 500)
            last_error = error
            if not retryable or attempt > OCR_RETRIES:
                raise
            sleep_s = OCR_RETRY_BACKOFF_S * attempt
            logger.warning(
                "OCR gateway %s attempt %d/%d returned HTTP %s; retrying in %.1fs",
                model,
                attempt,
                OCR_RETRIES + 1,
                status,
                sleep_s,
            )
            time.sleep(sleep_s)
        except requests.RequestException as error:
            last_error = error
            if attempt > OCR_RETRIES:
                raise
            sleep_s = OCR_RETRY_BACKOFF_S * attempt
            logger.warning(
                "OCR gateway %s attempt %d/%d failed (%s); retrying in %.1fs",
                model,
                attempt,
                OCR_RETRIES + 1,
                error,
                sleep_s,
            )
            time.sleep(sleep_s)
    if last_error is not None:
        raise last_error
    return ""


def _call_gateway_ocr_candidates(data_url: str) -> str:
    import requests

    candidates = _ocr_models()
    last_error: requests.RequestException | None = None
    for index, model in enumerate(candidates, start=1):
        try:
            return _call_gateway_ocr(data_url, model)
        except requests.RequestException as error:
            last_error = error
            if index < len(candidates):
                logger.warning("OCR gateway %s failed (%s); trying fallback", model, error)
                continue
            raise
    if last_error is not None:
        raise last_error
    return ""


def _sanitize_gateway_line(raw: str) -> str | None:
    text = raw.strip()
    if not text or text.startswith("```"):
        return None

    text = text.strip("`").strip()
    role_match = _ROLE_PREFIX_RE.match(text)
    if role_match:
        role = role_match.group(1).lower()
        text = text[role_match.end():].strip()
        if role == "user":
            return None

    text = re.sub(r"^[>\-*0-9\.\)\s]+", "", text).strip()
    if not text:
        return None

    normalized = re.sub(r"\s+", " ", text.lower()).strip(" .:;!-")
    if normalized in _NO_TEXT_MARKERS:
        return None
    if normalized.startswith("extract every piece of text visible"):
        return None
    return text


def _parse_gateway_lines(raw: str) -> list[dict]:
    if not raw:
        return []

    boxes: list[dict] = []
    for line in raw.splitlines():
        text = _sanitize_gateway_line(line)
        if not text:
            continue
        boxes.append(
            {
                "text": text,
                "conf": 1.0,
                "bbox": [0.0, 0.0, 0.0, 0.0],
            }
        )
    return boxes


def _run_ocr_gateway(image_path: str) -> list[dict]:
    import requests

    data_url = _image_request_url(image_path)
    try:
        raw = _call_gateway_ocr_candidates(data_url)
    except requests.RequestException as error:
        logger.warning("OCR gateway unavailable for %s: %s", image_path, error)
        return []
    return _parse_gateway_lines(raw)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_ocr(image_path: str) -> list[dict]:
    """Return a list of `{text, conf, bbox}` dicts for the given image."""
    if OCR_BACKEND == "local":
        return _run_ocr_local(image_path)
    if OCR_BACKEND == "gateway":
        return _run_ocr_gateway(image_path)
    logger.warning("unknown VIDSEARCH_OCR_BACKEND=%r; falling back to gateway", OCR_BACKEND)
    return _run_ocr_gateway(image_path)


def get_ocr_engine(model_root: str | None = None):  # noqa: ARG001 - kept for signature compatibility
    if OCR_BACKEND == "local":
        return _get_local_engine()
    return {"backend": "gateway", "model": OCR_MODEL, "url": LITELLM_URL}

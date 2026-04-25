"""VLM captioning pass for Phase 0 meme ingest.

Per docs/PHASE_0_RETRIEVAL_PLAN.md section 2.3, each image gets four short
labels emitted by a gateway-routed vision model:

    - caption_literal: one-sentence literal description of what is depicted
    - caption_figurative: one short phrase capturing the implied meaning / joke
    - template_name: canonical template name or the sentinel "unknown"
    - tags: up to 6 lowercase comma-separated tags

These four labels plus normalized OCR are assembled into a `retrieval_text`
blob by `build_retrieval_text()` using the exact separator format required
by BGE-M3 at query time.

Gateway is the primary inference surface. If the primary vision model fails,
we retry once with a configured fallback model before returning empty captions.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import re
import time
from pathlib import Path

import requests

from vidsearch.config import _load_dotenv_defaults
from vidsearch.ingest.media_urls import image_request_url

_load_dotenv_defaults()

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

LITELLM_URL = os.environ.get("LITELLM_URL", "http://127.0.0.1:4000").rstrip("/")
LITELLM_MASTER_KEY = os.environ.get("LITELLM_MASTER_KEY", "")
CAPTION_MODEL = os.environ.get("VIDSEARCH_CAPTION_MODEL", "vision")
CAPTION_MODEL_FALLBACK = os.environ.get("VIDSEARCH_CAPTION_MODEL_FALLBACK", "qwen3.6-vlm-wrapper")
CAPTION_TIMEOUT_S = float(os.environ.get("VIDSEARCH_CAPTION_TIMEOUT_S", "45"))
CAPTION_MAX_TOKENS = int(os.environ.get("VIDSEARCH_CAPTION_MAX_TOKENS", "48"))
CAPTION_RETRIES = int(os.environ.get("VIDSEARCH_CAPTION_RETRIES", "2"))
CAPTION_RETRY_BACKOFF_S = float(os.environ.get("VIDSEARCH_CAPTION_RETRY_BACKOFF_S", "2"))

_TEMPLATE_WHITELIST_PATH = Path(
    os.environ.get(
        "VIDSEARCH_TEMPLATE_WHITELIST",
        str(Path(__file__).resolve().parents[2] / "infra" / "data" / "template_whitelist.txt"),
    )
)

UNKNOWN_TEMPLATE = "unknown"
MAX_TAGS = 6


# Prompts (PHASE_0_RETRIEVAL_PLAN.md section 2.3). Kept terse and deterministic.
PROMPT_LITERAL = (
    "In one sentence, describe literally what is shown in this image. "
    "No interpretation, no meme names. Plain description only."
)
PROMPT_FIGURATIVE = (
    "In one short phrase, what feeling or situation does this meme convey? "
    "Answer in under 12 words. No quotation marks."
)
PROMPT_TEMPLATE = (
    "Name the canonical meme template shown in this image, lowercase, "
    "in under 6 words. If you are not sure, answer exactly: unknown"
)
PROMPT_TAGS = (
    "List up to 6 lowercase single-word tags that describe this meme, "
    "comma-separated, no explanations. Example: cat, surprised, table, indoor"
)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class Captions:
    """The four VLM-emitted labels for one image."""

    literal: str = ""
    figurative: str = ""
    template: str = UNKNOWN_TEMPLATE
    tags: list[str] = dataclasses.field(default_factory=list)

    @property
    def populated(self) -> bool:
        return bool(self.literal or self.figurative or self.tags or self.template != UNKNOWN_TEMPLATE)


# ---------------------------------------------------------------------------
# Template whitelist
# ---------------------------------------------------------------------------


_whitelist_cache: set[str] | None = None


def _load_template_whitelist() -> set[str]:
    global _whitelist_cache
    if _whitelist_cache is not None:
        return _whitelist_cache
    whitelist: set[str] = set()
    try:
        for raw in _TEMPLATE_WHITELIST_PATH.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            whitelist.add(_normalise_template(line))
    except FileNotFoundError:
        logger.warning(
            "template whitelist not found at %s; every template will be 'unknown'",
            _TEMPLATE_WHITELIST_PATH,
        )
    _whitelist_cache = whitelist
    return whitelist


def _normalise_template(value: str) -> str:
    normalized = value.strip().lower()
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _validate_template(raw: str) -> str:
    if not raw:
        return UNKNOWN_TEMPLATE
    candidate = _normalise_template(raw)
    if not candidate or candidate == UNKNOWN_TEMPLATE:
        return UNKNOWN_TEMPLATE
    whitelist = _load_template_whitelist()
    if candidate in whitelist:
        return candidate
    for entry in whitelist:
        if entry.replace(" ", "") == candidate.replace(" ", ""):
            return entry
    return UNKNOWN_TEMPLATE


# ---------------------------------------------------------------------------
# Gateway call
# ---------------------------------------------------------------------------

def _image_request_url(path: str | Path) -> str:
    return image_request_url(path, component="caption")


def _call_vlm(data_url: str, prompt: str, model: str) -> str:
    if not LITELLM_MASTER_KEY:
        raise RuntimeError("LITELLM_MASTER_KEY not set; cannot call caption gateway")
    payload = {
        "model": model,
        "temperature": 0.0,
        "max_tokens": CAPTION_MAX_TOKENS,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_url}},
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    }
    last_error: Exception | None = None
    for attempt in range(1, CAPTION_RETRIES + 2):
        try:
            response = requests.post(
                f"{LITELLM_URL}/v1/chat/completions",
                json=payload,
                headers={
                    "Authorization": f"Bearer {LITELLM_MASTER_KEY}",
                    "Content-Type": "application/json",
                },
                timeout=CAPTION_TIMEOUT_S,
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
            return str(content).strip()
        except requests.HTTPError as error:
            status = getattr(error.response, "status_code", None)
            retryable = status == 429 or (status is not None and status >= 500)
            last_error = error
            if not retryable or attempt > CAPTION_RETRIES:
                raise
            sleep_s = CAPTION_RETRY_BACKOFF_S * attempt
            logger.warning(
                "caption gateway %s attempt %d/%d returned HTTP %s; retrying in %.1fs",
                model,
                attempt,
                CAPTION_RETRIES + 1,
                status,
                sleep_s,
            )
            time.sleep(sleep_s)
        except requests.RequestException as error:
            last_error = error
            if attempt > CAPTION_RETRIES:
                raise
            sleep_s = CAPTION_RETRY_BACKOFF_S * attempt
            logger.warning(
                "caption gateway %s attempt %d/%d failed (%s); retrying in %.1fs",
                model,
                attempt,
                CAPTION_RETRIES + 1,
                error,
                sleep_s,
            )
            time.sleep(sleep_s)
    if last_error is not None:
        raise last_error
    return ""


def _caption_models() -> list[str]:
    models: list[str] = []
    for candidate in (CAPTION_MODEL, CAPTION_MODEL_FALLBACK):
        value = (candidate or "").strip()
        if value and value not in models:
            models.append(value)
    return models


def _run_caption_bundle(data_url: str, model: str) -> Captions:
    literal = _clip_sentence(_call_vlm(data_url, PROMPT_LITERAL, model))
    figurative = _clip_phrase(_call_vlm(data_url, PROMPT_FIGURATIVE, model))
    template_raw = _clip_phrase(_call_vlm(data_url, PROMPT_TEMPLATE, model), max_chars=64)
    tags_raw = _call_vlm(data_url, PROMPT_TAGS, model)
    return Captions(
        literal=literal,
        figurative=figurative,
        template=_validate_template(template_raw),
        tags=_parse_tags(tags_raw),
    )


# ---------------------------------------------------------------------------
# Clipping helpers
# ---------------------------------------------------------------------------


def _clip_sentence(raw: str, max_chars: int = 240) -> str:
    if not raw:
        return ""
    text = raw.strip().strip('"').strip("'")
    text = text.split("\n", 1)[0].strip()
    if len(text) > max_chars:
        text = text[:max_chars].rstrip()
    return text


def _clip_phrase(raw: str, max_chars: int = 96) -> str:
    return _clip_sentence(raw, max_chars=max_chars)


def _parse_tags(raw: str) -> list[str]:
    if not raw:
        return []
    head = raw.split("\n", 1)[0]
    parts = re.split(r"[,;]", head)
    tags: list[str] = []
    seen: set[str] = set()
    for part in parts:
        token = re.sub(r"[^a-z0-9\-]+", "", part.strip().lower())
        if not token or token in seen:
            continue
        seen.add(token)
        tags.append(token)
        if len(tags) >= MAX_TAGS:
            break
    return tags


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def caption_image(path: str | Path) -> Captions:
    """Run the 4-prompt VLM pass. Failures return empty Captions."""
    try:
        data_url = _image_request_url(path)
    except Exception as error:
        logger.error("caption: failed to read %s: %s", path, error)
        return Captions()

    candidates = _caption_models()
    for index, model in enumerate(candidates, start=1):
        try:
            return _run_caption_bundle(data_url, model)
        except requests.HTTPError as error:
            if index < len(candidates):
                logger.warning(
                    "caption model %s failed for %s (%s); retrying fallback",
                    model,
                    path,
                    error,
                )
                continue
            logger.warning("caption gateway HTTP error for %s: %s", path, error)
            return Captions()
        except requests.RequestException as error:
            if index < len(candidates):
                logger.warning(
                    "caption model %s unreachable for %s (%s); retrying fallback",
                    model,
                    path,
                    error,
                )
                continue
            logger.warning("caption gateway unreachable for %s: %s", path, error)
            return Captions()
        except Exception as error:  # noqa: BLE001 - one bad image must not kill the batch
            logger.warning("caption failed for %s via %s: %s", path, model, error)
            if index >= len(candidates):
                return Captions()
    return Captions()


def build_retrieval_text(captions: Captions, ocr_text: str | None = None) -> str:
    """Assemble the BGE-M3 retrieval blob per PHASE_0_RETRIEVAL_PLAN.md section 2.3."""
    lines: list[str] = []
    if captions.literal:
        lines.append(f"[CAP_LIT] {captions.literal}")
    if captions.figurative:
        lines.append(f"[CAP_FIG] {captions.figurative}")
    if captions.template and captions.template != UNKNOWN_TEMPLATE:
        lines.append(f"[TEMPLATE] {captions.template}")
    if captions.tags:
        lines.append(f"[TAGS] {', '.join(captions.tags)}")
    ocr_clean = (ocr_text or "").strip()
    if ocr_clean:
        lines.append(f"[OCR] {ocr_clean}")
    return "\n".join(lines)


__all__ = [
    "Captions",
    "UNKNOWN_TEMPLATE",
    "caption_image",
    "build_retrieval_text",
]

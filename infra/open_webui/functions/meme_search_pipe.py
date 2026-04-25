"""
title: Meme Search
author: VidSearch
description: Search the local meme corpus and render matching thumbnails inline in Open WebUI.
version: 0.1.0
"""

from __future__ import annotations

import json
import os
from typing import Any
from urllib import error, request


def _search_api_base() -> str:
    return os.environ.get("VIDSEARCH_OWUI_SEARCH_URL", "http://api:8000").rstrip("/")


def _public_api_base() -> str:
    return os.environ.get("VIDSEARCH_PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/")


def _default_limit() -> int:
    raw = os.environ.get("VIDSEARCH_OWUI_LIMIT", "5").strip()
    try:
        value = int(raw)
    except ValueError:
        value = 5
    return max(1, min(value, 10))


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                if item.strip():
                    parts.append(item.strip())
                continue
            if not isinstance(item, dict):
                continue

            item_type = str(item.get("type", "text")).strip().lower()
            if item_type not in {"text", "input_text"}:
                continue

            text = item.get("text") or item.get("content") or item.get("value") or ""
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())

        return " ".join(parts).strip()

    return ""


def _extract_query(body: dict[str, Any]) -> str:
    messages = body.get("messages") or []
    for message in reversed(messages):
        if not isinstance(message, dict):
            continue
        if message.get("role") != "user":
            continue
        text = _content_to_text(message.get("content"))
        if text:
            return text
    return ""


def _normalize_public_url(url: str, image_id: str | None = None) -> str:
    if not url and image_id:
        return f"{_public_api_base()}/thumbnail/{image_id}.webp"

    if not url:
        return ""

    internal = _search_api_base()
    public = _public_api_base()
    if url.startswith(internal):
        return public + url[len(internal) :]
    if url.startswith("http://api:8000"):
        return public + url[len("http://api:8000") :]
    return url


def _search(query: str, limit: int) -> dict[str, Any]:
    payload = json.dumps({"query": query, "limit": limit}).encode("utf-8")
    req = request.Request(
        f"{_search_api_base()}/search",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "vidsearch-owui-pipe/0.1",
        },
        method="POST",
    )

    with request.urlopen(req, timeout=240) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _format_hit(hit: dict[str, Any]) -> str:
    image_id = str(hit.get("image_id", "")).strip()
    thumbnail_url = _normalize_public_url(str(hit.get("thumbnail_uri", "")).strip(), image_id)
    source_uri = str(hit.get("source_uri", "")).strip()
    rank = hit.get("rank", "?")
    retrieval_score = hit.get("retrieval_score")
    rerank_score = hit.get("rerank_score")
    ocr_excerpt = str(hit.get("ocr_excerpt", "")).strip()

    metrics = [f"Rank `{rank}`"]
    if retrieval_score is not None:
        metrics.append(f"Retrieval `{float(retrieval_score):.4f}`")
    if rerank_score is not None:
        metrics.append(f"Rerank `{float(rerank_score):.4f}`")

    lines = []
    if thumbnail_url:
        lines.append(f"![meme]({thumbnail_url})")
        if image_id:
            lines.append("")
            lines.append(f"[Open full image]({_public_api_base()}/image/{image_id})")
    if source_uri:
        lines.append("")
        lines.append(f"Source: `{source_uri}`")
    if metrics:
        lines.append("")
        lines.append(" | ".join(metrics))
    if ocr_excerpt:
        lines.append("")
        lines.append(f"OCR: `{ocr_excerpt}`")
    return "\n".join(lines).strip()


def format_search_markdown(result: dict[str, Any], query: str) -> str:
    hits = result.get("hits") or []
    intent = str(result.get("intent", "semantic_description")).strip() or "semantic_description"

    if not hits:
        return (
            f"No local meme match found for `{query}`.\n\n"
            "Try a more literal quote, a stronger visual description, or fewer concepts."
        )

    blocks = [f"Intent: `{intent}`"]
    for hit in hits:
        blocks.append(_format_hit(hit))

    return "\n\n".join(block for block in blocks if block).strip()


class Pipe:
    def pipe(self, body: dict[str, Any], __user__: dict[str, Any] | None = None) -> str:
        query = _extract_query(body)
        if not query:
            return "Describe the meme you want to find."

        limit = _default_limit()
        try:
            result = _search(query, limit)
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore").strip()
            return (
                "Meme search failed.\n\n"
                f"HTTP `{exc.code}` from the local search API.\n"
                f"{detail or 'No error body returned.'}"
            )
        except error.URLError as exc:
            return (
                "Meme search is unavailable.\n\n"
                f"Could not reach `{_search_api_base()}/search`: {exc.reason}"
            )
        except Exception as exc:
            return f"Meme search failed with an unexpected error: {exc}"

        return format_search_markdown(result, query)

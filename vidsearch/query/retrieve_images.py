"""Phase 0 retrieval orchestrator.

Per docs/PHASE_0_RETRIEVAL_PLAN.md §3–§5:

    1. Classify intent (exact_text | fuzzy_text | semantic_description |
       mixed_visual_description).
    2. Encode the query through BGE-M3 (dense + sparse) and SigLIP-2 text
       tower (visual), then run hybrid RRF over Qdrant's three prefetches.
    3. For each top-K candidate, reconstruct a text document from the
       caption + OCR fields stored in core.image_items, and rerank
       (query, document) pairs through jina-reranker-v2.
    4. Return ranked hits enriched with the payload the OWUI tool needs to
       render inline image links (source_uri / thumbnail_uri).

Intent-conditional per-leg weights are implemented with Qdrant's uniform
server-side RRF for Phase 0; per-leg weighting is a P0-G4 tuning lever we
tighten after the baseline run (see PHASE_0_RETRIEVAL_PLAN.md §10).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from vidsearch.config import (
    ENABLE_VISUAL_QUERY,
    RERANK_TOP_K_EXACT,
    RERANK_TOP_K_FUZZY,
    RERANK_TOP_K_MIXED,
    RERANK_TOP_K_SEMANTIC,
)
from vidsearch.ingest.ocr_normalize import repair_mojibake_text
from vidsearch.query.encoders import _get_bge, _get_siglip, encode_text, encode_text_visual
from vidsearch.query.intent import classify_intent
from vidsearch.query.rerank_images import _get_reranker, _get_tokenizer, rerank
from vidsearch.storage import qdrant as qdrant_store
from vidsearch.storage.pg import get_cursor

logger = logging.getLogger(__name__)


# Intent-conditional rerank cutoffs: how many candidates to present to the
# cross-encoder. Lower cutoffs = faster + tighter; higher = safer recall.
# Defaults drawn from retrieval plan §5. If we later need per-leg RRF
# weighting (plan §4), it will live alongside this table.
_RERANK_TOP_K_BY_INTENT: dict[str, int] = {
    "exact_text": RERANK_TOP_K_EXACT,
    "fuzzy_text": RERANK_TOP_K_FUZZY,
    "semantic_description": RERANK_TOP_K_SEMANTIC,
    "mixed_visual_description": RERANK_TOP_K_MIXED,
}
_DEFAULT_RERANK_TOP_K = RERANK_TOP_K_SEMANTIC


def _fetch_item_rows(cur, image_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Batch-load the caption / OCR columns we use to reconstruct the
    rerank document."""
    if not image_ids:
        return {}
    cur.execute(
        """SELECT i.image_id,
                  i.ocr_full_text,
                  i.caption_literal,
                  i.caption_figurative,
                  i.template_name,
                  i.tags,
                  i.thumbnail_uri,
                  img.source_uri
           FROM core.image_items i
           JOIN core.images img ON i.image_id = img.image_id
           WHERE i.image_id = ANY(%s)""",
        (list(image_ids),),
    )
    rows: dict[str, dict[str, Any]] = {}
    for r in cur.fetchall():
        rows[r[0]] = {
            "ocr_full_text": repair_mojibake_text(r[1] or ""),
            "caption_literal": r[2] or "",
            "caption_figurative": r[3] or "",
            "template_name": r[4] or "",
            "tags": list(r[5] or []),
            "thumbnail_uri": r[6] or "",
            "source_uri": r[7] or "",
        }
    return rows


def _reconstruct_rerank_doc(row: dict[str, Any], ocr_excerpt_chars: int = 200) -> str:
    """Per retrieval plan §5: build a short text doc that gives the
    cross-encoder enough signal without drowning it in raw OCR."""
    parts: list[str] = []
    if row.get("caption_literal"):
        parts.append(row["caption_literal"])
    if row.get("caption_figurative"):
        parts.append(row["caption_figurative"])
    if row.get("template_name"):
        parts.append(f"template: {row['template_name']}")
    if row.get("tags"):
        parts.append("tags: " + ", ".join(row["tags"]))
    ocr = (row.get("ocr_full_text") or "").strip()
    if ocr:
        parts.append(f"text: {ocr[:ocr_excerpt_chars]}")
    return " | ".join(parts).strip()


def warm_retrieval_runtime() -> None:
    started = time.perf_counter()
    logger.info("retrieval prewarm start: visual_query=%s", ENABLE_VISUAL_QUERY)
    try:
        qdrant_store.ensure_collections()
        _get_bge()
        _get_reranker()
        _get_tokenizer()
        if ENABLE_VISUAL_QUERY:
            _get_siglip()
    except Exception as e:
        logger.warning("retrieval prewarm failed (non-fatal): %s", e)
        return

    logger.info(
        "retrieval prewarm complete in %.2fs: visual_query=%s",
        time.perf_counter() - started,
        ENABLE_VISUAL_QUERY,
    )


def retrieve_images(query: str, limit: int = 10) -> dict[str, Any]:
    intent = classify_intent(query)
    # The caller's requested limit is a lower bound on candidate pickup.
    # Otherwise a top_k=20 replay can silently search only the configured
    # top-10/12 intent cap and misclassify targets as missing.
    rerank_cap = max(limit, _RERANK_TOP_K_BY_INTENT.get(intent, _DEFAULT_RERANK_TOP_K))

    text_dense, text_sparse = encode_text(query)
    visual = encode_text_visual(query) if ENABLE_VISUAL_QUERY else []

    candidates = qdrant_store.search_hybrid(
        text_dense=text_dense,
        text_sparse=text_sparse,
        visual=visual,
        limit=rerank_cap,
        intent=intent,
    )

    if not candidates:
        return {
            "query": query,
            "intent": intent,
            "total_returned": 0,
            "hits": [],
        }

    # Qdrant points carry our canonical image_id in payload; fall back to .id.
    hits_by_id: dict[str, dict[str, Any]] = {}
    for base_rank, pt in enumerate(candidates, start=1):
        payload = pt.payload or {}
        iid = payload.get("image_id") or pt.id
        hits_by_id[iid] = {
            "image_id": iid,
            "base_rank": base_rank,
            "retrieval_score": float(pt.score or 0.0),
            "payload": payload,
        }

    with get_cursor() as cur:
        item_rows = _fetch_item_rows(cur, list(hits_by_id.keys()))

    rerank_docs: list[str] = []
    rerank_ids: list[str] = []
    for iid in hits_by_id:
        row = item_rows.get(iid, {})
        doc = _reconstruct_rerank_doc(row)
        # Always give the reranker something — if we have no caption yet for
        # this image, fall back to source_uri stem + OCR so the cross-encoder
        # doesn't see an empty string.
        if not doc:
            doc = f"{row.get('source_uri', '')} {row.get('ocr_full_text', '')}".strip()
        rerank_docs.append(doc)
        rerank_ids.append(iid)

    reranked = rerank(query, rerank_docs, top_k=min(limit, len(rerank_docs)))
    rerank_score_by_id = {rerank_ids[orig_idx]: float(score) for orig_idx, score in reranked}

    # P0-G4 tuning: the cross-encoder improves noisy fuzzy-text queries but
    # empirically degrades exact/mixed queries where OCR/RRF already places the
    # target first. Keep rerank scores for observability, but only let the
    # reranker order fuzzy-text slates.
    if intent == "fuzzy_text":
        ordered_ids = [rerank_ids[orig_idx] for orig_idx, _score in reranked]
        reranker_applied = True
    else:
        ordered_ids = rerank_ids[:limit]
        reranker_applied = False

    hits: list[dict[str, Any]] = []
    for rank_idx, iid in enumerate(ordered_ids):
        base = hits_by_id[iid]
        row = item_rows.get(iid, {})
        ocr = (row.get("ocr_full_text") or "")
        hits.append(
            {
                "rank": rank_idx + 1,
                "base_rank": base["base_rank"],
                "image_id": iid,
                "source_uri": row.get("source_uri") or base["payload"].get("source_uri", ""),
                "thumbnail_uri": row.get("thumbnail_uri") or base["payload"].get("thumbnail_uri", ""),
                "caption_literal": row.get("caption_literal", ""),
                "caption_figurative": row.get("caption_figurative", ""),
                "template_name": row.get("template_name", ""),
                "tags": row.get("tags", []),
                "ocr_excerpt": ocr[:200] if ocr else "",
                "retrieval_score": base["retrieval_score"],
                "rerank_score": rerank_score_by_id.get(iid),
            }
        )

    return {
        "query": query,
        "intent": intent,
        "rerank_cap": rerank_cap,
        "reranker_applied": reranker_applied,
        "total_returned": len(hits),
        "hits": hits,
    }

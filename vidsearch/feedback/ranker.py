from __future__ import annotations

import json
import logging
import random
from functools import lru_cache
from pathlib import Path
from typing import Any

from vidsearch.config import (
    FEEDBACK_EXPLORATION_RATE,
    FEEDBACK_MAX_UPWARD_MOVEMENT,
    FEEDBACK_RANKER_ALPHA,
    FEEDBACK_RANKER_ARTIFACT,
    FEEDBACK_RANKER_ENABLED,
    FEEDBACK_RANKER_SHADOW,
)
from vidsearch.feedback.service import FEATURE_VERSION, feature_snapshot

logger = logging.getLogger(__name__)


FEATURE_KEYS = [
    "bias",
    "rank",
    "base_rank",
    "retrieval_score",
    "rerank_score",
    "has_ocr",
    "has_caption",
    "tag_count",
    "text_overlap",
    "source_path_depth",
    "slate_size",
    "position_fraction",
    "duplicate_pressure",
    "near_duplicate_count",
]


def _numeric(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _rerank_score_value(value: Any) -> float:
    """Feature v1 imputation rule for non-reranked impressions.

    Phase 0 only applies the cross-encoder reranker to fuzzy-text slates, so
    most impressions have no rerank score. Keep feature_version stable by
    zero-filling the score and reporting missingness in trainer diagnostics.
    """
    return _numeric(value, 0.0)


def feature_vector(snapshot: dict[str, Any]) -> list[float]:
    features = snapshot.get("features") or {}
    per = features.get("per_impression") or {}
    slate = features.get("list_level") or {}
    values = {
        "bias": 1.0,
        "rank": _numeric(per.get("rank")),
        "base_rank": _numeric(per.get("base_rank")),
        "retrieval_score": _numeric(per.get("retrieval_score")),
        "rerank_score": _rerank_score_value(per.get("rerank_score")),
        "has_ocr": 1.0 if per.get("has_ocr") else 0.0,
        "has_caption": 1.0 if per.get("has_caption") else 0.0,
        "tag_count": _numeric(per.get("tag_count")),
        "text_overlap": _numeric(per.get("text_overlap")),
        "source_path_depth": _numeric(per.get("source_path_depth")),
        "slate_size": _numeric(slate.get("slate_size")),
        "position_fraction": _numeric(slate.get("position_fraction")),
        "duplicate_pressure": _numeric(slate.get("duplicate_pressure")),
        "near_duplicate_count": _numeric(slate.get("near_duplicate_count")),
    }
    return [values[key] for key in FEATURE_KEYS]


@lru_cache(maxsize=1)
def _load_artifact() -> dict[str, Any] | None:
    if not FEEDBACK_RANKER_ARTIFACT:
        return None
    path = Path(FEEDBACK_RANKER_ARTIFACT)
    if not path.exists():
        logger.warning("feedback ranker artifact not found: %s", path)
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if int(data.get("feature_version", -1)) != FEATURE_VERSION:
        logger.warning("feedback ranker feature_version mismatch; bypassing artifact")
        return None
    if len(data.get("weights") or []) != len(FEATURE_KEYS):
        logger.warning("feedback ranker weight length mismatch; bypassing artifact")
        return None
    if FEEDBACK_RANKER_ENABLED and not data.get("promotion_approved"):
        logger.warning("feedback ranker enabled but artifact is not promotion_approved; bypassing")
        return None
    return data


def _score(weights: list[float], snapshot: dict[str, Any]) -> float:
    vector = feature_vector(snapshot)
    return sum(weight * value for weight, value in zip(weights, vector, strict=True))


def _cap_upward_movement(original: list[dict[str, Any]], desired: list[dict[str, Any]]) -> list[dict[str, Any]]:
    original_positions = {hit["image_id"]: index for index, hit in enumerate(original)}
    final: list[dict[str, Any] | None] = [None] * len(original)

    for desired_hit in desired:
        original_index = original_positions[desired_hit["image_id"]]
        earliest = max(0, original_index - FEEDBACK_MAX_UPWARD_MOVEMENT)
        placed = False
        for index in range(earliest, len(final)):
            if final[index] is None:
                final[index] = desired_hit
                placed = True
                break
        if not placed:
            for index in range(len(final)):
                if final[index] is None:
                    final[index] = desired_hit
                    break

    reranked = [hit for hit in final if hit is not None]
    for index, hit in enumerate(reranked, start=1):
        hit["rank"] = index
    return reranked


def maybe_apply_feedback_ranker(query: str, intent: str, hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not (FEEDBACK_RANKER_SHADOW or FEEDBACK_RANKER_ENABLED):
        return hits

    artifact = _load_artifact()
    if not artifact:
        return hits

    weights = [float(value) for value in artifact["weights"]]
    scored: list[dict[str, Any]] = []
    for hit in hits:
        snapshot = feature_snapshot(query, intent, hit, len(hits))
        learned_score = _score(weights, snapshot)
        updated = dict(hit)
        updated["learned_score"] = learned_score
        scored.append(updated)

    if FEEDBACK_RANKER_SHADOW and not FEEDBACK_RANKER_ENABLED:
        return scored

    desired = sorted(
        scored,
        key=lambda hit: (
            _numeric(hit.get("rerank_score")) + FEEDBACK_RANKER_ALPHA * _numeric(hit.get("learned_score")),
            -int(hit.get("rank") or 0),
        ),
        reverse=True,
    )
    reranked = _cap_upward_movement(scored, desired)
    for index, hit in enumerate(reranked, start=1):
        hit["rank"] = index
    return reranked


def maybe_apply_exploration(hits: list[dict[str, Any]], *, rng: random.Random | None = None) -> list[dict[str, Any]]:
    """Apply the default-off M7 exploration policy.

    Exploration is intentionally conservative: at most one swap inside ranks
    4-8, never introducing new candidates and never touching top 3. The
    env default is 0.0, so Phase 0 ordering is unchanged unless explicitly
    enabled after the feedback promotion gates.
    """
    if FEEDBACK_EXPLORATION_RATE <= 0 or len(hits) < 8:
        return hits

    chooser = rng or random
    if chooser.random() >= FEEDBACK_EXPLORATION_RATE:
        return hits

    updated = [dict(hit) for hit in hits]
    start = 3
    end = min(len(updated), 8)
    if end - start < 2:
        return hits

    first, second = chooser.sample(range(start, end), 2)
    updated[first], updated[second] = updated[second], updated[first]
    for index, hit in enumerate(updated, start=1):
        hit["rank"] = index
        if index - 1 in {first, second}:
            hit["is_exploration"] = True
            hit["exploration_policy"] = "swap_4_8_v1"
            hit["propensity"] = FEEDBACK_EXPLORATION_RATE
            hit["propensity_method"] = "logged_randomized_swap_v1"
    return updated

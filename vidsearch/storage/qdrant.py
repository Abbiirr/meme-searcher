import logging
import uuid
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from qdrant_client import QdrantClient, models

from vidsearch.config import QDRANT_URL, MEME_COLLECTION
from infra.qdrant.bootstrap import bootstrap_qdrant

logger = logging.getLogger(__name__)

_UUID_NS = uuid.uuid5(uuid.NAMESPACE_URL, "vidsearch/meme")
_RRF_K = 60
_PREFETCH_LIMIT = 100

_INTENT_WEIGHTS: dict[str, dict[str, float]] = {
    "exact_text": {"text-dense": 1.0, "text-sparse": 1.5, "visual": 0.3},
    "fuzzy_text": {"text-dense": 1.0, "text-sparse": 1.3, "visual": 0.5},
    "semantic_description": {"text-dense": 1.3, "text-sparse": 0.7, "visual": 0.8},
    "mixed_visual_description": {"text-dense": 1.2, "text-sparse": 0.9, "visual": 1.1},
}
_DEFAULT_WEIGHTS = {"text-dense": 1.0, "text-sparse": 1.0, "visual": 1.0}

_client = None
_collections_ready = False


def get_client() -> QdrantClient:
    global _client
    if _client is None:
        _client = QdrantClient(url=QDRANT_URL)
    return _client


def ensure_collections() -> None:
    global _collections_ready
    if _collections_ready:
        return
    bootstrap_qdrant(QDRANT_URL)
    _collections_ready = True


def _to_uuid(image_id: str) -> str:
    return str(uuid.uuid5(_UUID_NS, image_id))


@dataclass
class FusedPoint:
    id: str
    payload: dict[str, Any]
    score: float


def get_intent_weights(intent: str | None) -> dict[str, float]:
    return dict(_INTENT_WEIGHTS.get(intent or "", _DEFAULT_WEIGHTS))


def _point_identity(point) -> str:
    payload = getattr(point, "payload", None) or {}
    return payload.get("image_id") or str(getattr(point, "id"))


def weighted_rrf_fuse(
    rankings: dict[str, list[Any]],
    *,
    weights: dict[str, float],
    k: int = _RRF_K,
    limit: int = 50,
) -> list[FusedPoint]:
    totals: dict[str, float] = {}
    kept: dict[str, Any] = {}

    for leg, points in rankings.items():
        weight = float(weights.get(leg, 1.0))
        if weight <= 0:
            continue
        for rank, point in enumerate(points, start=1):
            identity = _point_identity(point)
            totals[identity] = totals.get(identity, 0.0) + (weight / (k + rank))
            kept.setdefault(identity, point)

    fused = [
        FusedPoint(
            id=identity,
            payload=(getattr(kept[identity], "payload", None) or {}),
            score=score,
        )
        for identity, score in totals.items()
    ]
    fused.sort(key=lambda point: point.score, reverse=True)
    return fused[:limit]


def _query_leg(
    client: QdrantClient,
    *,
    collection_name: str,
    query: Any,
    using: str,
    limit: int,
) -> list[Any]:
    response = client.query_points(
        collection_name=collection_name,
        query=query,
        using=using,
        limit=limit,
        with_payload=True,
    )
    return response.points


def upsert_point(
    image_id: str,
    source_uri: str,
    thumbnail_uri: str,
    fmt: str,
    width: int,
    height: int,
    has_ocr: bool,
    has_caption: bool,
    text_dense: list[float],
    text_sparse: dict[int, float],
    visual: list[float],
    model_version: dict[str, str] | str | None = None,
):
    ensure_collections()
    client = get_client()
    import time

    sparse_vec = None
    if text_sparse:
        sparse_vec = models.SparseVector(
            indices=list(text_sparse.keys()),
            values=list(text_sparse.values()),
        )

    vector_data = {}
    if text_dense:
        vector_data["text-dense"] = text_dense
    if sparse_vec:
        vector_data["text-sparse"] = sparse_vec
    if visual:
        vector_data["visual"] = visual

    if not vector_data:
        raise ValueError(f"cannot upsert {image_id} without any vectors")

    client.upsert(
        MEME_COLLECTION,
        points=[
            models.PointStruct(
                id=_to_uuid(image_id),
                vector=vector_data,
                payload={
                    "image_id": image_id,
                    "source_uri": source_uri,
                    "thumbnail_uri": thumbnail_uri,
                    "format": fmt,
                    "width": width,
                    "height": height,
                    "has_ocr": has_ocr,
                    "has_caption": has_caption,
                    "ingested_at": int(time.time()),
                    "model_version": model_version or {},
                },
            )
        ],
    )


def get_point(image_id: str, *, with_vectors: bool = False):
    ensure_collections()
    client = get_client()
    points = client.retrieve(
        collection_name=MEME_COLLECTION,
        ids=[_to_uuid(image_id)],
        with_payload=True,
        with_vectors=with_vectors,
    )
    return points[0] if points else None


def delete_point(image_id: str):
    ensure_collections()
    client = get_client()
    client.delete(MEME_COLLECTION, points_selector=models.PointIdsList(points=[_to_uuid(image_id)]))


def search_hybrid(
    text_dense: list[float],
    text_sparse: dict[int, float],
    visual: list[float],
    limit: int = 50,
    intent: str | None = None,
):
    ensure_collections()
    client = get_client()
    per_leg_limit = max(limit, _PREFETCH_LIMIT)
    rankings: dict[str, list[Any]] = {}

    if text_dense:
        rankings["text-dense"] = _query_leg(
            client,
            collection_name=MEME_COLLECTION,
            query=text_dense,
            using="text-dense",
            limit=per_leg_limit,
        )
    if text_sparse:
        rankings["text-sparse"] = _query_leg(
            client,
            collection_name=MEME_COLLECTION,
            query=models.SparseVector(indices=list(text_sparse.keys()), values=list(text_sparse.values())),
            using="text-sparse",
            limit=per_leg_limit,
        )
    if visual:
        rankings["visual"] = _query_leg(
            client,
            collection_name=MEME_COLLECTION,
            query=visual,
            using="visual",
            limit=per_leg_limit,
        )

    if not rankings:
        return []

    return weighted_rrf_fuse(rankings, weights=get_intent_weights(intent), limit=limit)

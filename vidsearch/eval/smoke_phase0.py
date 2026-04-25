from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from vidsearch.ingest.images import ingest_image
from vidsearch.query.retrieve_images import retrieve_images
from vidsearch.storage import pg as pg_store
from vidsearch.storage import qdrant as qdrant_store


def _vector_norm(vector: Any) -> float:
    if vector is None:
        return 0.0
    if isinstance(vector, (list, tuple)):
        values = [float(x) for x in vector]
        return math.sqrt(sum(x * x for x in values))
    if hasattr(vector, "values"):
        values = [float(x) for x in vector.values]
        return math.sqrt(sum(x * x for x in values))
    if isinstance(vector, dict):
        if "values" in vector:
            values = [float(x) for x in vector["values"]]
        else:
            values = [float(x) for x in vector.values()]
        return math.sqrt(sum(x * x for x in values))
    return 0.0


def _load_pg_row(image_id: str) -> dict[str, Any] | None:
    with pg_store.get_cursor() as cur:
        cur.execute(
            """
            SELECT i.image_id, i.source_uri, ii.thumbnail_uri, ii.has_ocr, ii.has_caption,
                   ii.caption_literal, ii.caption_figurative, ii.template_name, ii.tags,
                   ii.retrieval_text
            FROM core.images i
            JOIN core.image_items ii USING (image_id)
            WHERE i.image_id = %s
            """,
            (image_id,),
        )
        row = cur.fetchone()
    if not row:
        return None
    return {
        "image_id": row[0],
        "source_uri": row[1],
        "thumbnail_uri": row[2],
        "has_ocr": row[3],
        "has_caption": row[4],
        "caption_literal": row[5],
        "caption_figurative": row[6],
        "template_name": row[7],
        "tags": row[8] or [],
        "retrieval_text": row[9],
    }


def _load_ingest_steps(image_id: str) -> list[dict[str, Any]]:
    with pg_store.get_cursor() as cur:
        cur.execute(
            """
            SELECT step, state, attempts, meta
            FROM ops.ingest_steps
            WHERE image_id = %s
            ORDER BY step
            """,
            (image_id,),
        )
        rows = cur.fetchall()
    return [
        {"step": row[0], "state": row[1], "attempts": row[2], "meta": row[3]}
        for row in rows
    ]


def _load_qdrant_point(image_id: str) -> dict[str, Any] | None:
    client = qdrant_store.get_client()
    points = client.retrieve(
        collection_name="memes",
        ids=[qdrant_store._to_uuid(image_id)],
        with_payload=True,
        with_vectors=True,
    )
    if not points:
        return None
    point = points[0]
    vectors = point.vector if isinstance(point.vector, dict) else {}
    norms = {name: round(_vector_norm(value), 6) for name, value in vectors.items()}
    payload = point.payload or {}
    return {
        "id": str(point.id),
        "payload": payload,
        "vector_norms": norms,
    }


def smoke_one(path: str, *, query: str | None = None, force: bool = True) -> dict[str, Any]:
    ingest_result = ingest_image(path, force=force)
    image_id = ingest_result.get("image_id")
    report: dict[str, Any] = {
        "path": path,
        "ingest": ingest_result,
    }
    if not image_id:
        return report

    report["postgres"] = _load_pg_row(image_id)
    report["ingest_steps"] = _load_ingest_steps(image_id)
    report["qdrant"] = _load_qdrant_point(image_id)
    if query:
        report["search"] = retrieve_images(query, limit=5)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a Phase 0 smoke proof for one or more images")
    parser.add_argument("--path", action="append", required=True, help="Image path to ingest and inspect")
    parser.add_argument("--query", action="append", default=[], help="Optional query to run after ingest (repeatable)")
    parser.add_argument("--no-force", action="store_true", help="Do not force re-ingest")
    args = parser.parse_args()

    queries = args.query or []
    reports = []
    for index, path in enumerate(args.path):
        query = queries[index] if index < len(queries) else None
        reports.append(smoke_one(path, query=query, force=not args.no_force))
    print(json.dumps(reports, indent=2, default=str))


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from vidsearch.ingest.caption import Captions, build_retrieval_text
from vidsearch.query.encoders import encode_text
from vidsearch.storage import pg as pg_store
from vidsearch.storage import qdrant as qdrant_store


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _coerce_sparse(vector: Any) -> dict[int, float]:
    if not vector:
        return {}
    if isinstance(vector, dict):
        indices = vector.get("indices")
        values = vector.get("values")
        if indices is not None and values is not None:
            return {int(index): float(value) for index, value in zip(indices, values, strict=False)}
        return {int(index): float(value) for index, value in vector.items()}
    if hasattr(vector, "indices") and hasattr(vector, "values"):
        return {int(index): float(value) for index, value in zip(vector.indices, vector.values, strict=False)}
    return {}


def apply_manual_metadata(*, repairs_path: Path, dry_run: bool = False) -> dict[str, Any]:
    repairs = _read_jsonl(repairs_path)
    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for repair in repairs:
        image_id = str(repair.get("image_id") or "")
        if not image_id:
            skipped.append({"reason": "missing_image_id", "row": repair})
            continue

        with pg_store.get_cursor() as cur:
            cur.execute(
                """SELECT img.source_uri,
                          img.width,
                          img.height,
                          img.format,
                          ii.thumbnail_uri
                   FROM core.images img
                   JOIN core.image_items ii USING (image_id)
                   WHERE img.image_id = %s""",
                (image_id,),
            )
            row = cur.fetchone()

        if row is None:
            skipped.append({"image_id": image_id, "reason": "image_not_found"})
            continue

        source_uri, width, height, fmt, thumbnail_uri = row
        captions = Captions(
            literal=str(repair.get("caption_literal") or ""),
            figurative=str(repair.get("caption_figurative") or ""),
            template=str(repair.get("template_name") or "unknown"),
            tags=[str(tag) for tag in repair.get("tags") or []],
        )
        ocr_text = str(repair.get("ocr_text") or "")
        retrieval_text = build_retrieval_text(captions, ocr_text)
        if not retrieval_text.strip():
            skipped.append({"image_id": image_id, "reason": "empty_retrieval_text"})
            continue

        text_dense, text_sparse = encode_text(retrieval_text)
        point = qdrant_store.get_point(image_id, with_vectors=True)
        vectors = getattr(point, "vector", None) or {}
        visual = vectors.get("visual") if isinstance(vectors, dict) else []
        payload = getattr(point, "payload", None) or {}
        model_version = payload.get("model_version") or {}

        if not dry_run:
            with pg_store.get_cursor() as cur:
                cur.execute(
                    """UPDATE core.image_items
                       SET ocr_text = %s,
                           ocr_full_text = %s,
                           caption_text = %s,
                           caption_literal = %s,
                           caption_figurative = %s,
                           template_name = %s,
                           tags = %s,
                           retrieval_text = %s,
                           has_ocr = %s,
                           has_caption = %s
                       WHERE image_id = %s""",
                    (
                        ocr_text or None,
                        ocr_text or None,
                        retrieval_text,
                        captions.literal or None,
                        captions.figurative or None,
                        captions.template if captions.template != "unknown" else None,
                        captions.tags or None,
                        retrieval_text,
                        bool(ocr_text.strip()),
                        bool(captions.literal or captions.figurative or captions.tags),
                        image_id,
                    ),
                )

            qdrant_store.upsert_point(
                image_id=image_id,
                source_uri=str(source_uri),
                thumbnail_uri=thumbnail_uri or "",
                fmt=str(fmt),
                width=int(width),
                height=int(height),
                has_ocr=bool(ocr_text.strip()),
                has_caption=bool(captions.literal or captions.figurative or captions.tags),
                text_dense=text_dense,
                text_sparse=_coerce_sparse(text_sparse),
                visual=list(visual or []),
                model_version=model_version,
            )

        applied.append(
            {
                "image_id": image_id,
                "dry_run": dry_run,
                "retrieval_text_chars": len(retrieval_text),
                "tags": captions.tags,
            }
        )

    return {"status": "complete", "dry_run": dry_run, "applied": applied, "skipped": skipped}


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply reviewed manual metadata repairs and reindex text vectors.")
    parser.add_argument("--repairs", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = apply_manual_metadata(repairs_path=Path(args.repairs), dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

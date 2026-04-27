from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from vidsearch.ingest.caption import Captions, build_retrieval_text
from vidsearch.ingest.ocr_normalize import repair_mojibake_text
from vidsearch.query.encoders import encode_text
from vidsearch.storage import pg as pg_store
from vidsearch.storage import qdrant as qdrant_store


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


def _load_ids_from_analysis(path: Path, *, failure_class: str | None) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    ids = []
    for item in data.get("items") or []:
        if failure_class and item.get("failure_class") != failure_class:
            continue
        image_id = item.get("target_image_id")
        if image_id:
            ids.append(str(image_id))
    return ids


def repair_text_encoding(
    *,
    image_ids: list[str] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    image_ids = list(dict.fromkeys(image_ids or []))
    where = "WHERE (ii.ocr_text LIKE '%à¦%' OR ii.ocr_text LIKE '%à§%' OR ii.ocr_full_text LIKE '%à¦%' OR ii.ocr_full_text LIKE '%à§%')"
    params: tuple[Any, ...] = ()
    if image_ids:
        where = "WHERE img.image_id = ANY(%s)"
        params = (image_ids,)

    with pg_store.get_cursor() as cur:
        cur.execute(
            f"""SELECT img.image_id,
                       img.source_uri,
                       img.width,
                       img.height,
                       img.format,
                       ii.thumbnail_uri,
                       COALESCE(ii.ocr_text, ''),
                       COALESCE(ii.ocr_full_text, ''),
                       COALESCE(ii.caption_literal, ''),
                       COALESCE(ii.caption_figurative, ''),
                       COALESCE(ii.template_name, ''),
                       COALESCE(ii.tags, ARRAY[]::text[]),
                       COALESCE(ii.has_caption, false),
                       COALESCE(ii.has_ocr, false)
                FROM core.images img
                JOIN core.image_items ii USING (image_id)
                {where}
                ORDER BY img.image_id""",
            params,
        )
        rows = cur.fetchall()

    repaired: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for row in rows:
        (
            image_id,
            source_uri,
            width,
            height,
            fmt,
            thumbnail_uri,
            ocr_text,
            ocr_full_text,
            caption_literal,
            caption_figurative,
            template_name,
            tags,
            has_caption,
            has_ocr,
        ) = row
        fixed_ocr_text = repair_mojibake_text(ocr_text)
        fixed_ocr_full_text = repair_mojibake_text(ocr_full_text)
        if fixed_ocr_text == ocr_text and fixed_ocr_full_text == ocr_full_text:
            skipped.append({"image_id": image_id, "reason": "no_encoding_change"})
            continue

        captions = Captions(
            literal=caption_literal,
            figurative=caption_figurative,
            template=template_name or "unknown",
            tags=list(tags or []),
        )
        retrieval_text = build_retrieval_text(captions, fixed_ocr_text)
        text_dense, text_sparse = encode_text(retrieval_text) if retrieval_text.strip() else ([], {})
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
                           retrieval_text = %s
                       WHERE image_id = %s""",
                    (fixed_ocr_text or None, fixed_ocr_full_text or None, retrieval_text or None, image_id),
                )
            qdrant_store.upsert_point(
                image_id=image_id,
                source_uri=source_uri,
                thumbnail_uri=thumbnail_uri or "",
                fmt=fmt,
                width=int(width),
                height=int(height),
                has_ocr=bool(has_ocr),
                has_caption=bool(has_caption),
                text_dense=text_dense,
                text_sparse=_coerce_sparse(text_sparse),
                visual=list(visual or []),
                model_version=model_version,
            )

        repaired.append(
            {
                "image_id": image_id,
                "old_ocr_excerpt": ocr_full_text[:120],
                "new_ocr_excerpt": fixed_ocr_full_text[:120],
                "retrieval_text_chars": len(retrieval_text),
            }
        )

    return {"status": "complete", "dry_run": dry_run, "repaired": repaired, "skipped": skipped}


def main() -> int:
    parser = argparse.ArgumentParser(description="Repair UTF-8-as-Latin-1 mojibake in stored OCR and reindex text vectors.")
    parser.add_argument("--image-id", action="append", default=[])
    parser.add_argument("--analysis")
    parser.add_argument("--failure-class", default="bangla_metadata_under_prompted")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    image_ids = list(args.image_id or [])
    if args.analysis:
        image_ids.extend(_load_ids_from_analysis(Path(args.analysis), failure_class=args.failure_class or None))
    result = repair_text_encoding(image_ids=image_ids or None, dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

import argparse
import hashlib
import logging
import sys
from pathlib import Path

from vidsearch.config import DATA_ROOT
from vidsearch.ids import image_id
from vidsearch.ingest.scanner import scan_corpus
from vidsearch.ingest.image_utils import decode_image, generate_thumbnail
from vidsearch.ingest.ocr import run_ocr
from vidsearch.ingest.ocr_normalize import normalize_ocr_text, is_placeholder_ocr_text
from vidsearch.ingest.caption import Captions, caption_image, build_retrieval_text, UNKNOWN_TEMPLATE
from vidsearch.ingest.fingerprints import POINT_MODEL_KEYS, build_point_model_versions
from vidsearch.config import ENABLE_CAPTIONS
from psycopg.types.json import Json
from vidsearch.storage import pg as pg_store
from vidsearch.storage import minio as minio_store
from vidsearch.storage import qdrant as qdrant_store
from vidsearch.logging_utils import log_event

logger = logging.getLogger(__name__)

INGEST_STEPS = [
    "hash", "decode", "thumbnail", "ocr", "caption",
    "embed_text", "embed_visual", "upsert_pg", "upsert_qdrant",
]


def _error_meta(error: str, **extra) -> dict:
    """Normalize ingest-step failures for ops queries and closeout audits."""
    return {"error": error, "error_reason": error, **extra}


def _captions_from_existing(existing: dict | None) -> Captions:
    if not existing:
        return Captions()
    return Captions(
        literal=existing.get("caption_literal") or "",
        figurative=existing.get("caption_figurative") or "",
        template=existing.get("template_name") or UNKNOWN_TEMPLATE,
        tags=list(existing.get("tags") or []),
    )


def _point_payload(point) -> dict:
    payload = getattr(point, "payload", None)
    return payload if isinstance(payload, dict) else {}


def _point_vectors(point) -> dict:
    vectors = getattr(point, "vector", None)
    return vectors if isinstance(vectors, dict) else {}


def _coerce_dense_vector(vector) -> list[float]:
    if isinstance(vector, (list, tuple)):
        return [float(value) for value in vector]
    return []


def _coerce_sparse_vector(vector) -> dict[int, float]:
    if vector is None:
        return {}
    if isinstance(vector, dict):
        if "indices" in vector and "values" in vector:
            return {
                int(index): float(value)
                for index, value in zip(vector["indices"], vector["values"], strict=False)
            }
        return {int(index): float(value) for index, value in vector.items()}
    indices = getattr(vector, "indices", None)
    values = getattr(vector, "values", None)
    if indices is not None and values is not None:
        return {
            int(index): float(value)
            for index, value in zip(indices, values, strict=False)
        }
    return {}


def ingest_image(path: str | Path, force: bool = False) -> dict:
    path = Path(path)
    result = {"path": str(path), "status": "unknown"}
    seed_model_versions()
    log_event(logger, logging.INFO, "ingest_image_start", path=str(path), force=force)

    raw_bytes = path.read_bytes()
    sha256 = hashlib.sha256(raw_bytes).digest()
    img_id = image_id(raw_bytes)
    existing_pg = None
    existing_point = None

    with pg_store.get_cursor() as cur:
        if not force:
            cur.execute(
                "SELECT image_id FROM core.images WHERE sha256 = %s",
                (sha256,),
            )
            if cur.fetchone():
                pg_store.upsert_ingest_step(cur, img_id, "hash", "done")
                result["status"] = "duplicate"
                result["image_id"] = img_id
                log_event(
                    logger,
                    logging.INFO,
                    "ingest_image_duplicate",
                    path=str(path),
                    image_id=img_id,
                )
                return result

        pg_store.upsert_ingest_step(cur, img_id, "hash", "done")
        if force:
            existing_pg = pg_store.get_image_by_id(cur, img_id)

    if force:
        try:
            existing_point = qdrant_store.get_point(img_id, with_vectors=True)
        except Exception as e:
            logger.warning("existing qdrant point lookup failed for %s: %s", path, e)

    existing_captions = _captions_from_existing(existing_pg)
    existing_retrieval_text = (existing_pg or {}).get("retrieval_text") or ""
    existing_thumbnail_uri = (existing_pg or {}).get("thumbnail_uri") or ""
    existing_ocr_text = (existing_pg or {}).get("ocr_text") or ""
    existing_ocr_full_text = (existing_pg or {}).get("ocr_full_text") or ""
    existing_has_ocr = bool((existing_pg or {}).get("has_ocr")) and any(
        text.strip() and not is_placeholder_ocr_text(text)
        for text in (existing_ocr_text, existing_ocr_full_text)
    )
    existing_has_caption = bool((existing_pg or {}).get("has_caption"))
    existing_vectors = _point_vectors(existing_point)
    existing_payload = _point_payload(existing_point)
    existing_model_versions = existing_payload.get("model_version")
    if not isinstance(existing_model_versions, dict):
        existing_model_versions = {}

    try:
        img, width, height, fmt = decode_image(path)
    except Exception as e:
        logger.error("decode failed for %s: %s", path, e)
        with pg_store.get_cursor() as cur:
            pg_store.upsert_ingest_step(cur, img_id, "decode", "error", _error_meta(str(e)))
        result["status"] = "failed"
        result["error"] = f"decode: {e}"
        log_event(
            logger,
            logging.ERROR,
            "ingest_image_failed",
            path=str(path),
            image_id=img_id,
            stage="decode",
            error=str(e),
        )
        return result

    with pg_store.get_cursor() as cur:
        pg_store.upsert_ingest_step(cur, img_id, "decode", "done")

    thumb_error = None
    try:
        thumb_data = generate_thumbnail(img)
        thumb_uri = minio_store.upload_thumbnail(img_id, thumb_data)
    except Exception as e:
        logger.error("thumbnail failed for %s: %s", path, e)
        thumb_uri = None
        thumb_error = str(e)
        with pg_store.get_cursor() as cur:
            pg_store.upsert_ingest_step(cur, img_id, "thumbnail", "error", _error_meta(str(e)))

    effective_thumb_uri = thumb_uri or existing_thumbnail_uri
    with pg_store.get_cursor() as cur:
        meta = None
        if effective_thumb_uri and not thumb_uri and existing_thumbnail_uri:
            meta = {"preserved": True}
            if thumb_error:
                meta.update(_error_meta(thumb_error))
        elif thumb_error:
            meta = _error_meta(thumb_error)
        pg_store.upsert_ingest_step(cur, img_id, "thumbnail", "done" if effective_thumb_uri else "skipped", meta)

    ocr_boxes = []
    ocr_text = ""
    ocr_full_text = ""
    has_ocr = False
    ocr_ran = False
    ocr_error = None
    try:
        ocr_boxes = run_ocr(str(path))
        ocr_ran = True
        ocr_text, ocr_full_text, _ = normalize_ocr_text(ocr_boxes)
        has_ocr = bool(ocr_text.strip())
    except Exception as e:
        logger.warning("ocr failed/skipped for %s: %s", path, e)
        ocr_error = str(e)

    effective_ocr_text = ocr_text
    effective_ocr_full_text = ocr_full_text
    effective_has_ocr = has_ocr
    ocr_preserved = False
    if not effective_has_ocr and existing_has_ocr:
        effective_ocr_text = existing_ocr_text
        effective_ocr_full_text = existing_ocr_full_text
        effective_has_ocr = bool(effective_ocr_text or effective_ocr_full_text)
        ocr_preserved = effective_has_ocr

    with pg_store.get_cursor() as cur:
        meta = None
        if ocr_preserved:
            meta = {"preserved": True}
            if ocr_error:
                meta.update(_error_meta(ocr_error))
        elif ocr_error:
            meta = _error_meta(ocr_error)
        pg_store.upsert_ingest_step(cur, img_id, "ocr", "done" if effective_has_ocr else "skipped", meta)

    # ── caption step (gateway VLM; emits the 4 labels of retrieval_plan §2.3) ──
    captions = Captions()
    caption_error = None
    if ENABLE_CAPTIONS:
        try:
            captions = caption_image(path)
        except Exception as e:  # noqa: BLE001 — one bad image must not kill the batch
            logger.warning("caption failed for %s: %s", path, e)
            captions = Captions()
            caption_error = str(e)

    effective_captions = captions
    caption_preserved = False
    if not captions.populated and existing_has_caption:
        effective_captions = existing_captions
        caption_preserved = effective_captions.populated
    has_caption = captions.populated
    effective_has_caption = effective_captions.populated

    with pg_store.get_cursor() as cur:
        meta = None
        if caption_preserved:
            meta = {"preserved": True}
            if caption_error:
                meta.update(_error_meta(caption_error))
        elif caption_error:
            meta = _error_meta(caption_error)
        pg_store.upsert_ingest_step(
            cur, img_id, "caption",
            "done" if effective_has_caption else ("skipped" if not ENABLE_CAPTIONS else "error"),
            meta,
        )

    # retrieval_text is what BGE-M3 embeds; built from captions + OCR per §2.3.
    retrieval_text = build_retrieval_text(effective_captions, effective_ocr_text)
    if not retrieval_text.strip():
        retrieval_text = existing_retrieval_text

    text_dense = []
    text_sparse = {}
    text_error = None
    try:
        from vidsearch.query.encoders import encode_text
        embed_input = retrieval_text if retrieval_text.strip() else effective_ocr_text
        if embed_input and embed_input.strip():
            text_dense, text_sparse = encode_text(embed_input)
    except Exception as e:
        logger.error("text embedding failed for %s: %s", path, e)
        text_error = str(e)

    effective_text_dense = text_dense or _coerce_dense_vector(existing_vectors.get("text-dense"))
    effective_text_sparse = text_sparse or _coerce_sparse_vector(existing_vectors.get("text-sparse"))

    with pg_store.get_cursor() as cur:
        meta = None
        if not text_dense and (effective_text_dense or effective_text_sparse):
            meta = {"preserved": True}
            if text_error:
                meta.update(_error_meta(text_error))
        elif text_error:
            meta = _error_meta(text_error)
        pg_store.upsert_ingest_step(
            cur,
            img_id,
            "embed_text",
            "done" if (effective_text_dense or effective_text_sparse) else "skipped",
            meta,
        )

    visual = []
    visual_error = None
    try:
        from vidsearch.query.encoders import encode_visual
        visual = encode_visual(img)
    except Exception as e:
        logger.warning("visual embedding skipped for %s: %s", path, e)
        visual_error = str(e)

    effective_visual = visual or _coerce_dense_vector(existing_vectors.get("visual"))
    if not effective_visual:
        logger.info("no visual embedding available, skipping visual vector")

    with pg_store.get_cursor() as cur:
        meta = None
        if not visual and effective_visual:
            meta = {"preserved": True}
            if visual_error:
                meta.update(_error_meta(visual_error))
        elif visual_error:
            meta = _error_meta(visual_error)
        pg_store.upsert_ingest_step(cur, img_id, "embed_visual", "done" if effective_visual else "skipped", meta)

    with pg_store.get_cursor() as cur:
        pg_store.upsert_image(cur, img_id, sha256, str(path), width, height, fmt)
        pg_store.upsert_image_item(
            cur, img_id, effective_thumb_uri or None, effective_ocr_text or None, effective_ocr_full_text or None,
            ocr_boxes if ocr_boxes else None, effective_has_ocr,
            caption_model="meme_vlm_captioner" if effective_has_caption else None,
            has_caption=effective_has_caption,
            caption_literal=effective_captions.literal or None,
            caption_figurative=effective_captions.figurative or None,
            template_name=effective_captions.template if effective_captions.template != UNKNOWN_TEMPLATE else None,
            tags=effective_captions.tags or None,
            retrieval_text=retrieval_text or None,
        )
        model_revisions = pg_store.get_model_revisions(cur, POINT_MODEL_KEYS)
        pg_store.upsert_ingest_step(cur, img_id, "upsert_pg", "done")

    point_model_versions_current = build_point_model_versions(
        model_revisions,
        has_text_dense=bool(text_dense),
        has_text_sparse=bool(text_sparse),
        has_visual=bool(visual),
        has_caption=has_caption,
        has_ocr=ocr_ran and bool(ocr_text or ocr_full_text or ocr_boxes),
    )
    point_model_versions = {}
    if effective_text_dense:
        if point_model_versions_current.get("text_dense"):
            point_model_versions["text_dense"] = point_model_versions_current["text_dense"]
        elif existing_model_versions.get("text_dense"):
            point_model_versions["text_dense"] = existing_model_versions["text_dense"]
    if effective_text_sparse:
        if point_model_versions_current.get("text_sparse"):
            point_model_versions["text_sparse"] = point_model_versions_current["text_sparse"]
        elif existing_model_versions.get("text_sparse"):
            point_model_versions["text_sparse"] = existing_model_versions["text_sparse"]
    if effective_visual:
        if point_model_versions_current.get("visual"):
            point_model_versions["visual"] = point_model_versions_current["visual"]
        elif existing_model_versions.get("visual"):
            point_model_versions["visual"] = existing_model_versions["visual"]
    if effective_has_caption:
        if point_model_versions_current.get("meme_vlm_captioner"):
            point_model_versions["meme_vlm_captioner"] = point_model_versions_current["meme_vlm_captioner"]
        elif existing_model_versions.get("meme_vlm_captioner"):
            point_model_versions["meme_vlm_captioner"] = existing_model_versions["meme_vlm_captioner"]
    if effective_has_ocr:
        if point_model_versions_current.get("meme_ocr"):
            point_model_versions["meme_ocr"] = point_model_versions_current["meme_ocr"]
        elif existing_model_versions.get("meme_ocr"):
            point_model_versions["meme_ocr"] = existing_model_versions["meme_ocr"]

    qdrant_error = None
    try:
        qdrant_store.upsert_point(
            image_id=img_id,
            source_uri=str(path),
            thumbnail_uri=effective_thumb_uri or "",
            fmt=fmt,
            width=width,
            height=height,
            has_ocr=effective_has_ocr,
            has_caption=effective_has_caption,
            text_dense=effective_text_dense,
            text_sparse=effective_text_sparse,
            visual=effective_visual,
            model_version=point_model_versions,
        )
    except Exception as e:
        logger.error("qdrant upsert failed for %s: %s", path, e)
        qdrant_error = str(e)

    with pg_store.get_cursor() as cur:
        meta = None
        if qdrant_error:
            meta = _error_meta(qdrant_error)
        elif existing_point and (
            ((not text_dense) and bool(effective_text_dense))
            or ((not text_sparse) and bool(effective_text_sparse))
            or ((not visual) and bool(effective_visual))
        ):
            meta = {"preserved": True}
        pg_store.upsert_ingest_step(cur, img_id, "upsert_qdrant", "error" if qdrant_error else "done", meta)

    result["status"] = "ingested"
    result["image_id"] = img_id
    log_event(
        logger,
        logging.INFO,
        "ingest_image_complete",
        path=str(path),
        image_id=img_id,
        has_ocr=effective_has_ocr,
        has_caption=effective_has_caption,
        has_visual=bool(effective_visual),
        has_text=bool(effective_text_dense or effective_text_sparse),
    )
    return result


from vidsearch.ingest.fingerprints import seed_model_versions as _seed_model_versions


def seed_model_versions() -> None:
    """Thin wrapper kept for call-site stability; real implementation
    lives in `vidsearch.ingest.fingerprints` so the FastAPI startup hook
    can call it without importing the heavier `ingest.images` module.
    """
    _seed_model_versions()


def ingest_folder(folder: str | Path) -> dict:
    folder = Path(folder)
    log_event(logger, logging.INFO, "ingest_folder_start", folder=str(folder))

    seed_model_versions()

    scan = scan_corpus(folder)
    log_event(
        logger,
        logging.INFO,
        "ingest_folder_scan",
        folder=str(folder),
        supported=len(scan.supported),
        skipped_unsupported=len(scan.skipped_unsupported),
        skipped_no_extension=len(scan.skipped_no_extension),
        failed_stat=len(scan.failed_stat),
    )

    ingested = 0
    duplicate = 0
    failed = 0

    for i, fpath in enumerate(scan.supported):
        log_event(
            logger,
            logging.INFO,
            "ingest_folder_item",
            folder=str(folder),
            index=i + 1,
            total=len(scan.supported),
            path=str(fpath),
        )
        result = ingest_image(fpath)
        if result["status"] == "ingested":
            ingested += 1
        elif result["status"] == "duplicate":
            duplicate += 1
        elif result["status"] == "failed":
            failed += 1

    summary = {
        "total_seen": scan.total_seen,
        "supported": len(scan.supported),
        "ingested": ingested,
        "duplicate": duplicate,
        "skipped": len(scan.skipped_unsupported) + len(scan.skipped_no_extension),
        "failed": failed,
    }

    with pg_store.get_cursor() as cur:
        cur.execute(
            "INSERT INTO ops.jobs (source_path, state, summary) VALUES (%s, %s, %s)",
            (str(folder), "done", Json(summary)),
        )

    log_event(logger, logging.INFO, "ingest_folder_complete", folder=str(folder), **summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description="Ingest images into the meme search engine")
    parser.add_argument("--path", type=str, help="Path to a single image")
    parser.add_argument("--folder", type=str, help="Path to a folder to scan recursively")
    parser.add_argument("--force", action="store_true", help="Force re-ingest even if duplicate")
    args = parser.parse_args()

    if args.path:
        result = ingest_image(args.path, force=args.force)
        print(result)
    elif args.folder:
        summary = ingest_folder(args.folder)
        print(summary)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()

import logging
import mimetypes
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response

from vidsearch.api.contracts import (
    SearchRequest,
    SearchResponse,
    SearchHit,
    IngestImageRequest,
    IngestFolderRequest,
    DeleteImageResponse,
    FeedbackRequest,
    FeedbackResponse,
)
from vidsearch.storage import pg as pg_store
from vidsearch.storage import qdrant as qdrant_store
from vidsearch.storage import minio as minio_store
from vidsearch.config import PREWARM_RETRIEVAL, PUBLIC_BASE_URL
from vidsearch.logging_utils import log_event

logger = logging.getLogger(__name__)

app = FastAPI(title="VidSearch - Meme Search Engine", version="0.1.0")
_startup_background_started = False
_startup_background_lock = threading.Lock()


@app.middleware("http")
async def log_requests(request, call_next):
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        log_event(
            logger,
            logging.ERROR,
            "http_request",
            method=request.method,
            path=request.url.path,
            query=str(request.url.query),
            status_code=500,
            duration_ms=duration_ms,
        )
        raise

    duration_ms = round((time.perf_counter() - started) * 1000, 2)
    log_event(
        logger,
        logging.INFO,
        "http_request",
        method=request.method,
        path=request.url.path,
        query=str(request.url.query),
        status_code=response.status_code,
        duration_ms=duration_ms,
    )
    return response


def _thumbnail_url(image_id: str) -> str:
    return f"{PUBLIC_BASE_URL}/thumbnail/{image_id}.webp"


def _image_url(image_id: str) -> str:
    return f"{PUBLIC_BASE_URL}/image/{image_id}"


def _background_startup_bookkeeping() -> None:
    """Run non-critical startup bookkeeping off the API boot path."""
    try:
        from vidsearch.ingest.fingerprints import seed_model_versions

        results = seed_model_versions()
        logger.info("model-version seed on startup: %s", results)
    except Exception as e:
        logger.warning("startup seed failed (non-fatal): %s", e)

    if PREWARM_RETRIEVAL:
        try:
            from vidsearch.query.retrieve_images import warm_retrieval_runtime

            warm_retrieval_runtime()
        except Exception as e:
            logger.warning("startup retrieval prewarm failed (non-fatal): %s", e)


@app.on_event("startup")
def _seed_model_versions_on_startup() -> None:
    """Stamp `ops.model_versions` on API boot.

    Per docs/MODEL_GATEWAY.md §4 every model referenced by the search
    hot-path must have a deterministic fingerprint. We seed on boot (not
    only at ingest start) so the API container can serve search immediately
    after a fresh deploy without waiting for an ingest cycle to stamp
    fingerprints.

    Non-fatal: if the gateway is down or Postgres is unreachable the API
    still boots; `seed_model_versions` already swallows its own exceptions.
    """
    global _startup_background_started
    with _startup_background_lock:
        if _startup_background_started:
            return
        _startup_background_started = True

    threading.Thread(
        target=_background_startup_bookkeeping,
        name="vidsearch-startup-bookkeeping",
        daemon=True,
    ).start()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/search", response_model=SearchResponse)
def search(req: SearchRequest):
    from vidsearch.query.retrieve_images import retrieve_images
    try:
        raw = retrieve_images(req.query, limit=req.limit)
    except Exception as e:
        logger.error("search failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

    hits = [
        SearchHit(
            rank=h["rank"],
            image_id=h["image_id"],
            source_uri=h.get("source_uri", ""),
            thumbnail_uri=_thumbnail_url(h["image_id"]),
            ocr_excerpt=h.get("ocr_excerpt", ""),
            retrieval_score=h.get("retrieval_score", 0.0),
            rerank_score=h.get("rerank_score"),
        )
        for h in raw.get("hits", [])[:req.limit]
    ]

    return SearchResponse(
        query=req.query,
        intent=raw.get("intent", "semantic_description"),
        total_returned=len(hits),
        hits=hits,
    )


@app.post("/ingest/image")
def ingest_image(req: IngestImageRequest):
    from vidsearch.ingest.images import ingest_image as _ingest
    result = _ingest(req.path)
    return result


@app.post("/ingest/folder")
def ingest_folder(req: IngestFolderRequest):
    from vidsearch.ingest.images import ingest_folder as _ingest
    result = _ingest(req.folder)
    return result


@app.get("/thumbnail/{image_id}.webp")
def get_thumbnail(image_id: str):
    with pg_store.get_cursor() as cur:
        img = pg_store.get_image_by_id(cur, image_id)
        if not img:
            raise HTTPException(status_code=404, detail="image not found")
        thumb_uri = img.get("thumbnail_uri")
    if not thumb_uri:
        raise HTTPException(status_code=404, detail="thumbnail not found")

    try:
        data = minio_store.download_thumbnail(thumb_uri)
    except Exception as e:
        logger.warning("thumbnail fetch failed for %s: %s", image_id, e)
        raise HTTPException(status_code=404, detail="thumbnail not found") from e

    media_type = "image/webp" if thumb_uri.endswith(".webp") else "image/jpeg"
    return Response(content=data, media_type=media_type)


@app.get("/image/{image_id}")
def get_image(image_id: str):
    with pg_store.get_cursor() as cur:
        img = pg_store.get_image_by_id(cur, image_id)
    if not img:
        raise HTTPException(status_code=404, detail="image not found")

    source_uri = img.get("source_uri")
    if not source_uri:
        raise HTTPException(status_code=404, detail="image file not found")

    path = Path(source_uri)
    if not path.exists():
        raise HTTPException(status_code=404, detail="image file not found")

    media_type, _ = mimetypes.guess_type(path.name)
    return Response(content=path.read_bytes(), media_type=media_type or "application/octet-stream")


@app.delete("/image/{image_id}", response_model=DeleteImageResponse)
def delete_image(image_id: str):
    with pg_store.get_cursor() as cur:
        img = pg_store.get_image_by_id(cur, image_id)
        if not img:
            raise HTTPException(status_code=404, detail="image not found")

        thumb_uri = img.get("thumbnail_uri")
        if thumb_uri:
            try:
                minio_store.delete_object(thumb_uri)
            except Exception as e:
                logger.warning("failed to delete thumbnail %s: %s", thumb_uri, e)

        try:
            qdrant_store.delete_point(image_id)
        except Exception as e:
            logger.warning("failed to delete qdrant point %s: %s", image_id, e)

        pg_store.delete_image(cur, image_id)

    return DeleteImageResponse(image_id=image_id, deleted=True)


@app.post("/feedback", response_model=FeedbackResponse)
def feedback(req: FeedbackRequest):
    with pg_store.get_cursor() as cur:
        cur.execute(
            "INSERT INTO feedback.events (query_text, image_id, signal, value) VALUES (%s, %s, %s, %s) RETURNING event_id",
            (req.query_text, req.image_id, req.signal, req.value),
        )
        event_id = str(cur.fetchone()[0])
    return FeedbackResponse(event_id=event_id)

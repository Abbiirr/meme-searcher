import logging
import mimetypes
import secrets
import threading
import time
from html import escape
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from vidsearch.api.contracts import (
    SearchRequest,
    SearchResponse,
    SearchHit,
    IngestImageRequest,
    IngestFolderRequest,
    DeleteImageResponse,
    FeedbackRequest,
    FeedbackResponse,
    FeedbackJudgmentResponse,
)
from vidsearch.storage import pg as pg_store
from vidsearch.storage import qdrant as qdrant_store
from vidsearch.storage import minio as minio_store
from vidsearch.config import PREWARM_RETRIEVAL, PUBLIC_BASE_URL
from vidsearch.feedback import service as feedback_service
from vidsearch.feedback.ranker import maybe_apply_exploration, maybe_apply_feedback_ranker
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

    raw_hits = list(raw.get("hits", [])[:req.limit])
    raw_hits = maybe_apply_feedback_ranker(req.query, raw.get("intent", "semantic_description"), raw_hits)
    raw_hits = maybe_apply_exploration(raw_hits)
    feedback_meta = feedback_service.log_search_impressions(
        query=req.query,
        intent=raw.get("intent", "semantic_description"),
        hits=raw_hits,
        client_session_id=req.client_session_id,
        owui_user_id=req.owui_user_id,
    )
    impression_meta_by_image = {
        item["image_id"]: item for item in (feedback_meta or {}).get("impressions", [])
    }

    hits = []
    for h in raw_hits:
        meta = impression_meta_by_image.get(h["image_id"], {})
        hits.append(SearchHit(
            rank=h["rank"],
            base_rank=h.get("base_rank"),
            image_id=h["image_id"],
            source_uri=h.get("source_uri", ""),
            thumbnail_uri=_thumbnail_url(h["image_id"]),
            ocr_excerpt=h.get("ocr_excerpt", ""),
            retrieval_score=h.get("retrieval_score", 0.0),
            rerank_score=h.get("rerank_score"),
            learned_score=h.get("learned_score"),
            impression_id=meta.get("impression_id"),
            feedback_select_url=meta.get("select_url"),
            feedback_reject_url=meta.get("reject_url"),
            feedback_undo_url=meta.get("undo_url"),
        ))

    return SearchResponse(
        query=req.query,
        intent=raw.get("intent", "semantic_description"),
        total_returned=len(hits),
        hits=hits,
        search_id=(feedback_meta or {}).get("search_id"),
        ranker_version_id=(feedback_meta or {}).get("ranker_version_id"),
        feedback_enabled=bool(feedback_meta),
        feedback_none_correct_url=(feedback_meta or {}).get("none_correct_url"),
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


def _feedback_html(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)}</title>
  <style>
    body {{ font-family: ui-sans-serif, system-ui, sans-serif; margin: 2rem; line-height: 1.45; }}
    main {{ max-width: 48rem; }}
    code {{ background: #f1f3f5; padding: .1rem .25rem; border-radius: .25rem; }}
    button {{ padding: .6rem .9rem; border: 0; border-radius: .4rem; background: #111827; color: white; }}
  </style>
</head>
<body><main>{body}</main></body>
</html>"""


@app.get("/feedback/confirm/{token}", response_class=HTMLResponse)
def feedback_confirm(token: str):
    from vidsearch.feedback.tokens import FeedbackTokenError, verify_feedback_token

    try:
        payload = verify_feedback_token(token)
    except FeedbackTokenError as exc:
        feedback_service.record_invalid_token_attempt(None, str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    csrf_token = secrets.token_urlsafe(32)
    action = escape(str(payload.get("action", "")))
    body = f"""
<h1>Recording feedback</h1>
<p>Action: <code>{action}</code></p>
<form id="feedback-form" method="post" action="/feedback/judgment">
  <input type="hidden" name="token" value="{escape(token)}">
  <input type="hidden" name="csrf_token" value="{escape(csrf_token)}">
  <noscript><button type="submit">Confirm feedback</button></noscript>
</form>
<script>document.getElementById("feedback-form").submit();</script>
"""
    response = HTMLResponse(_feedback_html("Recording feedback", body))
    response.set_cookie(
        "vidsearch_feedback_csrf",
        csrf_token,
        httponly=True,
        secure=False,
        samesite="strict",
        max_age=300,
    )
    return response


async def _parse_feedback_post(request: Request) -> tuple[str, str | None, bool]:
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        data = await request.json()
        return str(data.get("token") or ""), data.get("csrf_token"), False

    raw = (await request.body()).decode("utf-8", errors="ignore")
    parsed = parse_qs(raw)
    token = (parsed.get("token") or [""])[0]
    csrf_token = (parsed.get("csrf_token") or [None])[0]
    return token, csrf_token, True


def _feedback_result_html(result: dict[str, object]) -> HTMLResponse:
    status = escape(str(result.get("status", "recorded")))
    judgment_id = escape(str(result.get("judgment_id", "")))
    search_id = escape(str(result.get("search_id", "")))
    pairs_created = escape(str(result.get("pairs_created", 0)))
    body = f"""
<h1>Feedback {status}</h1>
<p>Judgment: <code>{judgment_id}</code></p>
<p>Search: <code>{search_id}</code></p>
<p>Preference pairs created: <code>{pairs_created}</code></p>
<p>You can close this tab and return to Open WebUI.</p>
"""
    return HTMLResponse(_feedback_html("Feedback recorded", body))


@app.post("/feedback/judgment", response_model=FeedbackJudgmentResponse)
async def feedback_judgment(request: Request):
    token, csrf_token, wants_html = await _parse_feedback_post(request)
    csrf_cookie = request.cookies.get("vidsearch_feedback_csrf")
    if not csrf_cookie or not csrf_token or not secrets.compare_digest(csrf_cookie, csrf_token):
        raise HTTPException(status_code=403, detail="invalid csrf token")

    try:
        result = feedback_service.record_judgment_from_token(token)
    except feedback_service.FeedbackServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    if wants_html:
        return _feedback_result_html(result)
    return JSONResponse(result)

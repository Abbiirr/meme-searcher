import json as _json
import logging
from contextlib import contextmanager

import psycopg
from psycopg.types.json import Json

from vidsearch.config import DATABASE_URL

logger = logging.getLogger(__name__)


@contextmanager
def get_connection(url: str | None = None):
    conn = psycopg.connect(url or DATABASE_URL)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def get_cursor(url: str | None = None):
    with get_connection(url) as conn:
        yield conn.cursor()


def upsert_image(cur, image_id: str, sha256: bytes, source_uri: str,
                 width: int, height: int, fmt: str, metadata: dict | None = None):
    cur.execute(
        """INSERT INTO core.images (image_id, sha256, source_uri, width, height, format, metadata)
           VALUES (%s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (sha256) DO UPDATE SET source_uri = EXCLUDED.source_uri
           RETURNING image_id""",
        (image_id, sha256, source_uri, width, height, fmt, Json(metadata or {})),
    )
    return cur.fetchone()[0]


def upsert_image_item(cur, image_id: str, thumbnail_uri: str | None,
                      ocr_text: str | None, ocr_full_text: str | None,
                      ocr_boxes: list | None, has_ocr: bool,
                      caption_text: str | None = None,
                      caption_model: str | None = None,
                      has_caption: bool = False,
                      caption_literal: str | None = None,
                      caption_figurative: str | None = None,
                      template_name: str | None = None,
                      tags: list[str] | None = None,
                      retrieval_text: str | None = None):
    cur.execute(
        """INSERT INTO core.image_items
           (image_id, thumbnail_uri, ocr_text, ocr_full_text, ocr_boxes,
            has_ocr, caption_text, caption_model, has_caption,
            caption_literal, caption_figurative, template_name, tags, retrieval_text)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (image_id) DO UPDATE SET
             thumbnail_uri = EXCLUDED.thumbnail_uri,
             ocr_text = EXCLUDED.ocr_text,
             ocr_full_text = EXCLUDED.ocr_full_text,
             ocr_boxes = EXCLUDED.ocr_boxes,
             has_ocr = EXCLUDED.has_ocr,
             caption_text = EXCLUDED.caption_text,
             caption_model = EXCLUDED.caption_model,
             has_caption = EXCLUDED.has_caption,
             caption_literal = EXCLUDED.caption_literal,
             caption_figurative = EXCLUDED.caption_figurative,
             template_name = EXCLUDED.template_name,
             tags = EXCLUDED.tags,
             retrieval_text = EXCLUDED.retrieval_text""",
         (image_id, thumbnail_uri, ocr_text, ocr_full_text,
          Json([b if isinstance(b, dict) else b._asdict() for b in ocr_boxes]) if ocr_boxes else None,
          has_ocr, caption_text, caption_model, has_caption,
          caption_literal, caption_figurative, template_name,
          tags if tags else None, retrieval_text),
    )


def upsert_model_version(cur, model_key: str, family: str, version: str,
                         revision: str | None = None, config: dict | None = None):
    """Seed / refresh a row in ops.model_versions. Idempotent.

    Used by ingest components to stamp the fingerprint of whichever model
    they are actually calling (see docs/MODEL_GATEWAY.md §4)."""
    cur.execute(
        """INSERT INTO ops.model_versions (model_key, family, version, revision, config)
           VALUES (%s, %s, %s, %s, %s)
           ON CONFLICT (model_key) DO UPDATE SET
             family = EXCLUDED.family,
             version = EXCLUDED.version,
             revision = COALESCE(EXCLUDED.revision, ops.model_versions.revision),
             config = EXCLUDED.config,
             activated_at = now()""",
        (model_key, family, version, revision, Json(config or {})),
    )


def get_model_revisions(cur, model_keys: list[str] | tuple[str, ...]) -> dict[str, str | None]:
    cur.execute(
        """SELECT model_key, revision
           FROM ops.model_versions
           WHERE model_key = ANY(%s)""",
        (list(model_keys),),
    )
    return {row[0]: row[1] for row in cur.fetchall()}


def upsert_ingest_step(cur, image_id: str, step: str, state: str, meta: dict | None = None):
    cur.execute(
        """INSERT INTO ops.ingest_steps (image_id, step, state, meta)
           VALUES (%s, %s, %s, %s)
           ON CONFLICT (image_id, step) DO UPDATE SET
             state = EXCLUDED.state,
             attempts = ops.ingest_steps.attempts + 1,
             meta = EXCLUDED.meta,
             updated_at = now()""",
        (image_id, step, state, Json(meta or {})),
    )


def get_ingest_step(cur, image_id: str, step: str) -> str | None:
    cur.execute(
        "SELECT state FROM ops.ingest_steps WHERE image_id = %s AND step = %s",
        (image_id, step),
    )
    row = cur.fetchone()
    return row[0] if row else None


def delete_image(cur, image_id: str) -> bool:
    cur.execute("DELETE FROM core.image_items WHERE image_id = %s", (image_id,))
    cur.execute("DELETE FROM core.images WHERE image_id = %s", (image_id,))
    cur.execute("DELETE FROM ops.ingest_steps WHERE image_id = %s", (image_id,))
    return cur.rowcount > 0


def get_image_by_id(cur, image_id: str) -> dict | None:
    cur.execute(
        """SELECT i.image_id, i.sha256, i.source_uri, i.width, i.height, i.format,
                  it.thumbnail_uri, it.ocr_text, it.ocr_full_text, it.has_ocr,
                  it.has_caption, it.caption_literal, it.caption_figurative,
                  it.template_name, it.tags, it.retrieval_text
           FROM core.images i
           LEFT JOIN core.image_items it ON i.image_id = it.image_id
           WHERE i.image_id = %s""",
        (image_id,),
    )
    row = cur.fetchone()
    if not row:
        return None
    return {
        "image_id": row[0], "sha256": row[1], "source_uri": row[2],
        "width": row[3], "height": row[4], "format": row[5],
        "thumbnail_uri": row[6], "ocr_text": row[7], "ocr_full_text": row[8],
        "has_ocr": row[9], "has_caption": row[10],
        "caption_literal": row[11], "caption_figurative": row[12],
        "template_name": row[13], "tags": row[14], "retrieval_text": row[15],
    }

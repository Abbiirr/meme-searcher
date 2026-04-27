from __future__ import annotations

import hashlib
import re
from typing import Any

from psycopg.types.json import Json

from vidsearch.config import (
    FEEDBACK_ENABLED,
    FEEDBACK_RANKER_ENABLED,
    FEEDBACK_RANKER_SHADOW,
    FEEDBACK_RANKER_VERSION,
    FEEDBACK_SESSION_RATE_LIMIT_PER_HOUR,
    FEEDBACK_USER_RATE_LIMIT_PER_DAY,
    PUBLIC_BASE_URL,
)
from vidsearch.feedback.schema import ensure_feedback_schema
from vidsearch.feedback.tokens import FeedbackTokenError, sign_feedback_token, user_hash, verify_feedback_token
from vidsearch.storage import pg as pg_store


FEATURE_VERSION = 1
PROPENSITY_METHOD = "deterministic_no_ope"
ZERO_UUID = "00000000-0000-0000-0000-000000000000"
VALID_JUDGMENT_ACTIONS = {"select", "reject", "none_correct", "undo"}


class FeedbackServiceError(RuntimeError):
    status_code = 400


class FeedbackNotFoundError(FeedbackServiceError):
    status_code = 404


class FeedbackRateLimitError(FeedbackServiceError):
    status_code = 429


class FeedbackTokenValidationError(FeedbackServiceError):
    status_code = 400


def _ranker_mode() -> str:
    if FEEDBACK_RANKER_ENABLED:
        return "online"
    if FEEDBACK_RANKER_SHADOW:
        return "shadow"
    return "baseline"


def _ranker_version_id() -> str:
    if FEEDBACK_RANKER_ENABLED or FEEDBACK_RANKER_SHADOW:
        return FEEDBACK_RANKER_VERSION
    return "baseline"


def _safe_session_id(client_session_id: str | None, raw_user_ref: str | None = None) -> str:
    value = (client_session_id or "").strip()
    if value:
        return value[:128]
    hashed = user_hash(raw_user_ref)
    if hashed:
        return f"owui:{hashed[:24]}"
    return "anonymous"


def _redact_query(query: str) -> str:
    collapsed = re.sub(r"\s+", " ", query).strip()
    return collapsed[:500]


def _text_overlap(query: str, hit: dict[str, Any]) -> float:
    query_terms = {part for part in re.split(r"\W+", query.lower()) if len(part) > 2}
    if not query_terms:
        return 0.0
    fields = " ".join(
        str(hit.get(key, ""))
        for key in ("caption_literal", "caption_figurative", "ocr_excerpt", "template_name", "source_uri")
    ).lower()
    fields += " " + " ".join(str(tag).lower() for tag in hit.get("tags") or [])
    hit_terms = {part for part in re.split(r"\W+", fields) if len(part) > 2}
    return len(query_terms & hit_terms) / max(len(query_terms), 1)


def feature_snapshot(query: str, intent: str, hit: dict[str, Any], total_results: int) -> dict[str, Any]:
    source_uri = str(hit.get("source_uri") or "")
    tags = [str(tag) for tag in hit.get("tags") or []]
    features = {
        "per_impression": {
            "rank": int(hit.get("rank") or 0),
            "base_rank": int(hit.get("base_rank") or hit.get("rank") or 0),
            "retrieval_score": float(hit.get("retrieval_score") or 0.0),
            "rerank_score": float(hit["rerank_score"]) if hit.get("rerank_score") is not None else None,
            "intent": intent,
            "has_ocr": bool(hit.get("ocr_excerpt")),
            "has_caption": bool(hit.get("caption_literal") or hit.get("caption_figurative")),
            "template_name": hit.get("template_name") or "",
            "tag_count": len(tags),
            "text_overlap": _text_overlap(query, hit),
            "source_path_depth": len([part for part in re.split(r"[\\/]+", source_uri) if part]),
            "source_stem_hash": hashlib.sha256(source_uri.encode("utf-8")).hexdigest()[:16],
        },
        "list_level": {
            "slate_size": int(total_results),
            "position_fraction": (int(hit.get("rank") or 1) - 1) / max(int(total_results) - 1, 1),
            "duplicate_pressure": 0.0,
            "template_diversity": None,
            "near_duplicate_count": 0,
        },
    }
    return {"feature_version": FEATURE_VERSION, "features": features}


def feedback_url(token: str) -> str:
    return f"{PUBLIC_BASE_URL}/feedback/confirm/{token}"


def log_search_impressions(
    *,
    query: str,
    intent: str,
    hits: list[dict[str, Any]],
    client_session_id: str | None = None,
    owui_user_id: str | None = None,
) -> dict[str, Any] | None:
    if not FEEDBACK_ENABLED:
        return None

    ensure_feedback_schema()
    raw_user_ref = owui_user_id or client_session_id
    session_id = _safe_session_id(client_session_id, raw_user_ref)
    hashed_user = user_hash(raw_user_ref)
    ranker_version_id = _ranker_version_id()
    ranker_mode = _ranker_mode()
    exploration_policy = next((str(hit.get("exploration_policy")) for hit in hits if hit.get("is_exploration")), "none")
    if exploration_policy != "none":
        ranker_mode = "exploration"

    with pg_store.get_cursor() as cur:
        cur.execute(
            """INSERT INTO feedback.ranker_versions (ranker_version_id, kind, status, feature_version)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (ranker_version_id) DO NOTHING""",
            (
                ranker_version_id,
                "phase0_order" if ranker_version_id == "baseline" else "pairwise_logistic",
                "baseline" if ranker_version_id == "baseline" else ranker_mode,
                FEATURE_VERSION,
            ),
        )
        cur.execute(
            """INSERT INTO feedback.search_sessions
               (query_text, query_redacted, intent, client_session_id, user_hash, result_count,
                ranker_version_id, ranker_mode, feature_version, propensity_method, exploration_policy)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               RETURNING search_id""",
            (
                query,
                _redact_query(query),
                intent,
                session_id,
                hashed_user,
                len(hits),
                ranker_version_id,
                ranker_mode,
                FEATURE_VERSION,
                PROPENSITY_METHOD,
                exploration_policy,
            ),
        )
        search_id = str(cur.fetchone()[0])

        impression_meta: list[dict[str, Any]] = []
        for hit in hits:
            features = feature_snapshot(query, intent, hit, len(hits))
            cur.execute(
                """INSERT INTO feedback.search_impressions
                   (search_id, image_id, rank, base_rank, retrieval_score, rerank_score, learned_score,
                    propensity, propensity_method, is_exploration, features_jsonb)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING impression_id""",
                (
                    search_id,
                    hit["image_id"],
                    int(hit.get("rank") or 0),
                    int(hit.get("base_rank") or hit.get("rank") or 0),
                    float(hit.get("retrieval_score") or 0.0),
                    float(hit["rerank_score"]) if hit.get("rerank_score") is not None else None,
                    hit.get("learned_score"),
                    float(hit.get("propensity") or 1.0),
                    str(hit.get("propensity_method") or PROPENSITY_METHOD),
                    bool(hit.get("is_exploration")),
                    Json(features),
                ),
            )
            impression_id = str(cur.fetchone()[0])
            select_token = sign_feedback_token(
                search_id=search_id,
                impression_id=impression_id,
                action="select",
                ranker_version_id=ranker_version_id,
                feature_version=FEATURE_VERSION,
            )
            reject_token = sign_feedback_token(
                search_id=search_id,
                impression_id=impression_id,
                action="reject",
                ranker_version_id=ranker_version_id,
                feature_version=FEATURE_VERSION,
            )
            undo_token = sign_feedback_token(
                search_id=search_id,
                impression_id=impression_id,
                action="undo",
                ranker_version_id=ranker_version_id,
                feature_version=FEATURE_VERSION,
            )
            impression_meta.append(
                {
                    "image_id": hit["image_id"],
                    "impression_id": impression_id,
                    "select_url": feedback_url(select_token),
                    "reject_url": feedback_url(reject_token),
                    "undo_url": feedback_url(undo_token),
                }
            )

        none_correct_token = sign_feedback_token(
            search_id=search_id,
            action="none_correct",
            ranker_version_id=ranker_version_id,
            feature_version=FEATURE_VERSION,
        )

    return {
        "search_id": search_id,
        "ranker_version_id": ranker_version_id,
        "feature_version": FEATURE_VERSION,
        "none_correct_url": feedback_url(none_correct_token),
        "impressions": impression_meta,
    }


def _record_invalid_token_attempt(client_session_id: str | None, reason: str) -> None:
    try:
        ensure_feedback_schema()
        with pg_store.get_cursor() as cur:
            cur.execute(
                """INSERT INTO feedback.invalid_token_attempts (client_session_id, reason)
                   VALUES (%s, %s)""",
                (client_session_id, reason),
            )
    except Exception:
        return


def record_invalid_token_attempt(client_session_id: str | None, reason: str) -> None:
    _record_invalid_token_attempt(client_session_id, reason[:200])


def _rate_limit_or_raise(cur, client_session_id: str, hashed_user: str | None) -> None:
    cur.execute(
        """SELECT COUNT(*)
           FROM feedback.judgments
           WHERE client_session_id = %s
             AND created_at >= now() - interval '1 hour'""",
        (client_session_id,),
    )
    if int(cur.fetchone()[0]) >= FEEDBACK_SESSION_RATE_LIMIT_PER_HOUR:
        cur.execute(
            """INSERT INTO feedback.rate_limit_events
               (client_session_id, user_hash, action, bucket, allowed, reason)
               VALUES (%s, %s, 'feedback_write', 'session_hour', false, %s)""",
            (client_session_id, hashed_user, "feedback session rate limit exceeded"),
        )
        raise FeedbackRateLimitError("feedback session rate limit exceeded")

    if hashed_user:
        cur.execute(
            """SELECT COUNT(*)
               FROM feedback.judgments
               WHERE user_hash = %s
                 AND created_at >= now() - interval '1 day'""",
            (hashed_user,),
        )
        if int(cur.fetchone()[0]) >= FEEDBACK_USER_RATE_LIMIT_PER_DAY:
            cur.execute(
                """INSERT INTO feedback.rate_limit_events
                   (client_session_id, user_hash, action, bucket, allowed, reason)
                   VALUES (%s, %s, 'feedback_write', 'user_day', false, %s)""",
                (client_session_id, hashed_user, "feedback user rate limit exceeded"),
            )
            raise FeedbackRateLimitError("feedback user rate limit exceeded")


def _legacy_signal(action: str) -> str:
    if action == "select":
        return "thumbs_up"
    if action == "reject":
        return "thumbs_down"
    return "reported_wrong"


def _generate_pairs_for_selection(cur, judgment_id: str, search_id: str, winner_impression_id: str) -> int:
    cur.execute(
        """SELECT image_id
           FROM feedback.search_impressions
           WHERE impression_id = %s AND search_id = %s""",
        (winner_impression_id, search_id),
    )
    winner = cur.fetchone()
    if not winner:
        raise FeedbackNotFoundError("winner impression not found")
    winner_image_id = winner[0]

    cur.execute(
        """SELECT impression_id, image_id
           FROM feedback.search_impressions
           WHERE search_id = %s AND impression_id <> %s
           ORDER BY rank ASC""",
        (search_id, winner_impression_id),
    )
    inserted = 0
    for loser_impression_id, loser_image_id in cur.fetchall():
        cur.execute(
            """INSERT INTO feedback.preference_pairs
               (search_id, source_judgment_id, winner_impression_id, loser_impression_id,
                winner_image_id, loser_image_id, feature_version, derivation_method, pair_weight)
               VALUES (%s, %s, %s, %s, %s, %s, %s, 'selected_vs_skipped', 1.0)
               ON CONFLICT DO NOTHING""",
            (
                search_id,
                judgment_id,
                winner_impression_id,
                loser_impression_id,
                winner_image_id,
                loser_image_id,
                FEATURE_VERSION,
            ),
        )
        inserted += cur.rowcount
    return inserted


def record_judgment_from_token(token: str) -> dict[str, Any]:
    try:
        payload = verify_feedback_token(token)
    except FeedbackTokenError as exc:
        record_invalid_token_attempt(None, str(exc))
        raise FeedbackTokenValidationError(str(exc)) from exc

    action = str(payload["action"])
    search_id = str(payload["search_id"])
    impression_id = payload.get("impression_id")
    ranker_version_id = str(payload.get("ranker_version_id") or "baseline")
    feature_version = int(payload.get("feature_version") or FEATURE_VERSION)
    token_nonce = str(payload.get("nonce") or "")

    if action not in VALID_JUDGMENT_ACTIONS:
        raise FeedbackTokenValidationError("invalid action")
    if action in {"select", "reject"} and not impression_id:
        raise FeedbackTokenValidationError("image-level feedback requires an impression")

    ensure_feedback_schema()
    with pg_store.get_cursor() as cur:
        cur.execute(
            """SELECT client_session_id, user_hash, query_text, feature_version
               FROM feedback.search_sessions
               WHERE search_id = %s""",
            (search_id,),
        )
        session = cur.fetchone()
        if not session:
            raise FeedbackNotFoundError("search session not found")
        client_session_id, hashed_user, query_text, session_feature_version = session

        if feature_version != int(session_feature_version):
            raise FeedbackTokenValidationError("feature version mismatch")

        image_id = None
        if impression_id:
            cur.execute(
                """SELECT image_id
                   FROM feedback.search_impressions
                   WHERE search_id = %s AND impression_id = %s""",
                (search_id, impression_id),
            )
            row = cur.fetchone()
            if not row:
                raise FeedbackTokenValidationError("feedback impression was not shown in this search")
            image_id = row[0]
        elif action not in {"none_correct", "undo"}:
            raise FeedbackTokenValidationError("unshown-image feedback is rejected")

        if action != "undo":
            cur.execute(
                """SELECT judgment_id
                   FROM feedback.judgments
                   WHERE search_id = %s
                     AND COALESCE(impression_id, %s::uuid) = COALESCE(%s::uuid, %s::uuid)
                     AND action = %s
                     AND tombstoned_at IS NULL""",
                (search_id, ZERO_UUID, impression_id, ZERO_UUID, action),
            )
            existing = cur.fetchone()
            if existing:
                judgment_id = str(existing[0])
                pairs_created = (
                    _generate_pairs_for_selection(cur, judgment_id, search_id, str(impression_id))
                    if action == "select" and impression_id
                    else 0
                )
                return {
                    "status": "duplicate",
                    "judgment_id": judgment_id,
                    "search_id": search_id,
                    "impression_id": impression_id,
                    "pairs_created": pairs_created,
                }

        _rate_limit_or_raise(cur, client_session_id, hashed_user)

        if action == "undo":
            cur.execute(
                """UPDATE feedback.judgments
                   SET tombstoned_at = now()
                   WHERE search_id = %s
                     AND COALESCE(impression_id, %s::uuid) = COALESCE(%s::uuid, %s::uuid)
                     AND tombstoned_at IS NULL
                     AND action <> 'undo'
                   RETURNING judgment_id""",
                (search_id, ZERO_UUID, impression_id, ZERO_UUID),
            )
            tombstoned_ids = [row[0] for row in cur.fetchall()]
            if tombstoned_ids:
                cur.execute(
                    """UPDATE feedback.preference_pairs
                       SET tombstoned_at = now()
                       WHERE source_judgment_id = ANY(%s) AND tombstoned_at IS NULL""",
                    (tombstoned_ids,),
                )
            cur.execute(
                """INSERT INTO feedback.judgments
                   (search_id, impression_id, image_id, action, value, client_session_id, user_hash,
                    token_nonce, token_version, ranker_version_id, feature_version)
                   VALUES (%s, %s, %s, 'undo', 0, %s, %s, %s, 1, %s, %s)
                   RETURNING judgment_id""",
                (
                    search_id,
                    impression_id,
                    image_id,
                    client_session_id,
                    hashed_user,
                    token_nonce,
                    ranker_version_id,
                    feature_version,
                ),
            )
            judgment_id = str(cur.fetchone()[0])
            return {
                "status": "undone",
                "judgment_id": judgment_id,
                "search_id": search_id,
                "impression_id": impression_id,
                "pairs_created": 0,
                "tombstoned_judgments": len(tombstoned_ids),
            }

        value = 1.0 if action == "select" else -1.0 if action == "reject" else 0.0
        cur.execute(
            """INSERT INTO feedback.judgments
               (search_id, impression_id, image_id, action, value, client_session_id, user_hash,
                token_nonce, token_version, ranker_version_id, feature_version)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 1, %s, %s)
               RETURNING judgment_id""",
            (
                search_id,
                impression_id,
                image_id,
                action,
                value,
                client_session_id,
                hashed_user,
                token_nonce,
                ranker_version_id,
                feature_version,
            ),
        )
        judgment_id = str(cur.fetchone()[0])

        cur.execute(
            """INSERT INTO feedback.events (query_text, image_id, signal, value, user_token)
               VALUES (%s, %s, %s, %s, %s)""",
            (query_text, image_id, _legacy_signal(action), value, hashed_user),
        )

        pairs_created = 0
        if action == "select" and impression_id:
            pairs_created = _generate_pairs_for_selection(cur, judgment_id, search_id, str(impression_id))

    return {
        "status": "recorded",
        "judgment_id": judgment_id,
        "search_id": search_id,
        "impression_id": impression_id,
        "pairs_created": pairs_created,
    }

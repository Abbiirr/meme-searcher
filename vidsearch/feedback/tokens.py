from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from typing import Any

from vidsearch.config import FEEDBACK_SECRET, FEEDBACK_TOKEN_TTL_SECONDS, USER_HASH_SECRET


VALID_ACTIONS = {"select", "reject", "none_correct", "undo"}


class FeedbackTokenError(ValueError):
    """Raised when a feedback token cannot be trusted."""


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def user_hash(raw_user_id: str | None) -> str | None:
    if not raw_user_id:
        return None
    digest = hmac.new(USER_HASH_SECRET.encode("utf-8"), raw_user_id.encode("utf-8"), hashlib.sha256).hexdigest()
    return digest


def sign_feedback_token(
    *,
    search_id: str,
    action: str,
    impression_id: str | None = None,
    ranker_version_id: str = "baseline",
    feature_version: int = 1,
    ttl_seconds: int = FEEDBACK_TOKEN_TTL_SECONDS,
    nonce: str | None = None,
    now: float | None = None,
) -> str:
    if action not in VALID_ACTIONS:
        raise ValueError(f"invalid feedback action: {action}")

    issued_at = int(now if now is not None else time.time())
    payload: dict[str, Any] = {
        "v": 1,
        "search_id": str(search_id),
        "impression_id": str(impression_id) if impression_id else None,
        "action": action,
        "exp": issued_at + int(ttl_seconds),
        "nonce": nonce or uuid.uuid4().hex,
        "ranker_version_id": ranker_version_id,
        "feature_version": int(feature_version),
    }
    payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload_part = _b64encode(payload_bytes)
    signature = hmac.new(FEEDBACK_SECRET.encode("utf-8"), payload_part.encode("ascii"), hashlib.sha256).digest()
    return f"{payload_part}.{_b64encode(signature)}"


def verify_feedback_token(token: str, *, now: float | None = None) -> dict[str, Any]:
    try:
        payload_part, signature_part = token.split(".", 1)
    except ValueError as exc:
        raise FeedbackTokenError("malformed token") from exc

    expected = hmac.new(FEEDBACK_SECRET.encode("utf-8"), payload_part.encode("ascii"), hashlib.sha256).digest()
    try:
        observed = _b64decode(signature_part)
    except Exception as exc:
        raise FeedbackTokenError("malformed signature") from exc

    if not hmac.compare_digest(expected, observed):
        raise FeedbackTokenError("bad signature")

    try:
        payload = json.loads(_b64decode(payload_part).decode("utf-8"))
    except Exception as exc:
        raise FeedbackTokenError("malformed payload") from exc

    action = payload.get("action")
    if action not in VALID_ACTIONS:
        raise FeedbackTokenError("invalid action")

    try:
        exp = int(payload["exp"])
    except Exception as exc:
        raise FeedbackTokenError("missing expiry") from exc

    current = int(now if now is not None else time.time())
    if exp < current:
        raise FeedbackTokenError("expired token")

    if not payload.get("search_id"):
        raise FeedbackTokenError("missing search_id")

    return payload

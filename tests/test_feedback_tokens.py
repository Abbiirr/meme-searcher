from __future__ import annotations

import pytest

from vidsearch.feedback.tokens import FeedbackTokenError, sign_feedback_token, user_hash, verify_feedback_token


def test_feedback_token_roundtrip():
    token = sign_feedback_token(
        search_id="11111111-1111-1111-1111-111111111111",
        impression_id="22222222-2222-2222-2222-222222222222",
        action="select",
        ranker_version_id="baseline",
        feature_version=1,
        now=1000,
    )

    payload = verify_feedback_token(token, now=1001)

    assert payload["search_id"] == "11111111-1111-1111-1111-111111111111"
    assert payload["impression_id"] == "22222222-2222-2222-2222-222222222222"
    assert payload["action"] == "select"
    assert payload["ranker_version_id"] == "baseline"
    assert payload["feature_version"] == 1


def test_feedback_token_rejects_tampering():
    token = sign_feedback_token(
        search_id="11111111-1111-1111-1111-111111111111",
        action="none_correct",
        now=1000,
    )
    payload, signature = token.split(".", 1)
    tampered_payload = ("a" if payload[0] != "a" else "b") + payload[1:]
    tampered = f"{tampered_payload}.{signature}"

    with pytest.raises(FeedbackTokenError):
        verify_feedback_token(tampered, now=1001)


def test_feedback_token_rejects_expiry():
    token = sign_feedback_token(
        search_id="11111111-1111-1111-1111-111111111111",
        action="reject",
        ttl_seconds=10,
        now=1000,
    )

    with pytest.raises(FeedbackTokenError, match="expired"):
        verify_feedback_token(token, now=1011)


def test_user_hash_is_hmac_not_raw_user_id():
    hashed = user_hash("admin@localhost")

    assert hashed
    assert hashed != "admin@localhost"
    assert user_hash("admin@localhost") == hashed

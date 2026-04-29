from __future__ import annotations

from vidsearch.feedback.train_lambdamart import train_lambdamart_contract


def test_lambdamart_contract_writes_offline_artifact(monkeypatch, tmp_path):
    monkeypatch.setattr("vidsearch.feedback.train_lambdamart._load_pairs", lambda client_session_prefix=None: [])
    monkeypatch.setattr(
        "vidsearch.feedback.train_lambdamart._feedback_volume",
        lambda client_session_prefix=None: {"unique_query_judgments": 0, "preference_pairs": 0},
    )

    result = train_lambdamart_contract(client_session_prefix="rlaif-r2-train", output_path=tmp_path / "lambdamart.json")

    assert result["promotion_approved"] is False
    assert result["serving_enabled"] is False
    assert result["status"] == "blocked"


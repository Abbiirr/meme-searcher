from __future__ import annotations

from vidsearch.feedback.ai_judge import validate_judgment


def test_ai_judge_schema_requires_valid_found_candidate():
    row = {
        "record_type": "ai_target_judgment_v1",
        "prompt_id": "target-1:p1",
        "target_id": "target-1",
        "judge_model": "judge-a",
        "judge_role": "primary",
        "candidate_order_seed": 1,
        "verdict": "exact_target_found",
        "selected_candidate_blind_id": "C02",
        "confidence": 0.8,
        "evidence": {},
        "short_reason": "same target",
    }

    assert validate_judgment(row) == []


def test_ai_judge_schema_rejects_found_without_candidate():
    row = {
        "record_type": "ai_target_judgment_v1",
        "verdict": "exact_target_found",
        "confidence": 0.8,
    }

    assert "target-found verdict requires selected_candidate_blind_id" in validate_judgment(row)


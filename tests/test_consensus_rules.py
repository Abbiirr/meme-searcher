from __future__ import annotations

from vidsearch.feedback.consensus import consensus_label


def test_consensus_accepts_two_matching_found_judgments():
    label = consensus_label(
        [
            {"prompt_id": "p1", "target_id": "t1", "verdict": "exact_target_found", "selected_candidate_blind_id": "C02", "confidence": 0.9},
            {"prompt_id": "p1", "target_id": "t1", "verdict": "exact_target_found", "selected_candidate_blind_id": "C02", "confidence": 0.8},
        ]
    )

    assert label["label"] == "target_found"
    assert label["accepted_for_training"] is True


def test_consensus_marks_disagreement_uncertain():
    label = consensus_label(
        [
            {"prompt_id": "p1", "target_id": "t1", "verdict": "exact_target_found", "selected_candidate_blind_id": "C02", "confidence": 0.9},
            {"prompt_id": "p1", "target_id": "t1", "verdict": "not_found", "confidence": 0.9},
        ]
    )

    assert label["label"] == "uncertain"
    assert label["accepted_for_training"] is False


from __future__ import annotations

from vidsearch.feedback.consensus import consensus_label


def test_target_not_found_consensus_is_not_training_eligible():
    label = consensus_label(
        [
            {"prompt_id": "p1", "target_id": "t1", "verdict": "not_found", "confidence": 0.95},
            {"prompt_id": "p1", "target_id": "t1", "verdict": "not_found", "confidence": 0.9},
        ]
    )

    assert label["label"] == "target_not_found"
    assert label["accepted_for_training"] is False


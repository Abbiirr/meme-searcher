from __future__ import annotations

from vidsearch.feedback.ai_judge import randomized_candidates


def test_judge_randomization_hides_position_and_ids():
    hits = [
        {"image_id": "img_1", "rank": 1, "source_uri": "data/meme/a.jpg", "ocr_excerpt": "a"},
        {"image_id": "img_2", "rank": 2, "source_uri": "data/meme/b.jpg", "ocr_excerpt": "b"},
    ]

    candidates = randomized_candidates(hits, seed=7)

    assert {candidate["blind_id"] for candidate in candidates} == {"C01", "C02"}
    assert all("rank" not in candidate for candidate in candidates)
    assert all("image_id" not in candidate for candidate in candidates)
    assert all("source_uri" not in candidate for candidate in candidates)


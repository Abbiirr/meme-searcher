from __future__ import annotations

import json

from vidsearch.feedback.rank_bucket_report import build_rank_bucket_report, rank_bucket


def test_rank_bucket_uses_top100_bucket_names():
    assert rank_bucket({"status": "target_found", "rank": 55}) == "target_in_top_100_not_20"
    assert rank_bucket({"status": "target_not_found", "top_k": 100}) == "target_not_in_top_100"


def test_rank_bucket_report_enforces_stop_reasons(tmp_path):
    results = tmp_path / "results.jsonl"
    pack = tmp_path / "pack.jsonl"
    out = tmp_path / "buckets.json"
    results.write_text(
        json.dumps(
            {
                "record_type": "target_search_result_v1",
                "target_id": "target-1",
                "prompt_id": "target-1:p1",
                "prompt_category": "exact_text",
                "status": "target_found",
                "rank": 3,
                "top_k": 100,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    pack.write_text(json.dumps({"record_type": "target_image_task_v1", "target_id": "target-1"}) + "\n", encoding="utf-8")

    report = build_rank_bucket_report(results_path=results, pack_path=pack, output_path=out)

    assert report["eligibility"]["eligible_for_ranker_training"] is False
    assert report["eligibility"]["eligible_rank_2_to_10"] == 1


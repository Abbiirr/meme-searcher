from __future__ import annotations

import json

from vidsearch.feedback.r2_report import build_r2_report


def test_r2_report_writes_promotion_decision(tmp_path):
    prompt = tmp_path / "prompt.md"
    judge = tmp_path / "judge.md"
    bucket = tmp_path / "bucket.md"
    verify = tmp_path / "verify.json"
    output = tmp_path / "report.md"
    prompt.write_text("# Prompt\n", encoding="utf-8")
    judge.write_text("# Judge\n", encoding="utf-8")
    bucket.write_text("# Bucket\n", encoding="utf-8")
    verify.write_text(
        json.dumps(
            {
                "promotion_ready": False,
                "base_metrics": {"MRR": 0.9},
                "learned_metrics": {"MRR": 0.8},
                "deltas": {"MRR": -0.1},
                "promotion_gates": {"mrr_no_regression": False},
            }
        ),
        encoding="utf-8",
    )

    result = build_r2_report(prompt_summary=prompt, judge_summary=judge, bucket_summary=bucket, post_verify=verify, output_path=output)

    assert result["promotion_ready"] is False
    assert "Promotion ready: `False`" in output.read_text(encoding="utf-8")


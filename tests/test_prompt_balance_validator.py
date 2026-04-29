from __future__ import annotations

import json

from vidsearch.feedback.prompt_balance import _next_prompt_id, normalize_category, validate_prompt_balance


def test_prompt_balance_normalizes_legacy_categories_and_detects_leaks(tmp_path):
    prompts = tmp_path / "prompts.jsonl"
    prompts.write_text(
        "\n".join(
            [
                json.dumps({"record_type": "target_prompt_label_v1", "prompt_id": "p1", "prompt": "find exact quote", "category": "exact_memory"}),
                json.dumps({"record_type": "target_prompt_label_v1", "prompt_id": "p2", "prompt": "find C:\\data\\secret.jpg", "category": "topic"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = validate_prompt_balance(
        prompts_path=prompts,
        output_path=tmp_path / "summary.md",
        minimums={"exact_text": 1, "semantic_description": 1},
    )

    assert normalize_category("paraphrase") == "fuzzy_text"
    assert result["counts_by_category"]["exact_text"] == 1
    assert result["status"] == "failed"
    assert "answer-leaking prompts detected: 1" in result["failures"]


def test_prompt_balance_next_prompt_id_appends_after_existing_target_rows():
    rows = [
        {"target_id": "target-1", "prompt_id": "target-1:p1"},
        {"target_id": "target-1", "prompt_id": "target-1:p8"},
        {"target_id": "target-2", "prompt_id": "target-2:p9"},
    ]

    assert _next_prompt_id(rows, "target-1") == "target-1:p9"

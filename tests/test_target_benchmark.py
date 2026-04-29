from __future__ import annotations

import json

import pytest

from vidsearch.feedback.analyze_target_misses import analyze_target_misses, classify_miss
from vidsearch.feedback.rank_bucket_report import build_rank_bucket_report, rank_bucket
from vidsearch.feedback.target_benchmark import (
    RESULT_RECORD,
    MISSING_RECORD,
    PROMPT_RECORD,
    TARGET_RECORD,
    _metadata_prompt_text,
    _model_family,
    _model_fingerprint,
    _parse_batch_prompt_response,
    _session_id,
    _training_target_image_ids,
    _unique_target_rows,
    validate_prompt_rows,
    write_target_prompt,
)


def test_validate_prompt_rows_accepts_known_targets():
    pack_rows = [{"record_type": TARGET_RECORD, "target_id": "target-1"}]
    prompt_rows = [
        {
            "record_type": PROMPT_RECORD,
            "target_id": "target-1",
            "prompt_id": "target-1:p1",
            "prompt": "find me a meme on not having friends",
        }
    ]

    prompts = validate_prompt_rows(pack_rows=pack_rows, prompt_rows=prompt_rows)

    assert len(prompts) == 1
    assert prompts[0]["prompt"] == "find me a meme on not having friends"


def test_validate_prompt_rows_rejects_unknown_target():
    pack_rows = [{"record_type": TARGET_RECORD, "target_id": "target-1"}]
    prompt_rows = [
        {
            "record_type": PROMPT_RECORD,
            "target_id": "missing",
            "prompt_id": "missing:p1",
            "prompt": "find this meme",
        }
    ]

    with pytest.raises(ValueError, match="unknown target_id"):
        validate_prompt_rows(pack_rows=pack_rows, prompt_rows=prompt_rows)


def test_write_target_prompt_names_output_schema(tmp_path):
    pack = tmp_path / "target_pack.jsonl"
    prompt = tmp_path / "agent_prompt.md"
    labels = tmp_path / "target_prompts.jsonl"
    pack.write_text(json.dumps({"record_type": TARGET_RECORD, "target_id": "target-1"}) + "\n", encoding="utf-8")

    result = write_target_prompt(pack_path=pack, output_path=prompt, labels_output=labels, prompts_per_image=5)
    text = prompt.read_text(encoding="utf-8")

    assert result["labels_output"] == str(labels)
    assert PROMPT_RECORD in text
    assert "Do not put the filename" in text
    assert str(labels) in text


def test_target_benchmark_session_ids_are_stable_and_bounded():
    prompt_id = "target-1:" + ("very long label " * 20)

    first = _session_id("rlhf-target", prompt_id)
    second = _session_id("rlhf-target", prompt_id)

    assert first == second
    assert first.startswith("rlhf-target-target-1")
    assert len(first) <= 128


def test_metadata_prompt_text_uses_public_metadata_only():
    target = {
        "target_id": "target-secret",
        "target_path": r"K:\projects\video_searcher\data\meme_rlhf\secret.jpg",
        "metadata_for_reviewer": {
            "caption_literal": "Heath Ledger interview meme about friends.",
            "ocr_excerpt": "I don't have a lot of friends. I just know a lot of people.",
            "tags": ["friends", "interview"],
        },
    }

    text = _metadata_prompt_text(target, prompts_per_image=1)

    assert "Heath Ledger interview meme" in text
    assert "I don't have a lot of friends" in text
    assert "target-secret" not in text
    assert "secret.jpg" not in text


def test_model_fingerprint_records_family_and_mode():
    fingerprint = _model_fingerprint(
        provider="litellm",
        model="qwen3.6-vlm-wrapper",
        endpoint="http://127.0.0.1:4100",
        mode="image",
    )

    assert fingerprint["family"] == "qwen"
    assert fingerprint["provider"] == "litellm"
    assert fingerprint["mode"] == "image"
    assert _model_family("fast") == "gateway-default"
    assert _model_family("fast-vl") == "gateway-default"


def test_parse_batch_prompt_response_maps_prompts_by_target():
    text = json.dumps(
        {
            "items": [
                {
                    "target_id": "target-1",
                    "prompts": [{"prompt": "find me a meme on tired winter cat", "category": "topic"}],
                },
                {
                    "target_id": "target-2",
                    "prompts": [{"prompt": "find me meme on not having friends", "category": "exact_memory"}],
                },
            ]
        }
    )

    parsed = _parse_batch_prompt_response(text)

    assert parsed["target-1"][0]["prompt"] == "find me a meme on tired winter cat"
    assert parsed["target-2"][0]["category"] == "exact_memory"


def test_unique_target_rows_dedupes_sha_targets():
    rows = [
        {"record_type": TARGET_RECORD, "target_id": "target-1", "target_path": "a.jpg"},
        {"record_type": TARGET_RECORD, "target_id": "target-1", "target_path": "copy.jpg"},
        {"record_type": TARGET_RECORD, "target_id": "target-2", "target_path": "b.jpg"},
    ]

    unique = _unique_target_rows(rows)

    assert [row["target_path"] for row in unique] == ["a.jpg", "b.jpg"]


def test_training_target_image_ids_reads_pack(tmp_path):
    pack = tmp_path / "target_pack.jsonl"
    pack.write_text(
        json.dumps({"record_type": TARGET_RECORD, "target_image_id": "img_1"})
        + "\n"
        + json.dumps({"record_type": TARGET_RECORD, "target_image_id": "img_2"})
        + "\n",
        encoding="utf-8",
    )

    assert _training_target_image_ids(pack) == {"img_1", "img_2"}


def test_classify_miss_flags_bangla_target_with_english_prompt():
    miss = {
        "failure_type": "target_not_in_slate",
        "prompt": "bengali text meme ignoring senior",
        "top_source_uris": ["data/meme/other.jpg"],
    }
    target = {
        "target_path": "data/meme_rlhf/target.jpg",
        "metadata_for_reviewer": {"ocr_excerpt": "রাস্তায় সিনিয়র ভাইকে সালাম না দিয়ে চলে যাই"},
    }

    failure_class, recommended_fix, details = classify_miss(miss, target)

    assert failure_class == "bangla_metadata_under_prompted"
    assert "Bangla-script prompt" in recommended_fix
    assert details["target_has_bangla"] is True


def test_analyze_target_misses_writes_summary(tmp_path):
    misses = tmp_path / "misses.jsonl"
    pack = tmp_path / "pack.jsonl"
    output = tmp_path / "analysis.json"
    misses.write_text(
        json.dumps(
            {
                "record_type": MISSING_RECORD,
                "target_id": "target-1",
                "target_image_id": "img_1",
                "target_path": "target.jpg",
                "prompt_id": "target-1:p1",
                "prompt": "find exact joke",
                "failure_type": "target_not_in_slate",
                "top_source_uris": ["other.jpg"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    pack.write_text(
        json.dumps(
            {
                "record_type": TARGET_RECORD,
                "target_id": "target-1",
                "target_path": "target.jpg",
                "metadata_for_reviewer": {"caption_literal": "A different exact visible joke about exams"},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    report = analyze_target_misses(misses_path=misses, pack_path=pack, output_path=output)

    assert report["total_misses"] == 1
    assert output.exists()
    assert report["items"][0]["record_type"] == "target_miss_analysis_v1"


def test_rank_bucket_classifies_target_pickup_cases():
    assert rank_bucket({"status": "found_selected", "rank": 1}) == "target_at_rank_1"
    assert rank_bucket({"status": "found_selected", "rank": 7}) == "target_in_top_10_not_1"
    assert rank_bucket({"status": "found_selected", "rank": 17}) == "target_in_top_20_not_10"
    assert rank_bucket({"status": "target_not_found", "top_k": 20}) == "target_not_in_top_20"


def test_build_rank_bucket_report_groups_by_language_and_category(tmp_path):
    results = tmp_path / "results.jsonl"
    pack = tmp_path / "pack.jsonl"
    output = tmp_path / "rank_buckets.json"
    results.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "record_type": RESULT_RECORD,
                        "target_id": "target-1",
                        "target_image_id": "img_1",
                        "prompt_id": "target-1:p1",
                        "prompt": "find this exact text",
                        "prompt_category": "exact_memory",
                        "status": "found_selected",
                        "rank": 3,
                        "top_k": 20,
                    }
                ),
                json.dumps(
                    {
                        "record_type": RESULT_RECORD,
                        "target_id": "target-2",
                        "target_image_id": "img_2",
                        "prompt_id": "target-2:p1",
                        "prompt": "বাংলা মিম",
                        "prompt_category": "multilingual",
                        "status": "target_not_found",
                        "top_k": 20,
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    pack.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "record_type": TARGET_RECORD,
                        "target_id": "target-1",
                        "metadata_for_reviewer": {"ocr_excerpt": "english text"},
                    }
                ),
                json.dumps(
                    {
                        "record_type": TARGET_RECORD,
                        "target_id": "target-2",
                        "metadata_for_reviewer": {"ocr_excerpt": "বাংলা লেখা"},
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    report = build_rank_bucket_report(results_path=results, pack_path=pack, output_path=output)

    assert report["bucket_counts"]["target_in_top_10_not_1"] == 1
    assert report["bucket_counts"]["target_not_in_top_20"] == 1
    assert report["bucket_counts_by_language"]["bangla"]["target_not_in_top_20"] == 1
    assert output.exists()

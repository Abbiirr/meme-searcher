from __future__ import annotations

import json
import random

import pytest

from vidsearch.feedback.agent_operator import (
    DECISION_RECORD,
    TASK_RECORD,
    _load_task_and_decision_maps,
    choose_decision,
    write_heuristic_decisions,
)
from vidsearch.feedback.exporters import export_feedback_datasets
from vidsearch.feedback.ranker import FEATURE_KEYS, feature_vector, maybe_apply_exploration
from vidsearch.feedback.post_rlhf_verify import (
    _grade_vector,
    _load_training_target_ids,
    _metric_block,
    _offline_learned_order,
    _overlap_gates,
)
from vidsearch.feedback.service import VALID_JUDGMENT_ACTIONS, feature_snapshot
from vidsearch.feedback.train_ranker import (
    SELECTED_MRR_MIN,
    SELECTED_MRR_PRESERVE_RATIO,
    _client_session_clause,
    _diff_matrix,
    _pair_weight,
    _per_intent_capability,
    _split_key,
    _training_composition,
)


def test_feature_snapshot_uses_versioned_wrapper():
    hit = {
        "rank": 2,
        "image_id": "img_1",
        "source_uri": "data/meme/jobs/example.jpg",
        "caption_literal": "A person applying for jobs.",
        "caption_figurative": "A meme about job applications.",
        "template_name": "unknown",
        "tags": ["jobs", "work"],
        "ocr_excerpt": "applied to 100 jobs",
        "retrieval_score": 0.5,
        "rerank_score": 0.8,
    }

    snapshot = feature_snapshot("find a meme on jobs", "semantic_description", hit, 5)

    assert snapshot["feature_version"] == 1
    assert snapshot["features"]["per_impression"]["rank"] == 2
    assert snapshot["features"]["per_impression"]["has_ocr"] is True
    assert snapshot["features"]["per_impression"]["has_caption"] is True
    assert snapshot["features"]["list_level"]["slate_size"] == 5


def test_feature_vector_matches_feature_keys():
    snapshot = {
        "feature_version": 1,
        "features": {
            "per_impression": {
                "rank": 1,
                "base_rank": 1,
                "retrieval_score": 0.7,
                "rerank_score": 0.9,
                "has_ocr": True,
                "has_caption": False,
                "tag_count": 3,
                "text_overlap": 0.5,
                "source_path_depth": 4,
            },
            "list_level": {
                "slate_size": 5,
                "position_fraction": 0.0,
                "duplicate_pressure": 0.0,
                "near_duplicate_count": 0,
            },
        },
    }

    vector = feature_vector(snapshot)

    assert len(vector) == len(FEATURE_KEYS)
    assert vector[FEATURE_KEYS.index("bias")] == 1.0
    assert vector[FEATURE_KEYS.index("rank")] == 1.0
    assert vector[FEATURE_KEYS.index("has_ocr")] == 1.0
    assert vector[FEATURE_KEYS.index("has_caption")] == 0.0


def test_feature_vector_zero_fills_missing_rerank_score():
    snapshot = {
        "feature_version": 1,
        "features": {
            "per_impression": {
                "rank": 1,
                "base_rank": 1,
                "retrieval_score": 0.7,
                "rerank_score": None,
            },
            "list_level": {},
        },
    }

    vector = feature_vector(snapshot)

    assert vector[FEATURE_KEYS.index("rerank_score")] == 0.0


def test_rank_only_pairwise_baseline_has_no_trivial_intercept():
    winner = {
        "feature_version": 1,
        "features": {
            "per_impression": {"rank": 5},
            "list_level": {},
        },
    }
    loser = {
        "feature_version": 1,
        "features": {
            "per_impression": {"rank": 1},
            "list_level": {},
        },
    }

    matrix = _diff_matrix([{"winner_features": winner, "loser_features": loser}], rank_only=True)

    assert matrix.shape == (1, 1)
    assert matrix[0][0] == 4.0


def test_pair_weight_calibrates_rank_one_examples():
    pair = {
        "pair_weight": 1.0,
        "winner_features": {
            "features": {
                "per_impression": {"rank": 1},
                "list_level": {},
            }
        },
    }

    assert _pair_weight(pair, rank1_weight=0.25) == 0.25


def test_training_composition_reports_rank_buckets_and_rerank_missing():
    pairs = [
        {
            "pair_weight": 1.0,
            "winner_features": {"features": {"per_impression": {"rank": 1, "rerank_score": None}}},
            "loser_features": {"features": {"per_impression": {"rank": 2, "rerank_score": None}}},
        },
        {
            "pair_weight": 1.0,
            "winner_features": {"features": {"per_impression": {"rank": 5, "rerank_score": 0.3}}},
            "loser_features": {"features": {"per_impression": {"rank": 6, "rerank_score": None}}},
        },
    ]

    composition = _training_composition(pairs, rank1_weight=0.5)

    assert composition["pair_counts_by_winner_rank_bucket"]["target_at_rank_1"] == 1
    assert composition["pair_counts_by_winner_rank_bucket"]["target_in_top_10_not_1"] == 1
    assert composition["effective_rank1_pair_share"] == 1 / 3
    assert composition["rerank_score_missing_values"] == 3


def test_client_session_clause_filters_by_prefix():
    clause, params = _client_session_clause("rlhf-target-full")

    assert "client_session_id LIKE" in clause
    assert params == ("rlhf-target-full%",)


def test_ranker_split_key_prefers_target_identity_from_session_id():
    key = _split_key("search-1", "rlaif-r2-search-target-deadbeef:p03-abcdef", None)

    assert key == "target-deadbeef"


def test_per_intent_capability_marks_partial_coverage_as_diagnostic_only():
    volume = {"judgments_per_intent": {"exact_text": 29, "semantic_description": 160}}

    capability = _per_intent_capability(volume)

    assert capability["exact_text"]["diagnostic_volume_ok"] is True
    assert capability["exact_text"]["promotion_volume_ok"] is False
    assert capability["exact_text"]["claim"] == "diagnostic-only"
    assert capability["semantic_description"]["claim"] == "promotion-eligible"


def test_selected_mrr_preserve_formula_is_relative_not_additive():
    base_mrr = 0.93
    threshold = max(SELECTED_MRR_MIN, base_mrr * SELECTED_MRR_PRESERVE_RATIO)

    assert threshold == pytest.approx(0.9207)
    assert threshold < base_mrr + 0.10


def test_target_not_found_is_not_a_judgment_action():
    assert "target_not_found" not in VALID_JUDGMENT_ACTIONS


def test_post_rlhf_grade_vector_counts_missing_positive():
    hits = [{"image_id": "img_a"}]
    grades = _grade_vector(hits, {"img_a": 3, "img_missing": 2})

    assert grades == [3, 0]


def test_post_rlhf_loads_training_target_ids(tmp_path):
    pack = tmp_path / "target_pack.jsonl"
    pack.write_text(
        json.dumps({"target_image_id": "img_train_1"}) + "\n" + json.dumps({"target_image_id": "img_train_2"}) + "\n",
        encoding="utf-8",
    )

    assert _load_training_target_ids(pack) == {"img_train_1", "img_train_2"}


def test_post_rlhf_overlap_gates_require_non_overlap_rows():
    empty_block = _metric_block([])

    assert _overlap_gates(empty_block)["without_overlap_verification_available"] is False


def test_post_rlhf_metric_block_reports_exact_text_misses():
    block = _metric_block(
        [
            {
                "query_id": "q1",
                "intent": "exact_text",
                "base_grades": [0, 1],
                "learned_grades": [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1],
            }
        ]
    )

    assert block["query_count"] == 1
    assert block["exact_text_misses_outside_top10"] == ["q1"]


def test_post_rlhf_offline_order_caps_upward_movement(monkeypatch):
    hits = [
        {
            "rank": index,
            "base_rank": index,
            "image_id": f"img_{index}",
            "source_uri": f"data/meme/{index}.jpg",
            "retrieval_score": 1.0 / index,
            "rerank_score": float(index),
        }
        for index in range(1, 9)
    ]
    # A high positive rerank weight wants rank 8 first, but the conservative
    # movement cap prevents it from jumping above position 3.
    weights = [0.0] * len(FEATURE_KEYS)
    weights[FEATURE_KEYS.index("rerank_score")] = 10.0
    monkeypatch.setattr("vidsearch.feedback.post_rlhf_verify.FEEDBACK_MAX_UPWARD_MOVEMENT", 5)

    learned = _offline_learned_order(query="anything", intent="semantic_description", hits=hits, weights=weights)

    assert learned[0]["image_id"] != "img_8"
    assert learned[2]["image_id"] == "img_8"


def test_agent_operator_decision_prefers_rich_non_top_candidate():
    task = {
        "task_id": "task-1",
        "query": "orange food tray",
        "candidates": [
            {
                "candidate_id": "rank-1",
                "rank": 1,
                "image_id": "img_top",
                "source_uri": "data/meme/top.jpg",
                "ocr_excerpt": "",
                "tags": [],
            },
            {
                "candidate_id": "rank-4",
                "rank": 4,
                "image_id": "img_rich",
                "source_uri": "data/meme/Old Memes/food/orange.jpg",
                "ocr_excerpt": "orange tray",
                "tags": ["food", "orange"],
            },
        ],
    }

    decision = choose_decision(task, operator="codex-test")

    assert decision["record_type"] == DECISION_RECORD
    assert decision["action"] == "select"
    assert decision["selected_candidate_id"] == "rank-4"
    assert decision["selected_image_id"] == "img_rich"


def test_agent_operator_writes_and_validates_decision_jsonl(tmp_path):
    pack = tmp_path / "pack.jsonl"
    decisions = tmp_path / "decisions.jsonl"
    task = {
        "record_type": TASK_RECORD,
        "task_id": "query-1",
        "query": "job meme",
        "candidates": [
            {
                "candidate_id": "query-1:rank-1",
                "rank": 1,
                "image_id": "img_1",
                "source_uri": "data/meme/jobs.jpg",
                "ocr_excerpt": "jobs",
                "tags": ["jobs"],
            }
        ],
    }
    pack.write_text(json.dumps(task) + "\n", encoding="utf-8")

    result = write_heuristic_decisions(pack_path=pack, output_path=decisions, operator="codex-test")
    tasks, decision_map = _load_task_and_decision_maps(pack, decisions)

    assert result["decisions"] == 1
    assert list(tasks) == ["query-1"]
    assert decision_map["query-1"]["selected_candidate_id"] == "query-1:rank-1"


def test_exploration_default_off_preserves_order(monkeypatch):
    hits = [{"rank": index, "image_id": f"img_{index}"} for index in range(1, 10)]
    monkeypatch.setattr("vidsearch.feedback.ranker.FEEDBACK_EXPLORATION_RATE", 0.0)

    assert maybe_apply_exploration(hits, rng=random.Random(7)) == hits


def test_exploration_swaps_only_ranks_four_to_eight(monkeypatch):
    hits = [{"rank": index, "image_id": f"img_{index}"} for index in range(1, 10)]
    monkeypatch.setattr("vidsearch.feedback.ranker.FEEDBACK_EXPLORATION_RATE", 1.0)

    explored = maybe_apply_exploration(hits, rng=random.Random(7))

    assert [hit["image_id"] for hit in explored[:3]] == ["img_1", "img_2", "img_3"]
    assert [hit["rank"] for hit in explored] == list(range(1, 10))
    changed = [hit for hit in explored if hit.get("is_exploration")]
    assert len(changed) == 2
    assert all(hit["exploration_policy"] == "swap_4_8_v1" for hit in changed)


def test_exporters_write_all_research_formats(tmp_path):
    snapshot = tmp_path / "snapshot.jsonl"
    row = {
        "record_type": "preference_pair",
        "pair_id": "pair-1",
        "search_id": "search-1",
        "query_redacted": "find a job meme",
        "intent": "semantic_description",
        "feature_version": 1,
        "chosen": {"image_id": "img_good", "rank": 2, "base_rank": 2, "features": {"feature_version": 1}},
        "rejected": {"image_id": "img_bad", "rank": 1, "base_rank": 1, "features": {"feature_version": 1}},
    }
    none_correct = {
        "record_type": "none_correct",
        "judgment_id": "judgment-1",
        "search_id": "search-2",
        "query_redacted": "find a missing meme",
        "intent": "semantic_description",
        "feature_version": 1,
        "candidates": [
            {"image_id": "img_wrong", "rank": 1, "base_rank": 1, "features": {"feature_version": 1}},
        ],
    }
    snapshot.write_text(json.dumps(row) + "\n" + json.dumps(none_correct) + "\n", encoding="utf-8")

    result = export_feedback_datasets(snapshot_path=snapshot, output_dir=tmp_path / "exports")

    assert result["exports"]["ltr"]["rows"] == 2
    assert result["exports"]["dpo"]["rows"] == 1
    assert result["exports"]["orpo"]["rows"] == 1
    assert result["exports"]["kto"]["rows"] == 3
    assert result["exports"]["reward_pairs"]["rows"] == 1

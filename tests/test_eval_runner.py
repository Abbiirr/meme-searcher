"""Tests for the YAML-side qrels loader in vidsearch.eval.runner.

The DB-side path needs live Postgres and lives under integration tests.
"""

from __future__ import annotations

import pytest

from vidsearch.eval import runner


def test_qrels_from_yaml_target_image_id_singleton():
    q = {"target_image_id": "img_a", "qrels": []}
    assert runner._qrels_from_yaml(q) == {"img_a": 3}


def test_qrels_from_yaml_target_image_id_list():
    q = {"target_image_id": ["img_a", "img_b"], "qrels": []}
    assert runner._qrels_from_yaml(q) == {"img_a": 3, "img_b": 3}


def test_qrels_from_yaml_explicit_grades_override_target():
    q = {
        "target_image_id": "img_a",
        "qrels": [
            {"image_id": "img_a", "grade": 2},
            {"image_id": "img_b", "grade": 1},
        ],
    }
    out = runner._qrels_from_yaml(q)
    # qrels entries overwrite the target-derived grade
    assert out == {"img_a": 2, "img_b": 1}


def test_qrels_from_yaml_skips_malformed_rows():
    q = {
        "qrels": [
            {"image_id": "img_a", "grade": 3},
            {"image_id": "img_b"},         # no grade
            {"grade": 1},                  # no image_id
            "not a dict",                  # not a dict
            {"image_id": 42, "grade": 1},  # wrong type
        ],
    }
    assert runner._qrels_from_yaml(q) == {"img_a": 3}


def test_qrels_from_yaml_empty():
    assert runner._qrels_from_yaml({}) == {}
    assert runner._qrels_from_yaml({"target_image_id": None, "qrels": None}) == {}


def test_qrels_from_yaml_grade_zero_is_recorded():
    # grade 0 means "explicitly judged as not relevant" — distinct from absent.
    q = {"qrels": [{"image_id": "img_a", "grade": 0}]}
    assert runner._qrels_from_yaml(q) == {"img_a": 0}


def test_eval_yaml_has_10_per_intent():
    """Guard the retrieval plan §7.1 balance requirement at commit time."""
    import yaml
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "vidsearch" / "eval" / "queries_memes.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    queries = data.get("queries", [])

    from collections import Counter
    counts = Counter(q["intent"] for q in queries)

    assert sum(counts.values()) == 40, f"expected 40 queries, got {sum(counts.values())}"
    assert counts["exact_text"] == 10
    assert counts["fuzzy_text"] == 10
    assert counts["semantic_description"] == 10
    assert counts["mixed_visual_description"] == 10

    # Every query must have a stable query_id so run history survives re-runs.
    ids = [q.get("query_id") for q in queries]
    assert len(ids) == len(set(ids)), "duplicate query_id in queries_memes.yaml"
    assert all(ids), "every query must have a query_id"

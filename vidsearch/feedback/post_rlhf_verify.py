from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np

from vidsearch.config import FEEDBACK_MAX_UPWARD_MOVEMENT, FEEDBACK_RANKER_ALPHA
from vidsearch.eval.metrics import compute_all_metrics
from vidsearch.eval.runner import _qrels_from_db, _qrels_from_yaml, load_queries
from vidsearch.feedback.ranker import FEATURE_KEYS, feature_vector
from vidsearch.feedback.service import FEATURE_VERSION, feature_snapshot
from vidsearch.query.retrieve_images import retrieve_images
from vidsearch.storage.pg import get_cursor

DEFAULT_TRAINING_TARGET_PACK = Path("artifacts/feedback_targets/target_pack.jsonl")


def _load_artifact(path: Path) -> dict[str, Any]:
    artifact = json.loads(path.read_text(encoding="utf-8"))
    if int(artifact.get("feature_version", -1)) != FEATURE_VERSION:
        raise ValueError(f"artifact feature_version does not match runtime feature version {FEATURE_VERSION}")
    if len(artifact.get("weights") or []) != len(FEATURE_KEYS):
        raise ValueError("artifact weight length does not match feature keys")
    return artifact


def _load_training_target_ids(path: Path | None) -> set[str]:
    if path is None or not path.exists():
        return set()
    target_ids: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        image_id = row.get("target_image_id")
        if image_id:
            target_ids.add(str(image_id))
    return target_ids


def _grade_vector(hits: list[dict[str, Any]], grade_map: dict[str, int]) -> list[int]:
    grades = [int(grade_map.get(str(hit["image_id"]), 0)) for hit in hits]
    judged_positives = sum(1 for grade in grade_map.values() if grade >= 1)
    retrieved_positives = sum(1 for grade in grades if grade >= 1)
    grades.extend([0] * max(0, judged_positives - retrieved_positives))
    return grades


def _offline_learned_order(
    *,
    query: str,
    intent: str,
    hits: list[dict[str, Any]],
    weights: np.ndarray,
) -> list[dict[str, Any]]:
    scored: list[dict[str, Any]] = []
    for hit in hits:
        snapshot = feature_snapshot(query, intent, hit, len(hits))
        learned_score = float(np.dot(weights, np.asarray(feature_vector(snapshot), dtype=np.float64)))
        combined_score = float(hit.get("rerank_score") or 0.0) + FEEDBACK_RANKER_ALPHA * learned_score
        scored.append({**hit, "learned_score": learned_score, "combined_score": combined_score})

    desired = sorted(
        scored,
        key=lambda hit: (
            float(hit.get("combined_score") or 0.0),
            -int(hit.get("rank") or 0),
        ),
        reverse=True,
    )
    return _cap_upward_movement(scored, desired)


def _cap_upward_movement(original: list[dict[str, Any]], desired: list[dict[str, Any]]) -> list[dict[str, Any]]:
    original_positions = {hit["image_id"]: index for index, hit in enumerate(original)}
    final: list[dict[str, Any] | None] = [None] * len(original)

    for desired_hit in desired:
        original_index = original_positions[desired_hit["image_id"]]
        earliest = max(0, original_index - FEEDBACK_MAX_UPWARD_MOVEMENT)
        placed = False
        for index in range(earliest, len(final)):
            if final[index] is None:
                final[index] = desired_hit
                placed = True
                break
        if not placed:
            for index in range(len(final)):
                if final[index] is None:
                    final[index] = desired_hit
                    break

    reranked = [hit for hit in final if hit is not None]
    for index, hit in enumerate(reranked, start=1):
        hit["rank"] = index
    return reranked


def _load_grade_map(query: dict[str, Any], *, use_db_qrels: bool) -> dict[str, int]:
    grade_map = _qrels_from_yaml(query)
    query_id = query.get("query_id")
    if use_db_qrels and query_id:
        try:
            with get_cursor() as cur:
                for image_id, grade in _qrels_from_db(cur, query_id).items():
                    grade_map[image_id] = max(grade_map.get(image_id, 0), grade)
        except Exception:
            pass
    return grade_map


def _exact_text_misses_outside_top10(rows: list[dict[str, Any]], metric_name: str) -> list[str]:
    misses: list[str] = []
    for row in rows:
        if row["intent"] != "exact_text":
            continue
        grades = row[metric_name]
        if not any(grade >= 1 for grade in grades[:10]):
            misses.append(str(row["query_id"]))
    return misses


def _metric_block(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "query_count": 0,
            "base_metrics": {},
            "learned_metrics": {},
            "deltas": {},
            "exact_text_misses_outside_top10": [],
        }
    base_metrics = compute_all_metrics([{"grades": row["base_grades"]} for row in rows])
    learned_metrics = compute_all_metrics([{"grades": row["learned_grades"]} for row in rows])
    deltas = {key: learned_metrics.get(key, 0.0) - base_metrics.get(key, 0.0) for key in learned_metrics}
    return {
        "query_count": len(rows),
        "base_metrics": base_metrics,
        "learned_metrics": learned_metrics,
        "deltas": deltas,
        "exact_text_misses_outside_top10": _exact_text_misses_outside_top10(rows, "learned_grades"),
    }


def _overlap_gates(block: dict[str, Any]) -> dict[str, bool]:
    if int(block.get("query_count") or 0) == 0:
        return {
            "without_overlap_verification_available": False,
            "without_overlap_recall_at_10_floor": False,
            "without_overlap_top_1_hit_rate_floor": False,
            "without_overlap_no_recall_at_10_regression": False,
            "without_overlap_no_top_1_hit_rate_regression": False,
            "without_overlap_no_exact_text_misses_outside_top10": False,
        }
    learned = block["learned_metrics"]
    deltas = block["deltas"]
    return {
        "without_overlap_verification_available": True,
        "without_overlap_recall_at_10_floor": learned.get("Recall@10", 0.0) >= 0.90,
        "without_overlap_top_1_hit_rate_floor": learned.get("top_1_hit_rate", 0.0) >= 0.70,
        "without_overlap_no_recall_at_10_regression": deltas.get("Recall@10", 0.0) >= -0.02,
        "without_overlap_no_top_1_hit_rate_regression": deltas.get("top_1_hit_rate", 0.0) >= -0.02,
        "without_overlap_no_exact_text_misses_outside_top10": not block["exact_text_misses_outside_top10"],
    }


def verify_post_rlhf(
    *,
    artifact_path: Path,
    queries_path: Path,
    output_path: Path,
    limit: int = 50,
    use_db_qrels: bool = True,
    training_target_pack: Path | None = DEFAULT_TRAINING_TARGET_PACK,
) -> dict[str, Any]:
    artifact = _load_artifact(artifact_path)
    weights = np.asarray([float(value) for value in artifact["weights"]], dtype=np.float64)
    queries = load_queries(str(queries_path))
    training_target_ids = _load_training_target_ids(training_target_pack)

    rows: list[dict[str, Any]] = []
    for query in queries:
        query_text = str(query["text"])
        raw = retrieve_images(query_text, limit=limit)
        hits = list(raw.get("hits") or [])
        intent = str(raw.get("intent") or query.get("intent") or "semantic_description")
        grade_map = _load_grade_map(query, use_db_qrels=use_db_qrels)
        positive_image_ids = sorted(str(image_id) for image_id, grade in grade_map.items() if int(grade) >= 1)
        overlap_image_ids = sorted(set(positive_image_ids) & training_target_ids)
        learned_hits = _offline_learned_order(query=query_text, intent=intent, hits=hits, weights=weights)
        rows.append(
            {
                "query_id": query.get("query_id"),
                "query": query_text,
                "intent": str(query.get("intent") or intent),
                "retrieved": len(hits),
                "base_grades": _grade_vector(hits, grade_map),
                "learned_grades": _grade_vector(learned_hits, grade_map),
                "base_top_image_ids": [hit["image_id"] for hit in hits[:10]],
                "learned_top_image_ids": [hit["image_id"] for hit in learned_hits[:10]],
                "qrels_positive_image_ids": positive_image_ids,
                "qrels_training_overlap_image_ids": overlap_image_ids,
                "qrels_overlaps_training_targets": bool(overlap_image_ids),
            }
        )

    all_block = _metric_block(rows)
    rows_with_overlap = [row for row in rows if row["qrels_overlaps_training_targets"]]
    rows_without_overlap = [row for row in rows if not row["qrels_overlaps_training_targets"]]
    with_overlap_block = _metric_block(rows_with_overlap)
    without_overlap_block = _metric_block(rows_without_overlap)
    base_metrics = all_block["base_metrics"]
    learned_metrics = all_block["learned_metrics"]
    deltas = all_block["deltas"]
    exact_misses = all_block["exact_text_misses_outside_top10"]
    gates = {
        "recall_at_10_floor": learned_metrics.get("Recall@10", 0.0) >= 0.90,
        "top_1_hit_rate_floor": learned_metrics.get("top_1_hit_rate", 0.0) >= 0.70,
        "no_exact_text_misses_outside_top10": not exact_misses,
        "no_recall_at_10_regression": deltas.get("Recall@10", 0.0) >= -0.02,
        "no_top_1_hit_rate_regression": deltas.get("top_1_hit_rate", 0.0) >= -0.02,
        "positive_mrr_lift": deltas.get("MRR", 0.0) > 0.0,
    }
    overlap_required_gates = _overlap_gates(without_overlap_block) if training_target_ids else {}
    promotion_gates = gates | overlap_required_gates
    report = {
        "status": "verified",
        "artifact": str(artifact_path),
        "ranker_version_id": artifact.get("ranker_version_id"),
        "queries_path": str(queries_path),
        "training_target_pack": str(training_target_pack) if training_target_pack else None,
        "candidate_pool": "data/meme via live Qdrant corpus",
        "training_set": "data/meme_rlhf feedback sessions",
        "query_count": len(rows),
        "limit": limit,
        "base_metrics": base_metrics,
        "learned_metrics": learned_metrics,
        "deltas": deltas,
        "gates": gates,
        "overlap_analysis": {
            "training_target_count": len(training_target_ids),
            "with_overlap": with_overlap_block,
            "without_overlap": without_overlap_block,
            "overlap_query_count": len(rows_with_overlap),
            "without_overlap_query_count": len(rows_without_overlap),
            "overlap_positive_image_ids": sorted(
                {image_id for row in rows_with_overlap for image_id in row["qrels_training_overlap_image_ids"]}
            ),
            "gates": overlap_required_gates,
        },
        "promotion_gates": promotion_gates,
        "promotion_ready": all(promotion_gates.values()) and bool(artifact.get("promotion_approved")),
        "exact_text_misses_outside_top10": exact_misses,
        "per_query": rows,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify an RLHF ranker on the data/meme corpus-backed eval set.")
    parser.add_argument("--artifact", required=True)
    parser.add_argument("--queries", default="vidsearch/eval/queries_memes.yaml")
    parser.add_argument("--output", default="artifacts/feedback_eval/post_rlhf_data_meme.json")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--training-target-pack", default=str(DEFAULT_TRAINING_TARGET_PACK))
    parser.add_argument("--no-training-target-overlap-check", action="store_true")
    parser.add_argument("--no-db-qrels", action="store_true")
    args = parser.parse_args()
    result = verify_post_rlhf(
        artifact_path=Path(args.artifact),
        queries_path=Path(args.queries),
        output_path=Path(args.output),
        limit=args.limit,
        use_db_qrels=not args.no_db_qrels,
        training_target_pack=None
        if args.no_training_target_overlap_check
        else Path(args.training_target_pack),
    )
    print(json.dumps({k: v for k, v in result.items() if k != "per_query"}, indent=2, sort_keys=True))
    return 0 if result["status"] == "verified" else 2


if __name__ == "__main__":
    raise SystemExit(main())

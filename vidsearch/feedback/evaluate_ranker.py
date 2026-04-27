from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any

import numpy as np

from vidsearch.feedback.ranker import FEATURE_KEYS, feature_vector
from vidsearch.feedback.schema import ensure_feedback_schema
from vidsearch.feedback.train_ranker import (
    _accuracy,
    _diff_matrix,
    _load_pairs,
    _selected_mrr,
    _train_logistic,
)
from vidsearch.storage import pg as pg_store


def _load_artifact(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if len(data.get("weights") or []) != len(FEATURE_KEYS):
        raise ValueError("ranker artifact weight length does not match feature keys")
    return data


def evaluate_ranker_artifact(*, artifact_path: Path, output_path: Path | None = None) -> dict[str, Any]:
    artifact = _load_artifact(artifact_path)
    pairs = _load_pairs()
    feature_version = int(artifact.get("feature_version", -1))
    versions = {pair["feature_version"] for pair in pairs}
    if versions and versions != {feature_version}:
        return {"status": "blocked", "reasons": [f"mixed feature versions for artifact {feature_version}: {sorted(versions)}"]}

    holdout_pairs = [pair for pair in pairs if pair["split"] == "holdout"] or [
        pair for pair in pairs if pair["split"] == "validation"
    ]
    train_pairs = [pair for pair in pairs if pair["split"] == "train"]
    weights = np.asarray([float(value) for value in artifact["weights"]], dtype=np.float64)
    x_holdout = _diff_matrix(holdout_pairs)
    x_train_rank = _diff_matrix(train_pairs, rank_only=True)
    x_holdout_rank = _diff_matrix(holdout_pairs, rank_only=True)
    rank_weights = _train_logistic(x_train_rank)
    position_only_accuracy = _accuracy(x_holdout_rank, rank_weights)
    pairwise_holdout_accuracy = _accuracy(x_holdout, weights)
    holdout_search_ids = {pair["search_id"] for pair in holdout_pairs}
    mrr = _selected_mrr(weights, holdout_search_ids)
    report = {
        "status": "evaluated",
        "artifact": str(artifact_path),
        "ranker_version_id": artifact.get("ranker_version_id"),
        "feature_version": feature_version,
        "pair_count": len(pairs),
        "holdout_pairs": len(holdout_pairs),
        "metrics": {
            "pairwise_holdout_accuracy": pairwise_holdout_accuracy,
            "position_only_holdout_accuracy": position_only_accuracy,
            "position_only_lift": pairwise_holdout_accuracy - position_only_accuracy,
            "selected_image_mrr": mrr["ranker_mrr"],
            "base_selected_image_mrr": mrr["base_mrr"],
            "selected_image_mrr_lift": mrr["ranker_mrr"] - mrr["base_mrr"],
            "top1_selected_rate": mrr["top1_selected_rate"],
        },
    }
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return report


def build_changed_ranking_report(
    *,
    artifact_path: Path,
    output_prefix: Path,
    limit: int = 20,
) -> dict[str, Any]:
    """Build a blind changed-ranking report plus a separate answer key."""
    ensure_feedback_schema()
    artifact = _load_artifact(artifact_path)
    weights = np.asarray([float(value) for value in artifact["weights"]], dtype=np.float64)
    with pg_store.get_cursor() as cur:
        cur.execute(
            """SELECT s.search_id::text,
                      s.query_redacted,
                      s.intent,
                      i.image_id,
                      i.rank,
                      i.features_jsonb
               FROM feedback.search_sessions s
               JOIN feedback.search_impressions i ON i.search_id = s.search_id
               ORDER BY s.served_at DESC, i.rank ASC
               LIMIT 1000"""
        )
        rows = cur.fetchall()

    by_search: dict[str, dict[str, Any]] = {}
    for search_id, query_redacted, intent, image_id, rank, features in rows:
        bucket = by_search.setdefault(search_id, {"query": query_redacted, "intent": intent, "items": []})
        bucket["items"].append(
            {
                "image_id": image_id,
                "rank": int(rank),
                "learned_score": float(np.dot(weights, np.asarray(feature_vector(features), dtype=np.float64))),
            }
        )

    rng = random.Random(1337)
    review_items: list[dict[str, Any]] = []
    answer_key: list[dict[str, Any]] = []
    for search_id, data in by_search.items():
        items = data["items"]
        base = [item["image_id"] for item in sorted(items, key=lambda item: item["rank"])]
        learned = [item["image_id"] for item in sorted(items, key=lambda item: item["learned_score"], reverse=True)]
        if base == learned:
            continue
        variants = [("A", base), ("B", learned)]
        rng.shuffle(variants)
        review_items.append(
            {
                "review_id": f"changed_{len(review_items) + 1:03d}",
                "query": data["query"],
                "intent": data["intent"],
                "variant_a_image_ids": variants[0][1],
                "variant_b_image_ids": variants[1][1],
                "verdict": "",
                "notes": "",
            }
        )
        answer_key.append(
            {
                "review_id": review_items[-1]["review_id"],
                "search_id": search_id,
                "variant_a_source": "base" if variants[0][1] == base else "learned",
                "variant_b_source": "base" if variants[1][1] == base else "learned",
            }
        )
        if len(review_items) >= limit:
            break

    blind_path = output_prefix.with_name(output_prefix.name + "_blind.json")
    key_path = output_prefix.with_name(output_prefix.name + "_answer_key.json")
    blind_path.parent.mkdir(parents=True, exist_ok=True)
    blind_path.write_text(json.dumps({"items": review_items}, indent=2, sort_keys=True), encoding="utf-8")
    key_path.write_text(json.dumps({"items": answer_key}, indent=2, sort_keys=True), encoding="utf-8")
    return {
        "status": "written",
        "changed_rankings": len(review_items),
        "blind_report": str(blind_path),
        "answer_key": str(key_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate a feedback ranker artifact.")
    parser.add_argument("--artifact", required=True)
    parser.add_argument("--output", default="artifacts/feedback_eval/latest.json")
    parser.add_argument("--changed-report-prefix", default="")
    args = parser.parse_args()
    result = evaluate_ranker_artifact(artifact_path=Path(args.artifact), output_path=Path(args.output))
    if args.changed_report_prefix and result["status"] == "evaluated":
        result["changed_ranking_report"] = build_changed_ranking_report(
            artifact_path=Path(args.artifact),
            output_prefix=Path(args.changed_report_prefix),
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "evaluated" else 2


if __name__ == "__main__":
    raise SystemExit(main())

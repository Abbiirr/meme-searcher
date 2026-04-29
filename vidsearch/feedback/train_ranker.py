from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
from psycopg.types.json import Json

from vidsearch.feedback.ranker import FEATURE_KEYS, feature_vector
from vidsearch.feedback.schema import ensure_feedback_schema
from vidsearch.feedback.service import FEATURE_VERSION, target_id_from_client_session_id
from vidsearch.storage import pg as pg_store


MIN_UNIQUE_QUERY_JUDGMENTS = 200
MIN_JUDGMENTS_PER_INTENT = 50
MIN_PREFERENCE_PAIRS = 300
PAIRWISE_BASELINE_MIN_LIFT = 0.05
PAIRWISE_BASELINE_HEALTHY_MAX_RANK1_SHARE = 0.60
SELECTED_MRR_MIN = 0.50
SELECTED_MRR_PRESERVE_RATIO = 0.99


def _sigmoid(value: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(value, -40, 40)))


def _bucket(split_key: str) -> int:
    digest = hashlib.sha256(split_key.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % 100


def _split_key(search_id: str, client_session_id: str | None = None, target_id: str | None = None) -> str:
    return target_id or target_id_from_client_session_id(client_session_id) or search_id


def _split_name(search_id: str, client_session_id: str | None = None, target_id: str | None = None) -> str:
    bucket = _bucket(_split_key(search_id, client_session_id, target_id))
    if bucket < 70:
        return "train"
    if bucket < 85:
        return "validation"
    return "holdout"


def _client_session_clause(client_session_prefix: str | None) -> tuple[str, tuple[str, ...]]:
    if not client_session_prefix:
        return "", ()
    return " AND s.client_session_id LIKE %s", (f"{client_session_prefix}%",)


def _load_pairs(client_session_prefix: str | None = None) -> list[dict[str, Any]]:
    ensure_feedback_schema()
    session_clause, params = _client_session_clause(client_session_prefix)
    with pg_store.get_cursor() as cur:
        cur.execute(
            """SELECT p.search_id::text,
                      s.intent,
                      s.client_session_id,
                      s.target_id,
                      p.feature_version,
                      winner.features_jsonb,
                      loser.features_jsonb,
                      p.winner_impression_id::text,
                      p.loser_impression_id::text,
                      p.pair_weight
               FROM feedback.preference_pairs p
               JOIN feedback.search_sessions s ON s.search_id = p.search_id
               JOIN feedback.search_impressions winner ON winner.impression_id = p.winner_impression_id
               JOIN feedback.search_impressions loser ON loser.impression_id = p.loser_impression_id
               WHERE p.tombstoned_at IS NULL
               """ + session_clause + """
               ORDER BY p.created_at ASC, p.pair_id ASC""",
            params,
        )
        return [
            {
                "search_id": row[0],
                "intent": row[1],
                "client_session_id": row[2],
                "target_id": row[3],
                "feature_version": int(row[4]),
                "winner_features": row[5],
                "loser_features": row[6],
                "winner_impression_id": row[7],
                "loser_impression_id": row[8],
                "pair_weight": float(row[9]),
                "split_key": _split_key(row[0], row[2], row[3]),
                "split": _split_name(row[0], row[2], row[3]),
            }
            for row in cur.fetchall()
        ]


def _feedback_volume(client_session_prefix: str | None = None) -> dict[str, Any]:
    ensure_feedback_schema()
    session_clause, params = _client_session_clause(client_session_prefix)
    with pg_store.get_cursor() as cur:
        cur.execute(
            """SELECT COUNT(DISTINCT s.search_id)
               FROM feedback.judgments j
               JOIN feedback.search_sessions s ON s.search_id = j.search_id
               WHERE j.tombstoned_at IS NULL AND j.action IN ('select','reject','none_correct')
               """ + session_clause,
            params,
        )
        unique_query_judgments = int(cur.fetchone()[0])

        cur.execute(
            """SELECT s.intent, COUNT(DISTINCT s.search_id)
               FROM feedback.judgments j
               JOIN feedback.search_sessions s ON s.search_id = j.search_id
               WHERE j.tombstoned_at IS NULL AND j.action IN ('select','reject','none_correct')
               """ + session_clause + """
               GROUP BY s.intent"""
            ,
            params,
        )
        per_intent = {row[0]: int(row[1]) for row in cur.fetchall()}

        cur.execute(
            """SELECT COUNT(*)
               FROM feedback.preference_pairs p
               JOIN feedback.search_sessions s ON s.search_id = p.search_id
               WHERE p.tombstoned_at IS NULL
               """ + session_clause,
            params,
        )
        pairs = int(cur.fetchone()[0])

    return {
        "unique_query_judgments": unique_query_judgments,
        "judgments_per_intent": per_intent,
        "preference_pairs": pairs,
        "client_session_prefix": client_session_prefix,
    }


def _volume_gate(volume: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons = []
    if volume["unique_query_judgments"] < MIN_UNIQUE_QUERY_JUDGMENTS:
        reasons.append(f"unique-query judgments {volume['unique_query_judgments']} < {MIN_UNIQUE_QUERY_JUDGMENTS}")
    if volume["preference_pairs"] < MIN_PREFERENCE_PAIRS:
        reasons.append(f"preference pairs {volume['preference_pairs']} < {MIN_PREFERENCE_PAIRS}")
    for intent in ("exact_text", "fuzzy_text", "semantic_description", "mixed_visual_description"):
        count = volume["judgments_per_intent"].get(intent, 0)
        if count < MIN_JUDGMENTS_PER_INTENT:
            reasons.append(f"{intent} judgments {count} < {MIN_JUDGMENTS_PER_INTENT}")
    return not reasons, reasons


def _per_intent_capability(volume: dict[str, Any]) -> dict[str, dict[str, Any]]:
    capability: dict[str, dict[str, Any]] = {}
    per_intent = volume.get("judgments_per_intent") or {}
    for intent in ("exact_text", "fuzzy_text", "semantic_description", "mixed_visual_description"):
        count = int(per_intent.get(intent, 0))
        capability[intent] = {
            "judgments": count,
            "promotion_volume_ok": count >= MIN_JUDGMENTS_PER_INTENT,
            "diagnostic_volume_ok": count >= max(1, MIN_JUDGMENTS_PER_INTENT // 2),
            "claim": "promotion-eligible" if count >= MIN_JUDGMENTS_PER_INTENT else "diagnostic-only",
        }
    return capability


def _diff_matrix(pairs: list[dict[str, Any]], *, rank_only: bool = False) -> np.ndarray:
    rows: list[list[float]] = []
    for pair in pairs:
        winner = feature_vector(pair["winner_features"])
        loser = feature_vector(pair["loser_features"])
        if rank_only:
            # Pairwise rank-only baseline must not include an intercept: all
            # rows are winner-minus-loser comparisons with positive labels, so
            # a constant term can learn the trivial "always winner" classifier.
            rows.append([winner[FEATURE_KEYS.index("rank")] - loser[FEATURE_KEYS.index("rank")]])
        else:
            rows.append([w - l for w, l in zip(winner, loser, strict=True)])
    return np.asarray(rows, dtype=np.float64)


def _winner_rank(pair: dict[str, Any]) -> int:
    per = (((pair.get("winner_features") or {}).get("features") or {}).get("per_impression") or {})
    return int(per.get("rank") or 0)


def _pair_weight(pair: dict[str, Any], *, rank1_weight: float = 1.0) -> float:
    weight = float(pair.get("pair_weight") or 1.0)
    if _winner_rank(pair) == 1:
        weight *= rank1_weight
    return max(weight, 0.0)


def _pair_weights(pairs: list[dict[str, Any]], *, rank1_weight: float = 1.0) -> np.ndarray:
    return np.asarray([_pair_weight(pair, rank1_weight=rank1_weight) for pair in pairs], dtype=np.float64)


def _training_composition(pairs: list[dict[str, Any]], *, rank1_weight: float = 1.0) -> dict[str, Any]:
    counts = {
        "target_at_rank_1": 0,
        "target_in_top_10_not_1": 0,
        "target_in_top_20_not_10": 0,
        "target_after_20_or_unknown": 0,
    }
    rerank_missing = 0
    rerank_total = 0
    effective_rank1 = 0.0
    effective_total = 0.0

    for pair in pairs:
        rank = _winner_rank(pair)
        if rank == 1:
            bucket = "target_at_rank_1"
        elif rank <= 10 and rank > 1:
            bucket = "target_in_top_10_not_1"
        elif rank <= 20 and rank > 10:
            bucket = "target_in_top_20_not_10"
        else:
            bucket = "target_after_20_or_unknown"
        counts[bucket] += 1

        weight = _pair_weight(pair, rank1_weight=rank1_weight)
        effective_total += weight
        if rank == 1:
            effective_rank1 += weight

        for side in ("winner_features", "loser_features"):
            per = (((pair.get(side) or {}).get("features") or {}).get("per_impression") or {})
            rerank_total += 1
            if per.get("rerank_score") is None:
                rerank_missing += 1

    rank1_share = (effective_rank1 / effective_total) if effective_total else 0.0
    return {
        "pair_counts_by_winner_rank_bucket": counts,
        "effective_pair_weight_total": effective_total,
        "effective_rank1_pair_weight": effective_rank1,
        "effective_rank1_pair_share": rank1_share,
        "rank_bucket_distribution_healthy": rank1_share <= PAIRWISE_BASELINE_HEALTHY_MAX_RANK1_SHARE,
        "rerank_score_imputation": "missing rerank_score is zero-filled in feature_vector(feature_version=1)",
        "rerank_score_missing_values": rerank_missing,
        "rerank_score_total_values": rerank_total,
        "rerank_score_missing_share": (rerank_missing / rerank_total) if rerank_total else 0.0,
    }


def _train_logistic(
    x: np.ndarray,
    *,
    sample_weight: np.ndarray | None = None,
    epochs: int = 1000,
    lr: float = 0.05,
    l2: float = 0.001,
) -> np.ndarray:
    weights = np.zeros(x.shape[1], dtype=np.float64)
    if len(x) == 0:
        return weights
    if sample_weight is None:
        sample_weight = np.ones(len(x), dtype=np.float64)
    sample_weight = np.asarray(sample_weight, dtype=np.float64)
    weight_sum = float(sample_weight.sum()) or 1.0

    for _ in range(epochs):
        logits = x @ weights
        probs = _sigmoid(logits)
        grad = -(((1.0 - probs) * sample_weight)[:, None] * x).sum(axis=0) / weight_sum + l2 * weights
        weights -= lr * grad
    return weights


def _accuracy(x: np.ndarray, weights: np.ndarray) -> float:
    if len(x) == 0:
        return 0.0
    return float((_sigmoid(x @ weights) >= 0.5).mean())


def _selected_mrr(weights: np.ndarray, holdout_search_ids: set[str]) -> dict[str, float]:
    if not holdout_search_ids:
        return {"base_mrr": 0.0, "ranker_mrr": 0.0, "top1_selected_rate": 0.0, "n": 0.0}

    with pg_store.get_cursor() as cur:
        cur.execute(
            """SELECT s.search_id::text,
                      j.impression_id::text,
                      i.impression_id::text,
                      i.rank,
                      i.base_rank,
                      i.features_jsonb
               FROM feedback.search_sessions s
               JOIN feedback.judgments j ON j.search_id = s.search_id
               JOIN feedback.search_impressions i ON i.search_id = s.search_id
               WHERE s.search_id = ANY(%s)
                 AND j.action = 'select'
                 AND j.tombstoned_at IS NULL
               ORDER BY s.search_id, i.rank""",
            (list(holdout_search_ids),),
        )
        rows = cur.fetchall()

    by_search: dict[str, dict[str, Any]] = {}
    for search_id, selected_impression_id, impression_id, rank, base_rank, features in rows:
        bucket = by_search.setdefault(search_id, {"selected": selected_impression_id, "impressions": []})
        bucket["impressions"].append(
            {
                "impression_id": impression_id,
                "rank": int(rank),
                "base_rank": int(base_rank),
                "score": float(np.dot(weights, np.asarray(feature_vector(features), dtype=np.float64))),
            }
        )

    base_mrr = 0.0
    ranker_mrr = 0.0
    top1 = 0.0
    n = 0
    for data in by_search.values():
        selected = data["selected"]
        impressions = data["impressions"]
        selected_base_rank = next((item["base_rank"] for item in impressions if item["impression_id"] == selected), None)
        if selected_base_rank is None:
            continue
        ordered = sorted(impressions, key=lambda item: item["score"], reverse=True)
        selected_rank = next(index for index, item in enumerate(ordered, start=1) if item["impression_id"] == selected)
        base_mrr += 1.0 / selected_base_rank
        ranker_mrr += 1.0 / selected_rank
        top1 += 1.0 if selected_rank == 1 else 0.0
        n += 1

    if n == 0:
        return {"base_mrr": 0.0, "ranker_mrr": 0.0, "top1_selected_rate": 0.0, "n": 0.0}
    return {
        "base_mrr": base_mrr / n,
        "ranker_mrr": ranker_mrr / n,
        "top1_selected_rate": top1 / n,
        "n": float(n),
    }


def train_feedback_ranker(
    *,
    output_path: Path,
    allow_small: bool = False,
    approve_promotion: bool = False,
    p0_g4_passing: bool = False,
    client_session_prefix: str | None = None,
    rank1_weight: float = 1.0,
) -> dict[str, Any]:
    pairs = _load_pairs(client_session_prefix=client_session_prefix)
    volume = _feedback_volume(client_session_prefix=client_session_prefix)
    volume_ok, volume_reasons = _volume_gate(volume)
    if not pairs:
        return {"status": "blocked", "volume": volume, "reasons": ["no preference pairs available"]}
    if not volume_ok and not allow_small:
        return {"status": "blocked", "volume": volume, "reasons": volume_reasons}

    feature_versions = {pair["feature_version"] for pair in pairs}
    if feature_versions and feature_versions != {FEATURE_VERSION}:
        return {"status": "blocked", "reasons": [f"mixed feature versions: {sorted(feature_versions)}"]}

    train_pairs = [pair for pair in pairs if pair["split"] == "train"]
    holdout_pairs = [pair for pair in pairs if pair["split"] == "holdout"]
    if not holdout_pairs:
        holdout_pairs = [pair for pair in pairs if pair["split"] == "validation"]

    x_train = _diff_matrix(train_pairs)
    x_holdout = _diff_matrix(holdout_pairs)
    train_sample_weights = _pair_weights(train_pairs, rank1_weight=rank1_weight)
    weights = _train_logistic(x_train, sample_weight=train_sample_weights)

    x_train_rank = _diff_matrix(train_pairs, rank_only=True)
    x_holdout_rank = _diff_matrix(holdout_pairs, rank_only=True)
    rank_weights = _train_logistic(x_train_rank, sample_weight=train_sample_weights)

    pairwise_holdout_accuracy = _accuracy(x_holdout, weights)
    position_only_accuracy = _accuracy(x_holdout_rank, rank_weights)
    holdout_search_ids = {pair["search_id"] for pair in holdout_pairs}
    mrr = _selected_mrr(weights, holdout_search_ids)
    composition = _training_composition(train_pairs, rank1_weight=rank1_weight)
    position_lift_gate_applicable = bool(composition["rank_bucket_distribution_healthy"])
    pairwise_lift = pairwise_holdout_accuracy - position_only_accuracy
    selected_mrr_threshold = max(SELECTED_MRR_MIN, mrr["base_mrr"] * SELECTED_MRR_PRESERVE_RATIO)

    gates = {
        "volume_ok": volume_ok,
        "pairwise_accuracy_ok": pairwise_holdout_accuracy >= 0.60,
        "position_baseline_lift_gate_applicable": position_lift_gate_applicable,
        "position_baseline_lift_ok": (not position_lift_gate_applicable)
        or pairwise_holdout_accuracy >= position_only_accuracy + PAIRWISE_BASELINE_MIN_LIFT,
        "selected_mrr_ok": mrr["ranker_mrr"] >= SELECTED_MRR_MIN,
        "selected_mrr_preserve_ok": mrr["ranker_mrr"] >= selected_mrr_threshold,
        "p0_g4_passing": p0_g4_passing,
    }
    promotion_approved = bool(approve_promotion and all(gates.values()))
    ranker_version_id = f"feedback_pairwise_v{FEATURE_VERSION}_{hashlib.sha256(str(weights.tolist()).encode()).hexdigest()[:12]}"

    artifact = {
        "ranker_version_id": ranker_version_id,
        "kind": "pairwise_logistic_numpy",
        "feature_version": FEATURE_VERSION,
        "feature_keys": FEATURE_KEYS,
        "weights": [float(value) for value in weights],
        "rank_only_baseline_weights": [float(value) for value in rank_weights],
        "promotion_approved": promotion_approved,
        "gates": gates,
        "volume": volume,
        "per_intent_capability": _per_intent_capability(volume),
        "training_composition": composition,
        "metrics": {
            "pairwise_holdout_accuracy": pairwise_holdout_accuracy,
            "position_only_holdout_accuracy": position_only_accuracy,
            "position_only_holdout_lift": pairwise_lift,
            "selected_image_mrr": mrr["ranker_mrr"],
            "base_selected_image_mrr": mrr["base_mrr"],
            "selected_image_mrr_preserve_threshold": selected_mrr_threshold,
            "top1_selected_rate": mrr["top1_selected_rate"],
            "holdout_selected_searches": mrr["n"],
            "train_pairs": float(len(train_pairs)),
            "holdout_pairs": float(len(holdout_pairs)),
        },
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(artifact, indent=2, sort_keys=True), encoding="utf-8")

    ensure_feedback_schema()
    with pg_store.get_cursor() as cur:
        cur.execute(
            """INSERT INTO feedback.ranker_versions
               (ranker_version_id, kind, status, feature_version, artifact_uri, metrics)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON CONFLICT (ranker_version_id) DO UPDATE SET
                 status = EXCLUDED.status,
                 artifact_uri = EXCLUDED.artifact_uri,
                 metrics = EXCLUDED.metrics""",
            (
                ranker_version_id,
                "pairwise_logistic_numpy",
                "candidate",
                FEATURE_VERSION,
                str(output_path),
                Json(artifact["metrics"]),
            ),
        )
        cur.execute(
            """INSERT INTO feedback.training_snapshots
               (ranker_version_id, feature_version, pair_count, judgment_count, query_count, config, metrics, artifact_uri)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                ranker_version_id,
                FEATURE_VERSION,
                len(pairs),
                volume["unique_query_judgments"],
                len({pair["search_id"] for pair in pairs}),
                Json(
                    {
                        "allow_small": allow_small,
                        "approve_promotion": approve_promotion,
                        "p0_g4_passing": p0_g4_passing,
                        "client_session_prefix": client_session_prefix,
                        "rank1_weight": rank1_weight,
                        "pairwise_baseline_lift_gate_applicable": position_lift_gate_applicable,
                    }
                ),
                Json(artifact["metrics"] | {"promotion_approved": promotion_approved}),
                str(output_path),
            ),
        )

    return {"status": "written", "artifact": str(output_path), "ranker_version_id": ranker_version_id, **artifact}


def main() -> int:
    parser = argparse.ArgumentParser(description="Train the gated feedback pairwise ranker.")
    parser.add_argument("--output", default="artifacts/feedback_rankers/latest.json")
    parser.add_argument("--allow-small", action="store_true", help="Write a smoke artifact below volume gates.")
    parser.add_argument("--approve-promotion", action="store_true", help="Mark artifact promotion-approved if all gates pass.")
    parser.add_argument("--p0-g4-passing", action="store_true", help="Assert the current Phase 0 P0-G4 gate is passing.")
    parser.add_argument(
        "--client-session-prefix",
        default="",
        help="Train only on feedback sessions whose client_session_id starts with this prefix.",
    )
    parser.add_argument(
        "--rank1-weight",
        type=float,
        default=1.0,
        help="Effective training weight multiplier for pairs where the selected target was already rank 1.",
    )
    args = parser.parse_args()

    result = train_feedback_ranker(
        output_path=Path(args.output),
        allow_small=args.allow_small,
        approve_promotion=args.approve_promotion,
        p0_g4_passing=args.p0_g4_passing,
        client_session_prefix=args.client_session_prefix or None,
        rank1_weight=args.rank1_weight,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "written" else 2


if __name__ == "__main__":
    raise SystemExit(main())

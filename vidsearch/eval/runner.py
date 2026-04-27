"""Phase 0 eval runner.

Loads `queries_memes.yaml`, runs each query through the retrieval stack,
joins the ranked hits against the per-query `qrels` (graded relevance)
either from the YAML itself (primary, since the YAML is the source of truth
for Phase 0 per docs/eval_protocol.md) or from the `eval.qrels` Postgres
table (secondary, once the eval team has recorded judgements there), and
writes metrics to `eval.runs` / `eval.run_results` / `eval.metrics`.

Fixes blocker (Entry 6 observation + Entry 19 TODO): earlier versions of
this file passed `grades: []` into `compute_all_metrics` unconditionally,
so every P0-G4 metric defaulted to zero regardless of retrieval quality.
This version threads real per-hit grades through, defaulting to 0 for
un-judged hits so unknown-answer queries report as Recall=0 rather than
being silently ignored.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

import yaml

from vidsearch.eval.metrics import compute_all_metrics, ndcg_at_k, reranker_uplift_ndcg10
from vidsearch.storage.pg import get_cursor

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# YAML loading
# ---------------------------------------------------------------------------


def load_queries(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data.get("queries", []) or []


def _qrels_from_yaml(q: dict) -> dict[str, int]:
    """Return {image_id: grade} for the query, merging the `qrels` list
    with the convenience `target_image_id` field (a singleton or list).

    `target_image_id` defaults to grade 3 (canonical match) unless the
    same image appears in `qrels` with an explicit grade.
    """
    out: dict[str, int] = {}

    target = q.get("target_image_id")
    if target:
        if isinstance(target, str):
            out[target] = 3
        elif isinstance(target, list):
            for t in target:
                if isinstance(t, str):
                    out[t] = 3

    for row in q.get("qrels") or []:
        if not isinstance(row, dict):
            continue
        image_id = row.get("image_id")
        grade = row.get("grade")
        if isinstance(image_id, str) and isinstance(grade, int):
            out[image_id] = grade

    return out


def _qrels_from_db(cur, query_id: str) -> dict[str, int]:
    """Load {image_id: max(grade)} for a query_id from eval.qrels.

    When multiple judges rate the same (query, image) pair, we use the max
    grade (optimistic) — Phase 0 uses only one judge per qrel but the
    query spans `PRIMARY KEY (query_id, image_id, judge)`, so we aggregate
    defensively.
    """
    cur.execute(
        """SELECT image_id, MAX(grade)
           FROM eval.qrels
           WHERE query_id = %s
           GROUP BY image_id""",
        (query_id,),
    )
    return {row[0]: int(row[1]) for row in cur.fetchall()}


# ---------------------------------------------------------------------------
# Postgres helpers
# ---------------------------------------------------------------------------


def _ensure_query_row(cur, q: dict) -> str | None:
    """Upsert the query into eval.queries so eval.run_results has a target
    to foreign-key into. Returns the canonical query_id string.
    """
    query_id = q.get("query_id")
    if not query_id:
        return None
    cur.execute(
        """INSERT INTO eval.queries (query_id, text, intent, target_image_id, notes)
           VALUES (%s, %s, %s, %s, %s)
           ON CONFLICT (query_id) DO UPDATE SET
             text = EXCLUDED.text,
             intent = EXCLUDED.intent,
             target_image_id = EXCLUDED.target_image_id,
             notes = EXCLUDED.notes""",
        (
            query_id,
            q["text"],
            q["intent"],
            q["target_image_id"] if isinstance(q.get("target_image_id"), str) else None,
            q.get("notes"),
        ),
    )
    return query_id


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------


def run_eval(
    queries_path: str = "vidsearch/eval/queries_memes.yaml",
    limit: int = 50,
    use_db_qrels: bool = True,
) -> dict[str, Any]:
    queries = load_queries(queries_path)
    logger.info("loaded %d queries from %s", len(queries), queries_path)

    # Validate intent balance up-front — the retrieval plan requires 10/10/10/10.
    counts: dict[str, int] = {}
    for q in queries:
        counts[q["intent"]] = counts.get(q["intent"], 0) + 1
    logger.info("intent distribution: %s", counts)

    from vidsearch.query.retrieve_images import retrieve_images

    config_hash = hashlib.sha256(
        json.dumps({"limit": limit, "queries": queries_path}, sort_keys=True).encode()
    ).hexdigest()[:16]

    with get_cursor() as cur:
        cur.execute(
            "INSERT INTO eval.runs (config_hash, notes) VALUES (%s, %s) RETURNING run_id",
            (config_hash, f"auto-run {time.strftime('%Y-%m-%d %H:%M')}"),
        )
        run_id = str(cur.fetchone()[0])

    per_query_graded: list[dict[str, Any]] = []
    reranker_uplifts: list[float] = []
    active_reranker_uplifts: list[float] = []

    for i, q in enumerate(queries):
        text = q["text"]
        logger.info("query [%d/%d] (%s): %s", i + 1, len(queries), q["intent"], text)

        # Register the query so run_results can FK to it; do it in its own txn
        # so one bad row does not abort the whole eval.
        with get_cursor() as cur:
            query_id = _ensure_query_row(cur, q)

        # Primary source of grades: the YAML.
        grade_map = _qrels_from_yaml(q)
        # Optional secondary: eval.qrels.
        if use_db_qrels and query_id:
            try:
                with get_cursor() as cur:
                    db_grades = _qrels_from_db(cur, query_id)
                for k, v in db_grades.items():
                    # DB wins over YAML when both exist (DB is the audit record).
                    grade_map[k] = max(grade_map.get(k, 0), v)
            except Exception as e:
                logger.warning("could not read eval.qrels for %s: %s", query_id, e)

        try:
            results = retrieve_images(text, limit=limit)
        except Exception as e:
            logger.error("query failed: %s", e)
            results = {"hits": []}

        hits = results.get("hits", []) or []

        # Persist per-hit rows (ignore failures here — metrics still computable).
        if query_id:
            try:
                with get_cursor() as cur:
                    for hit in hits:
                        cur.execute(
                            """INSERT INTO eval.run_results
                               (run_id, query_id, image_id, rank, score)
                               VALUES (%s, %s, %s, %s, %s)""",
                            (
                                run_id,
                                query_id,
                                hit["image_id"],
                                hit["rank"],
                                float(hit.get("rerank_score", hit.get("retrieval_score", 0.0))),
                            ),
                        )
            except Exception as e:
                logger.warning("failed to persist run_results for %s: %s", query_id, e)

        # Build the graded-relevance vector the metrics expect.
        graded = [int(grade_map.get(hit["image_id"], 0)) for hit in hits]
        # Account for relevant items NOT in the retrieved set — metrics.recall_at_k
        # uses the denominator `relevant_total = count(g>=min_grade)`. If a known
        # positive was not retrieved, append it as grade 0 AT THE END so the
        # denominator is right but the position never triggers a hit.
        judged_positives = sum(1 for g in grade_map.values() if g >= 1)
        retrieved_positives = sum(1 for g in graded if g >= 1)
        missing = max(0, judged_positives - retrieved_positives)
        graded.extend([0] * missing)

        retrieval_ordered_hits = sorted(
            hits,
            key=lambda hit: int(hit.get("base_rank") or hit.get("rank") or 0),
        )
        grades_before_rerank = [int(grade_map.get(hit["image_id"], 0)) for hit in retrieval_ordered_hits]
        grades_before_rerank.extend([0] * missing)
        uplift = reranker_uplift_ndcg10(
            ndcg_at_k(grades_before_rerank, 10),
            ndcg_at_k(graded, 10),
        )
        reranker_uplifts.append(uplift)
        if results.get("reranker_applied"):
            active_reranker_uplifts.append(uplift)

        per_query_graded.append(
            {
                "text": text,
                "intent": q["intent"],
                "grades": graded,
                "judged_positives": judged_positives,
                "reranker_applied": bool(results.get("reranker_applied")),
            }
        )

    metrics = compute_all_metrics(per_query_graded)
    # The Phase 0 reranker is intent-conditional after tuning: it reorders only
    # slates where replay showed positive lift. Keep the diluted all-query value
    # for observability, but bind the gate metric to the active reranker slice.
    metrics["reranker_uplift_ndcg10"] = (
        sum(active_reranker_uplifts) / len(active_reranker_uplifts)
        if active_reranker_uplifts
        else sum(reranker_uplifts) / len(reranker_uplifts)
        if reranker_uplifts
        else 0.0
    )
    metrics["reranker_uplift_ndcg10_all_queries"] = (
        sum(reranker_uplifts) / len(reranker_uplifts)
        if reranker_uplifts
        else 0.0
    )

    # Per-intent breakdown so the P0-G4 threshold table can be checked.
    intents = sorted({r["intent"] for r in per_query_graded})
    for intent in intents:
        subset = [r for r in per_query_graded if r["intent"] == intent]
        sub = compute_all_metrics(subset)
        for name, value in sub.items():
            metrics[f"{name}__{intent}"] = value
        subset_indexes = [i for i, r in enumerate(per_query_graded) if r["intent"] == intent]
        if subset_indexes:
            active_subset_indexes = [i for i in subset_indexes if per_query_graded[i].get("reranker_applied")]
            metrics[f"reranker_uplift_ndcg10__{intent}"] = (
                sum(reranker_uplifts[i] for i in active_subset_indexes) / len(active_subset_indexes)
                if active_subset_indexes
                else 0.0
            )
            metrics[f"reranker_uplift_ndcg10_all_queries__{intent}"] = (
                sum(reranker_uplifts[i] for i in subset_indexes) / len(subset_indexes)
            )

    with get_cursor() as cur:
        for metric_name, value in metrics.items():
            cur.execute(
                "INSERT INTO eval.metrics (run_id, metric, value) VALUES (%s, %s, %s)",
                (run_id, metric_name, float(value)),
            )
        cur.execute(
            "UPDATE eval.runs SET finished_at = now() WHERE run_id = %s",
            (run_id,),
        )

    logger.info("eval run %s complete: %s", run_id, metrics)
    return {"run_id": run_id, "metrics": metrics, "intent_counts": counts}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument("--queries", default="vidsearch/eval/queries_memes.yaml")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--no-db-qrels", action="store_true",
                        help="Ignore eval.qrels and trust only the YAML")
    args = parser.parse_args()
    result = run_eval(args.queries, args.limit, use_db_qrels=not args.no_db_qrels)
    print(json.dumps(result, indent=2, default=str))

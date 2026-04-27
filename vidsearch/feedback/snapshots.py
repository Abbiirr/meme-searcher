from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from psycopg.types.json import Json

from vidsearch.feedback.schema import ensure_feedback_schema
from vidsearch.feedback.service import FEATURE_VERSION
from vidsearch.storage import pg as pg_store


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _load_pair_rows() -> list[dict[str, Any]]:
    ensure_feedback_schema()
    with pg_store.get_cursor() as cur:
        cur.execute(
            """SELECT p.pair_id::text,
                      p.search_id::text,
                      s.query_redacted,
                      s.intent,
                      s.ranker_version_id,
                      s.ranker_mode,
                      s.feature_version,
                      s.propensity_method,
                      s.exploration_policy,
                      s.served_at,
                      p.source_judgment_id::text,
                      p.winner_impression_id::text,
                      p.loser_impression_id::text,
                      p.winner_image_id,
                      p.loser_image_id,
                      winner.rank,
                      winner.base_rank,
                      winner.features_jsonb,
                      loser.rank,
                      loser.base_rank,
                      loser.features_jsonb,
                      p.feature_version,
                      p.derivation_method,
                      p.pair_weight,
                      p.created_at
               FROM feedback.preference_pairs p
               JOIN feedback.search_sessions s ON s.search_id = p.search_id
               JOIN feedback.search_impressions winner ON winner.impression_id = p.winner_impression_id
               JOIN feedback.search_impressions loser ON loser.impression_id = p.loser_impression_id
               WHERE p.tombstoned_at IS NULL
                 AND s.opt_out = false
                 AND s.deleted_at IS NULL
               ORDER BY s.served_at ASC, p.created_at ASC, p.pair_id ASC"""
        )
        rows = []
        for row in cur.fetchall():
            rows.append(
                {
                    "record_type": "preference_pair",
                    "pair_id": row[0],
                    "search_id": row[1],
                    "query_redacted": row[2],
                    "intent": row[3],
                    "ranker_version_id": row[4],
                    "ranker_mode": row[5],
                    "session_feature_version": int(row[6]),
                    "propensity_method": row[7],
                    "exploration_policy": row[8],
                    "served_at": row[9],
                    "source_judgment_id": row[10],
                    "chosen": {
                        "impression_id": row[11],
                        "image_id": row[13],
                        "rank": int(row[15]),
                        "base_rank": int(row[16]),
                        "features": row[17],
                    },
                    "rejected": {
                        "impression_id": row[12],
                        "image_id": row[14],
                        "rank": int(row[18]),
                        "base_rank": int(row[19]),
                        "features": row[20],
                    },
                    "feature_version": int(row[21]),
                    "derivation_method": row[22],
                    "pair_weight": float(row[23]),
                    "created_at": row[24],
                }
            )
    return rows


def _load_none_correct_rows() -> list[dict[str, Any]]:
    ensure_feedback_schema()
    with pg_store.get_cursor() as cur:
        cur.execute(
            """SELECT j.judgment_id::text,
                      j.search_id::text,
                      s.query_redacted,
                      s.intent,
                      s.ranker_version_id,
                      s.ranker_mode,
                      s.feature_version,
                      s.propensity_method,
                      s.exploration_policy,
                      s.served_at,
                      j.created_at
               FROM feedback.judgments j
               JOIN feedback.search_sessions s ON s.search_id = j.search_id
               WHERE j.action = 'none_correct'
                 AND j.tombstoned_at IS NULL
                 AND s.opt_out = false
                 AND s.deleted_at IS NULL
               ORDER BY s.served_at ASC, j.created_at ASC, j.judgment_id ASC"""
        )
        sessions = cur.fetchall()

        rows = []
        for (
            judgment_id,
            search_id,
            query_redacted,
            intent,
            ranker_version_id,
            ranker_mode,
            session_feature_version,
            propensity_method,
            exploration_policy,
            served_at,
            created_at,
        ) in sessions:
            cur.execute(
                """SELECT impression_id::text, image_id, rank, base_rank, features_jsonb
                   FROM feedback.search_impressions
                   WHERE search_id = %s
                   ORDER BY rank ASC""",
                (search_id,),
            )
            candidates = [
                {
                    "impression_id": row[0],
                    "image_id": row[1],
                    "rank": int(row[2]),
                    "base_rank": int(row[3]),
                    "features": row[4],
                }
                for row in cur.fetchall()
            ]
            rows.append(
                {
                    "record_type": "none_correct",
                    "judgment_id": judgment_id,
                    "search_id": search_id,
                    "query_redacted": query_redacted,
                    "intent": intent,
                    "ranker_version_id": ranker_version_id,
                    "ranker_mode": ranker_mode,
                    "session_feature_version": int(session_feature_version),
                    "propensity_method": propensity_method,
                    "exploration_policy": exploration_policy,
                    "served_at": served_at,
                    "candidates": candidates,
                    "feature_version": FEATURE_VERSION,
                    "created_at": created_at,
                }
            )
    return rows


def _load_snapshot_rows() -> list[dict[str, Any]]:
    return _load_pair_rows() + _load_none_correct_rows()


def build_training_snapshot(
    *,
    output_path: Path,
    name: str = "feedback_snapshot",
    persist: bool = True,
) -> dict[str, Any]:
    rows = _load_snapshot_rows()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(json.dumps(row, sort_keys=True, default=_json_default) + "\n" for row in rows)
    output_path.write_text(payload, encoding="utf-8")
    digest = hashlib.sha256(output_path.read_bytes()).hexdigest()

    pair_rows = [row for row in rows if row["record_type"] == "preference_pair"]
    search_ids = {row["search_id"] for row in rows}
    judgment_ids = {
        row["source_judgment_id"] if row["record_type"] == "preference_pair" else row["judgment_id"]
        for row in rows
    }
    served_times = [row["served_at"] for row in rows if row.get("served_at")]
    result = {
        "status": "written",
        "artifact_uri": str(output_path),
        "export_sha256": digest,
        "pair_count": len(pair_rows),
        "judgment_count": len(judgment_ids),
        "query_count": len(search_ids),
        "feature_version": FEATURE_VERSION,
    }

    if persist:
        ensure_feedback_schema()
        with pg_store.get_cursor() as cur:
            cur.execute(
                """INSERT INTO feedback.training_snapshots
                   (feature_version, pair_count, judgment_count, query_count, source_started_at,
                    source_ended_at, export_sha256, config, artifact_uri)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING snapshot_id::text""",
                (
                    FEATURE_VERSION,
                    len(pair_rows),
                    len(judgment_ids),
                    len(search_ids),
                    min(served_times) if served_times else None,
                    max(served_times) if served_times else None,
                    digest,
                    Json({"name": name, "record_type": "preference_pair_jsonl"}),
                    str(output_path),
                ),
            )
            result["snapshot_id"] = cur.fetchone()[0]
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Build an immutable feedback training snapshot.")
    parser.add_argument("--output", default="artifacts/feedback_snapshots/latest.jsonl")
    parser.add_argument("--name", default="feedback_snapshot")
    parser.add_argument("--no-db", action="store_true", help="Do not insert feedback.training_snapshots row.")
    args = parser.parse_args()
    result = build_training_snapshot(output_path=Path(args.output), name=args.name, persist=not args.no_db)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

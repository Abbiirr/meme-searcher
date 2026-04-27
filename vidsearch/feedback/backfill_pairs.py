from __future__ import annotations

import argparse
import json

from vidsearch.feedback.schema import ensure_feedback_schema
from vidsearch.feedback.service import FEATURE_VERSION
from vidsearch.storage import pg as pg_store


def backfill_preference_pairs() -> dict[str, int]:
    """Create deterministic selected-vs-skipped pairs for existing judgments."""
    ensure_feedback_schema()
    selected_seen = 0
    inserted = 0
    with pg_store.get_cursor() as cur:
        cur.execute(
            """SELECT j.judgment_id::text,
                      j.search_id::text,
                      j.impression_id::text,
                      i.image_id
               FROM feedback.judgments j
               JOIN feedback.search_impressions i ON i.impression_id = j.impression_id
               WHERE j.action = 'select'
                 AND j.tombstoned_at IS NULL
                 AND j.impression_id IS NOT NULL
               ORDER BY j.created_at ASC, j.judgment_id ASC"""
        )
        selections = cur.fetchall()
        selected_seen = len(selections)
        for judgment_id, search_id, winner_impression_id, winner_image_id in selections:
            cur.execute(
                """INSERT INTO feedback.preference_pairs
                   (search_id, source_judgment_id, winner_impression_id, loser_impression_id,
                    winner_image_id, loser_image_id, feature_version, derivation_method, pair_weight)
                   SELECT %s, %s, %s, loser.impression_id, %s, loser.image_id, %s, 'selected_vs_skipped', 1.0
                   FROM feedback.search_impressions loser
                   WHERE loser.search_id = %s
                     AND loser.impression_id <> %s::uuid
                   ON CONFLICT DO NOTHING""",
                (
                    search_id,
                    judgment_id,
                    winner_impression_id,
                    winner_image_id,
                    FEATURE_VERSION,
                    search_id,
                    winner_impression_id,
                ),
            )
            inserted += cur.rowcount
    return {"selected_judgments_seen": selected_seen, "pairs_inserted": inserted}


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill deterministic feedback preference pairs.")
    parser.parse_args()
    print(json.dumps(backfill_preference_pairs(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

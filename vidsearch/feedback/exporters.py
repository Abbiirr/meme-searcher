from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(path)
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)
    path.write_text(payload, encoding="utf-8")
    return {
        "path": str(path),
        "rows": len(rows),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _candidate_text(item: dict[str, Any]) -> str:
    return json.dumps(
        {
            "image_id": item["image_id"],
            "rank": item["rank"],
            "base_rank": item["base_rank"],
        },
        sort_keys=True,
    )


def export_feedback_datasets(*, snapshot_path: Path, output_dir: Path) -> dict[str, Any]:
    """Export production/research datasets from an immutable snapshot JSONL."""
    rows = _read_jsonl(snapshot_path)

    ltr_rows: list[dict[str, Any]] = []
    dpo_rows: list[dict[str, Any]] = []
    kto_rows: list[dict[str, Any]] = []
    reward_rows: list[dict[str, Any]] = []

    for row in rows:
        prompt = row["query_redacted"]
        common = {
            "search_id": row["search_id"],
            "intent": row["intent"],
            "feature_version": row["feature_version"],
        }
        if row["record_type"] == "none_correct":
            for candidate in row.get("candidates", []):
                kto_rows.append(
                    common
                    | {
                        "judgment_id": row["judgment_id"],
                        "prompt": prompt,
                        "candidate": _candidate_text(candidate),
                        "desirable": False,
                        "source": "none_correct",
                    }
                )
            continue

        common = common | {"pair_id": row["pair_id"]}
        pair_meta = {
            "derivation_method": row.get("derivation_method", "selected_vs_skipped"),
            "pair_weight": row.get("pair_weight", 1.0),
        }
        ltr_rows.append(common | pair_meta | {"image_id": row["chosen"]["image_id"], "label": 1, "features": row["chosen"]["features"]})
        ltr_rows.append(common | pair_meta | {"image_id": row["rejected"]["image_id"], "label": 0, "features": row["rejected"]["features"]})
        dpo_rows.append(
            common
            | pair_meta
            | {
                "prompt": prompt,
                "chosen": _candidate_text(row["chosen"]),
                "rejected": _candidate_text(row["rejected"]),
            }
        )
        kto_rows.append(common | {"prompt": prompt, "candidate": _candidate_text(row["chosen"]), "desirable": True})
        kto_rows.append(common | {"prompt": prompt, "candidate": _candidate_text(row["rejected"]), "desirable": False})
        reward_rows.append(
            common
            | {
                "query": prompt,
                "chosen_image_id": row["chosen"]["image_id"],
                "rejected_image_id": row["rejected"]["image_id"],
                "chosen_features": row["chosen"]["features"],
                "rejected_features": row["rejected"]["features"],
            }
        )

    return {
        "snapshot": str(snapshot_path),
        "exports": {
            "ltr": _write_jsonl(output_dir / "ltr.jsonl", ltr_rows),
            "dpo": _write_jsonl(output_dir / "dpo.jsonl", dpo_rows),
            "orpo": _write_jsonl(output_dir / "orpo.jsonl", dpo_rows),
            "kto": _write_jsonl(output_dir / "kto.jsonl", kto_rows),
            "reward_pairs": _write_jsonl(output_dir / "reward_pairs.jsonl", reward_rows),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Export feedback snapshot data for LTR/DPO/ORPO/KTO/reward-model experiments.")
    parser.add_argument("--snapshot", default="artifacts/feedback_snapshots/latest.jsonl")
    parser.add_argument("--output-dir", default="artifacts/feedback_exports/latest")
    args = parser.parse_args()
    result = export_feedback_datasets(snapshot_path=Path(args.snapshot), output_dir=Path(args.output_dir))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from vidsearch.feedback.target_benchmark import RESULT_RECORD, TARGET_RECORD


REPORT_RECORD = "target_rank_bucket_report_v1"
BANGLA_RE = re.compile(r"[\u0980-\u09ff]")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _target_language(target: dict[str, Any] | None, prompt: str) -> str:
    metadata = (target or {}).get("metadata_for_reviewer") or {}
    text = " ".join(
        [
            prompt or "",
            metadata.get("ocr_excerpt") or "",
            metadata.get("caption_literal") or "",
            metadata.get("caption_figurative") or "",
            " ".join(str(tag) for tag in metadata.get("tags") or []),
        ]
    )
    return "bangla" if BANGLA_RE.search(text) else "non_bangla"


def rank_bucket(row: dict[str, Any]) -> str:
    if row.get("status") == "target_not_indexed":
        return "target_not_indexed"
    if row.get("status") != "found_selected":
        top_k = int(row.get("top_k") or 0)
        return f"target_not_in_top_{top_k}" if top_k else "target_not_retrieved"

    rank = int(row.get("rank") or 0)
    if rank <= 0:
        return "target_found_rank_unknown"
    if rank == 1:
        return "target_at_rank_1"
    if rank <= 10:
        return "target_in_top_10_not_1"
    if rank <= 20:
        return "target_in_top_20_not_10"
    if rank <= 50:
        return "target_in_top_50_not_20"
    if rank <= 100:
        return "target_in_top_100_not_50"
    return "target_found_after_100"


def _nested_increment(counter: dict[str, Counter[str]], key: str, bucket: str) -> None:
    counter[key][bucket] += 1


def build_rank_bucket_report(*, results_path: Path, pack_path: Path, output_path: Path) -> dict[str, Any]:
    rows = [row for row in _read_jsonl(results_path) if row.get("record_type") == RESULT_RECORD]
    targets = {
        str(row.get("target_id")): row
        for row in _read_jsonl(pack_path)
        if row.get("record_type") == TARGET_RECORD and row.get("target_id")
    }

    by_bucket: Counter[str] = Counter()
    by_prompt_category: dict[str, Counter[str]] = defaultdict(Counter)
    by_language: dict[str, Counter[str]] = defaultdict(Counter)
    by_target: dict[str, Counter[str]] = defaultdict(Counter)
    rank_sum = 0
    found_count = 0
    items: list[dict[str, Any]] = []

    for row in rows:
        bucket = rank_bucket(row)
        target_id = str(row.get("target_id") or "")
        category = str(row.get("prompt_category") or "unspecified")
        language = _target_language(targets.get(target_id), str(row.get("prompt") or ""))
        by_bucket[bucket] += 1
        _nested_increment(by_prompt_category, category, bucket)
        _nested_increment(by_language, language, bucket)
        _nested_increment(by_target, target_id or "unknown", bucket)

        rank = int(row.get("rank") or 0)
        if row.get("status") == "found_selected" and rank > 0:
            rank_sum += rank
            found_count += 1

        items.append(
            {
                "target_id": target_id,
                "target_image_id": row.get("target_image_id"),
                "prompt_id": row.get("prompt_id"),
                "prompt": row.get("prompt"),
                "prompt_category": category,
                "language": language,
                "status": row.get("status"),
                "rank": row.get("rank"),
                "top_k": row.get("top_k"),
                "bucket": bucket,
            }
        )

    useful_ltr = by_bucket["target_in_top_10_not_1"] + by_bucket["target_in_top_20_not_10"]
    rank1 = by_bucket["target_at_rank_1"]
    report = {
        "record_type": REPORT_RECORD,
        "status": "complete",
        "results_path": str(results_path),
        "pack_path": str(pack_path),
        "total_rows": len(rows),
        "found_count": found_count,
        "missing_count": len(rows) - found_count,
        "average_found_rank": (rank_sum / found_count) if found_count else None,
        "useful_ltr_examples": useful_ltr,
        "rank_1_examples": rank1,
        "rank_1_share_of_found": (rank1 / found_count) if found_count else None,
        "bucket_counts": dict(sorted(by_bucket.items())),
        "bucket_counts_by_prompt_category": {key: dict(sorted(value.items())) for key, value in sorted(by_prompt_category.items())},
        "bucket_counts_by_language": {key: dict(sorted(value.items())) for key, value in sorted(by_language.items())},
        "bucket_counts_by_target_id": {key: dict(sorted(value.items())) for key, value in sorted(by_target.items())},
        "items": items,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize target replay ranks into RLHF rank buckets.")
    parser.add_argument("--results", default="artifacts/feedback_targets/full_metadata_results.jsonl")
    parser.add_argument("--pack", default="artifacts/feedback_targets/target_pack.jsonl")
    parser.add_argument("--output", default="artifacts/feedback_targets/full_metadata_rank_buckets.json")
    args = parser.parse_args()

    report = build_rank_bucket_report(
        results_path=Path(args.results),
        pack_path=Path(args.pack),
        output_path=Path(args.output),
    )
    print(json.dumps({k: v for k, v in report.items() if k != "items"}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

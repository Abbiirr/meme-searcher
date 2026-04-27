from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from vidsearch.feedback.target_benchmark import MISSING_RECORD, TARGET_RECORD


ANALYSIS_RECORD = "target_miss_analysis_v1"
BANGLA_RE = re.compile(r"[\u0980-\u09ff]")
TOKEN_RE = re.compile(r"[\w\u0980-\u09ff]+", re.UNICODE)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _tokens(text: str) -> set[str]:
    return {token.lower() for token in TOKEN_RE.findall(text or "") if len(token) > 2}


def _basename(value: str) -> str:
    return re.split(r"[\\/]", value or "")[-1].lower()


def _path_similarity(target_path: str, candidates: list[str]) -> float:
    target_name = _basename(target_path)
    if not target_name:
        return 0.0
    return max((SequenceMatcher(None, target_name, _basename(candidate)).ratio() for candidate in candidates), default=0.0)


def _metadata_text(target: dict[str, Any]) -> str:
    metadata = target.get("metadata_for_reviewer") or {}
    fields = [
        metadata.get("ocr_excerpt") or "",
        metadata.get("caption_literal") or "",
        metadata.get("caption_figurative") or "",
        metadata.get("template_name") or "",
        " ".join(str(tag) for tag in metadata.get("tags") or []),
    ]
    return " ".join(fields)


def classify_miss(miss: dict[str, Any], target: dict[str, Any] | None) -> tuple[str, str, dict[str, Any]]:
    target = target or {}
    prompt = str(miss.get("prompt") or "")
    top_source_uris = [str(value) for value in miss.get("top_source_uris") or []]
    metadata_text = _metadata_text(target)
    target_path = str(target.get("indexed_source_uri") or target.get("target_path") or miss.get("target_path") or "")

    target_has_bangla = bool(BANGLA_RE.search(metadata_text))
    prompt_has_bangla = bool(BANGLA_RE.search(prompt))
    filename_similarity = _path_similarity(target_path, top_source_uris)
    prompt_tokens = _tokens(prompt)
    metadata_tokens = _tokens(metadata_text)
    overlap = len(prompt_tokens & metadata_tokens)
    overlap_ratio = overlap / max(len(prompt_tokens), 1)

    details = {
        "target_has_bangla": target_has_bangla,
        "prompt_has_bangla": prompt_has_bangla,
        "filename_similarity_to_top_hit": round(filename_similarity, 4),
        "prompt_metadata_overlap_ratio": round(overlap_ratio, 4),
        "prompt_token_count": len(prompt_tokens),
        "metadata_token_count": len(metadata_tokens),
    }

    if str(miss.get("failure_type")) == "target_not_indexed":
        return (
            "target_not_indexed",
            "Ingest/index parity must be repaired before this target can be learned from.",
            details,
        )

    if filename_similarity >= 0.86:
        return (
            "near_duplicate_or_filename_variant_confusion",
            "Inspect the near-duplicate returned in the top slate; add duplicate-family metadata or canonicalize variants.",
            details,
        )

    if target_has_bangla and not prompt_has_bangla:
        return (
            "bangla_metadata_under_prompted",
            "Generate a Bangla-script prompt and an English transliteration/paraphrase for this target, then replay.",
            details,
        )

    if len(prompt_tokens) <= 3 or overlap_ratio < 0.15:
        return (
            "prompt_metadata_gap",
            "Regenerate the target prompt from visible OCR/caption metadata; the current prompt is too sparse or off-target.",
            details,
        )

    return (
        "retrieval_or_rerank_recall_gap",
        "Target metadata appears related to the prompt but the base slate missed it; prioritize retrieval feature/OCR/caption investigation.",
        details,
    )


def analyze_target_misses(*, misses_path: Path, pack_path: Path, output_path: Path) -> dict[str, Any]:
    misses = [row for row in _read_jsonl(misses_path) if row.get("record_type") == MISSING_RECORD]
    targets = {
        str(row.get("target_id")): row
        for row in _read_jsonl(pack_path)
        if row.get("record_type") == TARGET_RECORD and row.get("target_id")
    }

    items: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for miss in misses:
        target = targets.get(str(miss.get("target_id")))
        failure_class, recommended_fix, details = classify_miss(miss, target)
        counts[failure_class] += 1
        items.append(
            {
                "record_type": ANALYSIS_RECORD,
                "target_id": miss.get("target_id"),
                "target_image_id": miss.get("target_image_id"),
                "target_path": miss.get("target_path"),
                "prompt_id": miss.get("prompt_id"),
                "prompt": miss.get("prompt"),
                "search_id": miss.get("search_id"),
                "failure_type": miss.get("failure_type"),
                "failure_class": failure_class,
                "recommended_fix": recommended_fix,
                "details": details,
                "top_source_uris": miss.get("top_source_uris") or [],
            }
        )

    report = {
        "record_type": "target_miss_analysis_report_v1",
        "status": "complete",
        "misses_path": str(misses_path),
        "pack_path": str(pack_path),
        "total_misses": len(items),
        "failure_class_counts": dict(sorted(counts.items())),
        "items": items,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Classify target-search misses from an RLHF replay.")
    parser.add_argument("--misses", default="artifacts/feedback_targets/full_metadata_target_not_found.jsonl")
    parser.add_argument("--pack", default="artifacts/feedback_targets/target_pack.jsonl")
    parser.add_argument("--output", default="artifacts/feedback_targets/full_metadata_target_not_found_analysis.json")
    args = parser.parse_args()

    result = analyze_target_misses(
        misses_path=Path(args.misses),
        pack_path=Path(args.pack),
        output_path=Path(args.output),
    )
    print(json.dumps({k: v for k, v in result.items() if k != "items"}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


TARGET_RECORD = "target_image_task_v1"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows) + ("\n" if rows else ""), encoding="utf-8")


def _field(row: dict[str, Any], name: str) -> str:
    if name == "target_id":
        return str(row.get("target_id") or "")
    metadata = row.get("metadata_for_reviewer") or {}
    if name == "template_family":
        return str(row.get("template_family") or metadata.get("template_name") or "template_unknown")
    if name == "near_duplicate_cluster":
        return str(row.get("near_duplicate_cluster") or row.get("sha256") or row.get("target_id") or "")
    if name == "language":
        text = " ".join(
            str(metadata.get(key) or "")
            for key in ("ocr_excerpt", "caption_literal", "caption_figurative", "template_name")
        )
        return "bn" if any("\u0980" <= char <= "\u09ff" for char in text) else str(row.get("language") or "unknown")
    return str(row.get(name) or metadata.get(name) or "")


def group_key(row: dict[str, Any], group_by: list[str]) -> str:
    parts = [_field(row, field.strip()) for field in group_by if field.strip()]
    return "|".join(parts) or str(row.get("target_id") or row.get("sha256") or "")


def _group_sort_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _take_groups(groups: list[list[dict[str, Any]]], target_count: int) -> tuple[list[list[dict[str, Any]]], list[list[dict[str, Any]]]]:
    selected: list[list[dict[str, Any]]] = []
    total = 0
    while groups and total < target_count:
        group = groups.pop(0)
        selected.append(group)
        total += len(group)
    return selected, groups


def _flatten(groups: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    return [row for group in groups for row in group]


def _summary(rows_by_split: dict[str, list[dict[str, Any]]], group_by: list[str]) -> str:
    lines = ["# R2 Target Split Summary", "", f"Group fields: `{', '.join(group_by)}`", ""]
    lines.append("| Split | Targets | Languages | Template families |")
    lines.append("| --- | ---: | --- | --- |")
    for split, rows in rows_by_split.items():
        languages = Counter(_field(row, "language") for row in rows)
        templates = Counter(_field(row, "template_family") for row in rows)
        lines.append(
            f"| `{split}` | `{len(rows)}` | "
            f"{', '.join(f'{k}:{v}' for k, v in sorted(languages.items())) or '-'} | "
            f"{', '.join(f'{k}:{v}' for k, v in sorted(templates.items())[:8]) or '-'} |"
        )
    lines.append("")
    lines.append("All rows for a target/group key are kept in one split. Raw JSONL artifacts remain under `artifacts/` and are not committed.")
    return "\n".join(lines) + "\n"


def build_splits(
    *,
    pack_path: Path,
    output_dir: Path,
    train_count: int,
    val_count: int,
    holdout_count: int,
    group_by: list[str],
    summary_path: Path | None = None,
) -> dict[str, Any]:
    rows = [row for row in read_jsonl(pack_path) if row.get("record_type") == TARGET_RECORD]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[group_key(row, group_by)].append(row)

    groups = [grouped[key] for key in sorted(grouped, key=_group_sort_key)]
    train_groups, groups = _take_groups(groups, train_count)
    val_groups, groups = _take_groups(groups, val_count)
    holdout_groups, groups = _take_groups(groups, holdout_count)

    rows_by_split = {
        "train": _flatten(train_groups),
        "val": _flatten(val_groups),
        "holdout": _flatten(holdout_groups),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    for split, split_rows in rows_by_split.items():
        write_jsonl(output_dir / f"{split}_pack.jsonl", split_rows)

    summary_path = summary_path or Path("docs/experiments/results/R2_TARGET_SPLIT_SUMMARY.md")
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(_summary(rows_by_split, group_by), encoding="utf-8")
    return {
        "status": "written",
        "pack": str(pack_path),
        "output_dir": str(output_dir),
        "summary": str(summary_path),
        "counts": {split: len(split_rows) for split, split_rows in rows_by_split.items()},
        "unassigned_targets": sum(len(group) for group in groups),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build leakage-safe R2 target splits.")
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build-splits")
    build.add_argument("--pack", required=True)
    build.add_argument("--output-dir", required=True)
    build.add_argument("--train-count", type=int, required=True)
    build.add_argument("--val-count", type=int, required=True)
    build.add_argument("--holdout-count", type=int, required=True)
    build.add_argument("--group-by", default="target_id,template_family,near_duplicate_cluster,language")
    build.add_argument("--summary", default="docs/experiments/results/R2_TARGET_SPLIT_SUMMARY.md")
    args = parser.parse_args()

    result = build_splits(
        pack_path=Path(args.pack),
        output_dir=Path(args.output_dir),
        train_count=args.train_count,
        val_count=args.val_count,
        holdout_count=args.holdout_count,
        group_by=[part.strip() for part in args.group_by.split(",")],
        summary_path=Path(args.summary),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

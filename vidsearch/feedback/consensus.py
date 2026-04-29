from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from pathlib import Path
from typing import Any


CONSENSUS_RECORD = "r2_consensus_label_v1"
FOUND_VERDICTS = {"exact_target_found", "near_duplicate_found"}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows) + ("\n" if rows else ""), encoding="utf-8")


def consensus_label(rows: list[dict[str, Any]]) -> dict[str, Any]:
    first = rows[0]
    valid = [row for row in rows if float(row.get("confidence") or 0.0) >= 0.70]
    found = [row for row in valid if row.get("verdict") in FOUND_VERDICTS]
    found_ids = {row.get("selected_candidate_blind_id") for row in found if row.get("selected_candidate_blind_id")}
    if not valid:
        label = "uncertain"
        reason = "all judgments below confidence threshold"
    elif len(found) >= 2 and len(found_ids) == 1:
        label = "target_found"
        reason = "two or more judges/permutations agree on the same blind candidate"
    elif all(row.get("verdict") == "not_found" for row in valid):
        label = "target_not_found"
        reason = "all confident judgments say not_found"
    else:
        label = "uncertain"
        reason = "judge disagreement or duplicate ambiguity"
    return {
        "record_type": CONSENSUS_RECORD,
        "prompt_id": first.get("prompt_id"),
        "target_id": first.get("target_id"),
        "label": label,
        "accepted_for_training": label == "target_found",
        "selected_candidate_blind_id": next(iter(found_ids)) if len(found_ids) == 1 else None,
        "source_judgment_count": len(rows),
        "reason": reason,
    }


def build_consensus(*, judgment_paths: list[Path], output_path: Path) -> dict[str, Any]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for path in judgment_paths:
        for row in read_jsonl(path):
            grouped[(str(row.get("prompt_id")), str(row.get("target_id")))].append(row)
    labels = [consensus_label(rows) for _, rows in sorted(grouped.items())]
    write_jsonl(output_path, labels)
    counts: dict[str, int] = defaultdict(int)
    for label in labels:
        counts[str(label["label"])] += 1
    return {"status": "written", "labels": len(labels), "counts": dict(sorted(counts.items())), "output": str(output_path)}


def sample_human_audit(*, labels_path: Path, output_path: Path, per_intent: int = 50, seed: int = 20260427) -> dict[str, Any]:
    labels = read_jsonl(labels_path)
    rng = random.Random(seed)
    rng.shuffle(labels)
    sample = labels[:per_intent]
    write_jsonl(output_path, sample)
    return {"status": "written", "sampled": len(sample), "output": str(output_path)}


def summarize_audit(*, labels_path: Path, human_labels_path: Path, output_path: Path) -> dict[str, Any]:
    labels = {str(row.get("prompt_id")): row for row in read_jsonl(labels_path)}
    humans = read_jsonl(human_labels_path)
    total = 0
    agree = 0
    for row in humans:
        prompt_id = str(row.get("prompt_id"))
        if prompt_id not in labels:
            continue
        total += 1
        agree += 1 if row.get("label") == labels[prompt_id].get("label") else 0
    agreement = (agree / total) if total else None
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        "# R2 Judge Audit Summary\n\n"
        f"Audited rows: `{total}`\n\n"
        f"AI-human agreement: `{agreement}`\n\n"
        "Labels are diagnostic unless the full audit thresholds pass.\n",
        encoding="utf-8",
    )
    return {"status": "written", "audited": total, "agreement": agreement, "output": str(output_path)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build conservative R2 consensus labels.")
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    build.add_argument("--judgments", action="append", required=True)
    build.add_argument("--output", required=True)
    audit = sub.add_parser("sample-human-audit")
    audit.add_argument("--labels", required=True)
    audit.add_argument("--output", required=True)
    audit.add_argument("--per-intent", type=int, default=50)
    summarize = sub.add_parser("summarize-audit")
    summarize.add_argument("--labels", required=True)
    summarize.add_argument("--human-labels", required=True)
    summarize.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.command == "build":
        result = build_consensus(judgment_paths=[Path(path) for path in args.judgments], output_path=Path(args.output))
    elif args.command == "sample-human-audit":
        result = sample_human_audit(labels_path=Path(args.labels), output_path=Path(args.output), per_intent=args.per_intent)
    else:
        result = summarize_audit(labels_path=Path(args.labels), human_labels_path=Path(args.human_labels), output_path=Path(args.output))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

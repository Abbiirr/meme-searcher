from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any


JUDGMENT_RECORD = "ai_target_judgment_v1"
VERDICTS = {
    "exact_target_found",
    "near_duplicate_found",
    "semantically_relevant_but_not_target",
    "not_found",
    "prompt_bad",
    "uncertain",
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows) + ("\n" if rows else ""), encoding="utf-8")


def randomized_candidates(hits: list[dict[str, Any]], *, seed: int) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    candidates = [dict(hit) for hit in hits]
    rng.shuffle(candidates)
    for index, candidate in enumerate(candidates, start=1):
        candidate["blind_id"] = f"C{index:02d}"
        for forbidden in ("rank", "base_rank", "retrieval_score", "rerank_score", "learned_score", "image_id", "source_uri"):
            candidate.pop(forbidden, None)
    return candidates


def validate_judgment(row: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if row.get("record_type") != JUDGMENT_RECORD:
        errors.append("wrong record_type")
    if row.get("verdict") not in VERDICTS:
        errors.append("invalid verdict")
    confidence = row.get("confidence")
    if not isinstance(confidence, int | float) or not (0 <= float(confidence) <= 1):
        errors.append("confidence must be between 0 and 1")
    if row.get("verdict") in {"exact_target_found", "near_duplicate_found"} and not row.get("selected_candidate_blind_id"):
        errors.append("target-found verdict requires selected_candidate_blind_id")
    return errors


def judge_target_slates_id_match(
    *,
    results_path: Path,
    output_path: Path,
    judge_model: str,
    repeat_permutations: int = 2,
    seed: int = 20260427,
) -> dict[str, Any]:
    rows = [row for row in read_jsonl(results_path) if row.get("record_type") == "target_search_result_v1"]
    judgments: list[dict[str, Any]] = []
    for row_index, row in enumerate(rows):
        top_ids = [str(image_id) for image_id in row.get("top_image_ids") or []]
        target_image_id = str(row.get("target_image_id") or "")
        for permutation in range(repeat_permutations):
            order_seed = seed + row_index * 1009 + permutation
            shuffled = list(top_ids)
            random.Random(order_seed).shuffle(shuffled)
            selected = None
            if target_image_id and target_image_id in shuffled:
                selected = f"C{shuffled.index(target_image_id) + 1:02d}"
            judgments.append(
                {
                    "record_type": JUDGMENT_RECORD,
                    "prompt_id": row.get("prompt_id"),
                    "target_id": row.get("target_id"),
                    "judge_model": judge_model,
                    "judge_role": "deterministic_id_oracle",
                    "candidate_order_seed": order_seed,
                    "verdict": "exact_target_found" if selected else "not_found",
                    "selected_candidate_index": (int(selected[1:]) if selected else None),
                    "selected_candidate_blind_id": selected,
                    "confidence": 1.0,
                    "evidence": {
                        "visual_match": 1.0 if selected else 0.0,
                        "ocr_match": 0.0,
                        "semantic_match": 0.0,
                        "template_match": 0.0,
                    },
                    "short_reason": "Deterministic target image ID check over a shuffled slate.",
                }
            )
    write_jsonl(output_path, judgments)
    return {"status": "written", "judgments": len(judgments), "output": str(output_path)}


def validate_judgments(path: Path) -> dict[str, Any]:
    rows = read_jsonl(path)
    invalid = [{"index": index, "errors": validate_judgment(row)} for index, row in enumerate(rows, start=1) if validate_judgment(row)]
    return {"status": "failed" if invalid else "passed", "rows": len(rows), "invalid": invalid[:50]}


def summarize_judge_bias(path: Path) -> dict[str, Any]:
    rows = [row for row in read_jsonl(path) if row.get("record_type") == JUDGMENT_RECORD]
    by_prompt: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_prompt.setdefault(str(row.get("prompt_id")), []).append(row)
    stable = 0
    checked = 0
    for group in by_prompt.values():
        found = [row.get("selected_candidate_blind_id") for row in group if row.get("selected_candidate_blind_id")]
        if len(group) >= 2:
            checked += 1
            stable += 1 if len(set(found)) <= 1 else 0
    return {
        "status": "summarized",
        "judgments": len(rows),
        "prompt_groups": len(by_prompt),
        "position_consistency": (stable / checked) if checked else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="R2 AI judge schema and deterministic ID-match scaffold.")
    sub = parser.add_subparsers(dest="command", required=True)
    judge = sub.add_parser("judge-target-slates")
    judge.add_argument("--results", required=True)
    judge.add_argument("--output", required=True)
    judge.add_argument("--judge-model", required=True)
    judge.add_argument("--shuffle-candidates", action="store_true")
    judge.add_argument("--repeat-permutations", type=int, default=2)
    judge.add_argument("--seed", type=int, default=20260427)
    validate = sub.add_parser("validate-judgments")
    validate.add_argument("--judgments", required=True)
    summary = sub.add_parser("summarize-judge-bias")
    summary.add_argument("--judgments", required=True)
    args = parser.parse_args()
    if args.command == "judge-target-slates":
        result = judge_target_slates_id_match(
            results_path=Path(args.results),
            output_path=Path(args.output),
            judge_model=args.judge_model,
            repeat_permutations=args.repeat_permutations,
            seed=args.seed,
        )
    elif args.command == "validate-judgments":
        result = validate_judgments(Path(args.judgments))
    else:
        result = summarize_judge_bias(Path(args.judgments))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("status") != "failed" else 2


if __name__ == "__main__":
    raise SystemExit(main())

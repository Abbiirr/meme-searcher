from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import httpx


PROMPT_RECORDS = {"target_prompt_label_v1", "target_prompt_label_v2"}
CANONICAL_CATEGORIES = {
    "exact_text",
    "fuzzy_text",
    "semantic_description",
    "mixed_visual_description",
    "short_sloppy",
    "multilingual",
}
ALIASES = {
    "exact_memory": "exact_text",
    "paraphrase": "fuzzy_text",
    "emotion": "semantic_description",
    "topic": "semantic_description",
    "named_entity": "mixed_visual_description",
}
LEAK_RE = re.compile(r"(?:target-[0-9a-f]{6,}|holdout-[0-9a-f]{6,}|[A-Za-z]:\\|/data/|\.jpe?g|\.png|sha256|image_id)", re.I)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows) + ("\n" if rows else ""), encoding="utf-8")


def normalize_category(value: str | None) -> str:
    raw = (value or "semantic_description").strip()
    return ALIASES.get(raw, raw if raw in CANONICAL_CATEGORIES else "semantic_description")


def infer_language(prompt: str, row: dict[str, Any]) -> str:
    if row.get("language"):
        return str(row["language"])
    has_bn = any("\u0980" <= char <= "\u09ff" for char in prompt)
    if has_bn and re.search(r"[A-Za-z]", prompt):
        return "mixed"
    if has_bn:
        return "bn"
    return "en" if re.search(r"[A-Za-z]", prompt) else "unknown"


def validate_prompt_balance(
    *,
    prompts_path: Path,
    output_path: Path | None = None,
    minimums: dict[str, int] | None = None,
) -> dict[str, Any]:
    minimums = minimums or {}
    rows = [row for row in read_jsonl(prompts_path) if row.get("record_type") in PROMPT_RECORDS]
    counts: Counter[str] = Counter()
    languages: Counter[str] = Counter()
    leaks: list[dict[str, Any]] = []
    duplicates: list[str] = []
    seen_prompts: set[str] = set()

    for row in rows:
        prompt = str(row.get("prompt") or "").strip()
        category = normalize_category(str(row.get("category") or ""))
        counts[category] += 1
        languages[infer_language(prompt, row)] += 1
        lowered = prompt.lower()
        if lowered in seen_prompts:
            duplicates.append(str(row.get("prompt_id") or prompt))
        seen_prompts.add(lowered)
        if LEAK_RE.search(prompt):
            leaks.append({"prompt_id": row.get("prompt_id"), "prompt": prompt})

    failures = []
    for category, minimum in minimums.items():
        if counts[category] < minimum:
            failures.append(f"{category} prompts {counts[category]} < {minimum}")
    if leaks:
        failures.append(f"answer-leaking prompts detected: {len(leaks)}")

    report = {
        "status": "failed" if failures else "passed",
        "prompt_rows": len(rows),
        "counts_by_category": dict(sorted(counts.items())),
        "counts_by_language": dict(sorted(languages.items())),
        "duplicate_prompt_ids": duplicates[:50],
        "leak_examples": leaks[:20],
        "failures": failures,
    }
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        lines = ["# R2 Prompt Balance Summary", ""]
        lines.append(f"Status: `{report['status']}`")
        lines.append("")
        lines.append("| Category | Count | Required |")
        lines.append("| --- | ---: | ---: |")
        for category in sorted(CANONICAL_CATEGORIES):
            lines.append(f"| `{category}` | `{counts[category]}` | `{minimums.get(category, 0)}` |")
        lines.append("")
        lines.append("| Language | Count |")
        lines.append("| --- | ---: |")
        for language, count in sorted(languages.items()):
            lines.append(f"| `{language}` | `{count}` |")
        if failures:
            lines.append("")
            lines.append("Failures:")
            for failure in failures:
                lines.append(f"- {failure}")
        output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def _target_metadata(target: dict[str, Any]) -> dict[str, Any]:
    metadata = target.get("metadata_for_reviewer") or {}
    return {
        "ocr_excerpt": metadata.get("ocr_excerpt") or "",
        "caption_literal": metadata.get("caption_literal") or "",
        "caption_figurative": metadata.get("caption_figurative") or "",
        "template_name": metadata.get("template_name") or "",
        "tags": list(metadata.get("tags") or []),
    }


def _next_prompt_id(existing_rows: list[dict[str, Any]], target_id: str) -> str:
    max_index = 0
    prefix = f"{target_id}:p"
    for row in existing_rows:
        prompt_id = str(row.get("prompt_id") or "")
        if prompt_id.startswith(prefix):
            try:
                max_index = max(max_index, int(prompt_id.rsplit("p", 1)[1]))
            except ValueError:
                continue
    return f"{target_id}:p{max_index + 1}"


def _augment_prompt_text(target: dict[str, Any], category: str) -> str:
    category_guidance = {
        "fuzzy_text": "Write one prompt that paraphrases or partially misremembers visible meme text. It must not quote all visible text exactly.",
        "mixed_visual_description": "Write one prompt combining the visual scene/template with the meme meaning or remembered text.",
        "exact_text": "Write one prompt using exact visible text only if a user would naturally remember it.",
        "semantic_description": "Write one prompt about the situation, joke, emotion, or meaning without relying only on exact text.",
    }
    return f"""Write exactly one additional natural user search prompt for this meme.

Required category: {category}
Guidance: {category_guidance.get(category, 'Write a natural user search prompt.')}

Rules:
- Return JSON only: {{"prompt":"...","language":"en|bn|mixed|unknown","rationale":"..."}}
- Do not mention filename, path, sha256, target id, image id, database fields, or metadata.
- The prompt should sound like a user trying to retrieve the exact meme.

Meme metadata:
{json.dumps(_target_metadata(target), ensure_ascii=False, indent=2)}
"""


def _parse_single_prompt(text: str) -> dict[str, str] | None:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        cleaned = cleaned[start : end + 1]
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        return None
    prompt = str(parsed.get("prompt") or "").strip()
    if not prompt or LEAK_RE.search(prompt):
        return None
    return {
        "prompt": prompt,
        "language": str(parsed.get("language") or "unknown"),
        "rationale": str(parsed.get("rationale") or "Generated to repair R2 prompt balance."),
    }


def augment_prompt_balance_metadata_gateway(
    *,
    pack_path: Path,
    prompts_path: Path,
    output_path: Path,
    model: str,
    gateway_url: str,
    api_key: str,
    minimums: dict[str, int],
    max_attempts: int = 400,
    timeout_seconds: float = 90.0,
) -> dict[str, Any]:
    targets = [row for row in read_jsonl(pack_path) if row.get("record_type") == "target_image_task_v1"]
    rows = [row for row in read_jsonl(prompts_path) if row.get("record_type") in PROMPT_RECORDS]
    counts = Counter(normalize_category(str(row.get("category") or "")) for row in rows)
    deficits = {category: max(0, minimum - counts[category]) for category, minimum in minimums.items()}
    if not targets:
        return {"status": "failed", "errors": ["no target rows available"]}

    chat_url = f"{gateway_url.rstrip('/')}/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    generated = 0
    errors: list[str] = []
    attempts = 0
    target_index = 0

    while any(value > 0 for value in deficits.values()) and attempts < max_attempts:
        category = max(deficits, key=lambda key: deficits[key])
        if deficits[category] <= 0:
            break
        target = targets[target_index % len(targets)]
        target_index += 1
        attempts += 1
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": _augment_prompt_text(target, category)}],
            "temperature": 0.3,
            "response_format": {"type": "json_object"},
        }
        try:
            response = httpx.post(chat_url, json=payload, headers=headers, timeout=timeout_seconds)
            response.raise_for_status()
            response_text = str(response.json()["choices"][0]["message"].get("content") or "")
            parsed = _parse_single_prompt(response_text)
            if not parsed:
                continue
            target_id = str(target["target_id"])
            row = {
                "record_type": "target_prompt_label_v2",
                "target_id": target_id,
                "prompt_id": _next_prompt_id(rows, target_id),
                "prompt": parsed["prompt"],
                "category": category,
                "language": parsed["language"],
                "uses_visible_text": category in {"exact_text", "fuzzy_text"},
                "expected_difficulty": "medium",
                "operator_model": model,
                "operator_role": "prompt_balance_augmenter",
                "source_modality": "metadata",
                "rationale": parsed["rationale"],
            }
            rows.append(row)
            counts[category] += 1
            deficits[category] -= 1
            generated += 1
            write_jsonl(output_path, rows)
        except Exception as exc:  # pragma: no cover - gateway behavior is environment-specific.
            errors.append(str(exc))
    write_jsonl(output_path, rows)
    return {
        "status": "complete" if not any(value > 0 for value in deficits.values()) else "failed",
        "generated": generated,
        "attempts": attempts,
        "deficits_remaining": deficits,
        "errors": errors[:20],
        "output": str(output_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate R2 prompt balance and leakage.")
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("--prompts", required=True)
    validate.add_argument("--output", required=True)
    validate.add_argument("--min-exact", type=int, default=200)
    validate.add_argument("--min-fuzzy", type=int, default=200)
    validate.add_argument("--min-semantic", type=int, default=200)
    validate.add_argument("--min-mixed", type=int, default=200)
    augment = sub.add_parser("augment-metadata-gateway")
    augment.add_argument("--pack", required=True)
    augment.add_argument("--prompts", required=True)
    augment.add_argument("--output", required=True)
    augment.add_argument("--model", default="fast")
    augment.add_argument("--gateway-url", default="http://127.0.0.1:4100")
    augment.add_argument("--api-key", required=True)
    augment.add_argument("--min-exact", type=int, default=200)
    augment.add_argument("--min-fuzzy", type=int, default=200)
    augment.add_argument("--min-semantic", type=int, default=200)
    augment.add_argument("--min-mixed", type=int, default=200)
    augment.add_argument("--max-attempts", type=int, default=400)
    augment.add_argument("--timeout-seconds", type=float, default=90.0)
    args = parser.parse_args()
    minimums = {
        "exact_text": args.min_exact,
        "fuzzy_text": args.min_fuzzy,
        "semantic_description": args.min_semantic,
        "mixed_visual_description": args.min_mixed,
    }
    if args.command == "validate":
        result = validate_prompt_balance(
            prompts_path=Path(args.prompts),
            output_path=Path(args.output),
            minimums=minimums,
        )
    else:
        result = augment_prompt_balance_metadata_gateway(
            pack_path=Path(args.pack),
            prompts_path=Path(args.prompts),
            output_path=Path(args.output),
            model=args.model,
            gateway_url=args.gateway_url,
            api_key=args.api_key,
            minimums=minimums,
            max_attempts=args.max_attempts,
            timeout_seconds=args.timeout_seconds,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] in {"passed", "complete"} else 2


if __name__ == "__main__":
    raise SystemExit(main())

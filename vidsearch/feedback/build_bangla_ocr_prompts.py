from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from vidsearch.storage.pg import get_cursor


PROMPT_RECORD = "target_prompt_label_v1"


def build_bangla_ocr_prompts(*, analysis_path: Path, output_path: Path, max_words: int = 10) -> dict[str, Any]:
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    items = [item for item in analysis.get("items") or [] if item.get("failure_class") == "bangla_metadata_under_prompted"]
    image_ids = [str(item["target_image_id"]) for item in items if item.get("target_image_id")]
    with get_cursor() as cur:
        cur.execute(
            "SELECT image_id, COALESCE(ocr_full_text, '') FROM core.image_items WHERE image_id = ANY(%s)",
            (image_ids,),
        )
        ocr_by_id = {row[0]: row[1] for row in cur.fetchall()}

    rows = []
    for item in items:
        ocr = " ".join((ocr_by_id.get(str(item.get("target_image_id"))) or "").split())
        phrase = " ".join(ocr.split()[:max_words]) or str(item.get("prompt") or "")
        target_id = str(item["target_id"])
        rows.append(
            {
                "record_type": PROMPT_RECORD,
                "target_id": target_id,
                "target_image_id": item.get("target_image_id"),
                "prompt_id": f"{target_id}:bangla-exact-postfix",
                "prompt": f"এই লেখা থাকা মিম {phrase}".strip(),
                "category": "multilingual",
                "rationale": "Post-fix Bangla-script exact OCR prompt for confirmed Bangla target pickup miss.",
            }
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    return {"status": "written", "written": len(rows), "output": str(output_path)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Bangla-script OCR prompts from target miss analysis.")
    parser.add_argument("--analysis", default="artifacts/feedback_targets/full_metadata_after_candidate_floor_misses_analysis.json")
    parser.add_argument("--output", default="artifacts/feedback_targets/full_metadata_after_candidate_floor_bangla_exact_prompts.jsonl")
    parser.add_argument("--max-words", type=int, default=10)
    args = parser.parse_args()
    result = build_bangla_ocr_prompts(analysis_path=Path(args.analysis), output_path=Path(args.output), max_words=args.max_words)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

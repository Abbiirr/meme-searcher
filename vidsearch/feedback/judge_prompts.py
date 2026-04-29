from __future__ import annotations

import json
from typing import Any


JUDGE_SYSTEM_PROMPT = """You are judging whether a specific target meme appears in a randomized candidate slate.

Return JSON only. Do not infer from original rank or score; they are intentionally hidden.
Use `uncertain` if the target and candidate are ambiguous near-duplicates.
"""


def build_judge_user_prompt(*, query: str, target_summary: dict[str, Any], candidates: list[dict[str, Any]]) -> str:
    public_candidates = [
        {
            "blind_id": candidate["blind_id"],
            "ocr_excerpt": candidate.get("ocr_excerpt") or "",
            "caption_literal": candidate.get("caption_literal") or "",
            "caption_figurative": candidate.get("caption_figurative") or "",
            "template_name": candidate.get("template_name") or "",
            "tags": list(candidate.get("tags") or []),
        }
        for candidate in candidates
    ]
    payload = {
        "query_prompt": query,
        "target_public_summary": target_summary,
        "randomized_candidates": public_candidates,
        "allowed_verdicts": [
            "exact_target_found",
            "near_duplicate_found",
            "semantically_relevant_but_not_target",
            "not_found",
            "prompt_bad",
            "uncertain",
        ],
        "schema": {
            "verdict": "one allowed verdict",
            "selected_candidate_blind_id": "C01 or null",
            "confidence": "0.0 to 1.0",
            "evidence": {
                "visual_match": "0.0 to 1.0",
                "ocr_match": "0.0 to 1.0",
                "semantic_match": "0.0 to 1.0",
                "template_match": "0.0 to 1.0",
            },
            "short_reason": "brief reason",
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)

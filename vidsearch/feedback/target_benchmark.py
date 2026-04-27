from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import random
import re
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx
from PIL import Image, ImageOps

from vidsearch.config import SUPPORTED_EXTENSIONS
from vidsearch.feedback.agent_operator import (
    _count_sessions_for_prefix,
    _delete_sessions_for_prefix,
    _read_jsonl,
    _token_from_feedback_url,
    _write_jsonl,
)
from vidsearch.feedback.service import record_judgment_from_token
from vidsearch.storage import pg as pg_store


TARGET_RECORD = "target_image_task_v1"
PROMPT_RECORD = "target_prompt_label_v1"
RESULT_RECORD = "target_search_result_v1"
MISSING_RECORD = "target_not_found_v1"
DEFAULT_API_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_GATEWAY_URL = os.environ.get("LITELLM_URL", "http://127.0.0.1:4100")
DEFAULT_GATEWAY_MODEL = os.environ.get("VIDSEARCH_AGENT_LABEL_MODEL", "qwen3.6-vlm-wrapper")
DEFAULT_GATEWAY_TEXT_MODEL = os.environ.get("VIDSEARCH_AGENT_TEXT_LABEL_MODEL", "fast")
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_VLM_MODEL = "maternion/Qianfan-OCR:4b"


def _json_default(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _safe_id(value: str, *, max_length: int = 96) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.:-]+", "-", value.strip())
    safe = safe.strip("-")
    return (safe or "item")[:max_length]


def _session_id(prefix: str, prompt_id: str) -> str:
    digest = hashlib.sha256(prompt_id.encode("utf-8")).hexdigest()[:16]
    base = f"{prefix}-{_safe_id(prompt_id, max_length=64)}-{digest}"
    return base[:128]


def _model_family(model: str) -> str:
    lowered = model.lower()
    if "qwen" in lowered or "qianfan" in lowered:
        return "qwen"
    if "claude" in lowered:
        return "claude"
    if "gpt" in lowered or "openai" in lowered:
        return "openai"
    if "gemini" in lowered:
        return "gemini"
    if "llama" in lowered:
        return "llama"
    if "mistral" in lowered:
        return "mistral"
    if "fast" == lowered:
        return "gateway-default"
    return "unknown"


def _model_fingerprint(*, provider: str, model: str, endpoint: str | None = None, mode: str) -> dict[str, str]:
    return {
        "provider": provider,
        "model": model,
        "family": _model_family(model),
        "endpoint": endpoint or "",
        "mode": mode,
    }


def _sha256_file(path: Path) -> tuple[str, bytes]:
    digest = hashlib.sha256(path.read_bytes()).digest()
    return digest.hex(), digest


def _image_base64_for_vlm(path: Path) -> str:
    with Image.open(path) as img:
        img.seek(0)
        converted = ImageOps.exif_transpose(img).convert("RGB")
        converted.thumbnail((1280, 1280))
        buf = BytesIO()
        converted.save(buf, format="JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _parse_prompt_response(text: str) -> list[dict[str, Any]]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()

    candidates = [cleaned]
    for opener, closer in [("{", "}"), ("[", "]")]:
        start = cleaned.find(opener)
        end = cleaned.rfind(closer)
        if start >= 0 and end > start:
            candidates.append(cleaned[start : end + 1])

    parsed: Any = None
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            break
        except json.JSONDecodeError:
            continue

    if parsed is None:
        prompts = []
        for line in cleaned.splitlines():
            line = re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", line).strip().strip('"')
            if line:
                prompts.append({"prompt": line, "category": "fallback", "rationale": "Parsed from non-JSON VLM text."})
        return prompts

    if isinstance(parsed, dict):
        raw_prompts = parsed.get("prompts") or []
    elif isinstance(parsed, list):
        raw_prompts = parsed
    else:
        raw_prompts = []

    prompts: list[dict[str, Any]] = []
    for item in raw_prompts:
        if isinstance(item, str):
            prompt = item.strip()
            category = "unspecified"
            rationale = ""
        elif isinstance(item, dict):
            prompt = str(item.get("prompt") or item.get("query") or "").strip()
            category = str(item.get("category") or "unspecified")
            rationale = str(item.get("rationale") or "")
        else:
            continue
        if prompt:
            prompts.append({"prompt": prompt, "category": category, "rationale": rationale})
    return prompts


def _target_prompt_text(target: dict[str, Any], *, prompts_per_image: int) -> str:
    metadata = target.get("metadata_for_reviewer") or {}
    return f"""You are labeling one meme for a local meme search system.

Look at the image and write {prompts_per_image} natural prompts a human might type when they want this exact meme.

Rules:
- Return JSON only: {{"prompts":[{{"prompt":"...","category":"exact_memory|paraphrase|emotion|topic|named_entity|short_sloppy|multilingual","rationale":"..."}}]}}
- Prompts must sound like user searches, for example "find me a meme on I don't have friends just people I know".
- Do not mention filename, file path, sha256, target id, image id, image resolution, or database metadata.
- Use exact visible text only when a normal user would remember that text.
- Include Bangla prompts naturally if the image contains Bangla text.
- Prefer prompts that would help retrieve this exact meme from a large corpus.

Visible/index hints, only to help read the image:
OCR excerpt: {metadata.get("ocr_excerpt") or ""}
Caption hint: {metadata.get("caption_literal") or metadata.get("caption_figurative") or ""}
Template hint: {metadata.get("template_name") or ""}
Tags hint: {", ".join(metadata.get("tags") or [])}
"""


def _metadata_prompt_text(target: dict[str, Any], *, prompts_per_image: int) -> str:
    metadata = target.get("metadata_for_reviewer") or {}
    public_metadata = {
        "ocr_excerpt": metadata.get("ocr_excerpt") or "",
        "caption_literal": metadata.get("caption_literal") or "",
        "caption_figurative": metadata.get("caption_figurative") or "",
        "template_name": metadata.get("template_name") or "",
        "tags": list(metadata.get("tags") or []),
    }
    return f"""You are acting as a human prompt writer for a local meme search benchmark.

Write {prompts_per_image} natural user search prompts for the meme described by this metadata.

Rules:
- Return JSON only: {{"prompts":[{{"prompt":"...","category":"exact_memory|paraphrase|emotion|topic|named_entity|short_sloppy|multilingual","rationale":"..."}}]}}
- Prompts must sound like real searches, for example "find me meme on I don't have friends just people I know".
- Do not mention filename, file path, sha256, target id, image id, image resolution, database fields, or that metadata was provided.
- Use exact visible text only when a normal user would remember that text.
- Include Bangla prompts naturally if the OCR/caption indicates Bangla text.
- Prefer prompts that would retrieve this exact meme from a large corpus, not generic image captions.

Meme metadata:
{json.dumps(public_metadata, ensure_ascii=False, indent=2)}
"""


def _public_target_metadata(target: dict[str, Any]) -> dict[str, Any]:
    metadata = target.get("metadata_for_reviewer") or {}
    return {
        "ocr_excerpt": metadata.get("ocr_excerpt") or "",
        "caption_literal": metadata.get("caption_literal") or "",
        "caption_figurative": metadata.get("caption_figurative") or "",
        "template_name": metadata.get("template_name") or "",
        "tags": list(metadata.get("tags") or []),
    }


def _metadata_batch_prompt_text(targets: list[dict[str, Any]], *, prompts_per_image: int) -> str:
    batch = [
        {
            "target_id": str(target["target_id"]),
            "metadata": _public_target_metadata(target),
        }
        for target in targets
    ]
    return f"""You are acting as a human prompt writer for a local meme search benchmark.

For each target below, write {prompts_per_image} natural user search prompt(s) for the described meme.

Rules:
- Return JSON only: {{"items":[{{"target_id":"...","prompts":[{{"prompt":"...","category":"exact_memory|paraphrase|emotion|topic|named_entity|short_sloppy|multilingual","rationale":"..."}}]}}]}}
- Prompts must sound like real searches, for example "find me meme on I don't have friends just people I know".
- Do not mention filename, file path, sha256, image id, image resolution, database fields, or that metadata was provided.
- The `target_id` is only for routing your JSON response; never include it inside a prompt.
- Use exact visible text only when a normal user would remember that text.
- Include Bangla prompts naturally if the OCR/caption indicates Bangla text.
- Prefer prompts that would retrieve the exact meme from a large corpus, not generic image captions.

Targets:
{json.dumps(batch, ensure_ascii=False, indent=2)}
"""


def _parse_batch_prompt_response(text: str) -> dict[str, list[dict[str, Any]]]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()

    candidates = [cleaned]
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        candidates.append(cleaned[start : end + 1])

    parsed: Any = None
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            break
        except json.JSONDecodeError:
            continue
    if not isinstance(parsed, dict):
        return {}

    by_target: dict[str, list[dict[str, Any]]] = {}
    for item in parsed.get("items") or []:
        if not isinstance(item, dict):
            continue
        target_id = str(item.get("target_id") or "").strip()
        if not target_id:
            continue
        by_target[target_id] = _parse_prompt_response(json.dumps({"prompts": item.get("prompts") or []}))
    return by_target


def _append_prompt_items(
    rows: list[dict[str, Any]],
    *,
    target_id: str,
    prompt_items: list[dict[str, Any]],
    prompts_per_image: int,
    operator: str,
    model_fingerprint: dict[str, str] | None = None,
) -> int:
    kept = prompt_items[: max(prompts_per_image, 1)]
    for index, item in enumerate(kept, start=1):
        fingerprint = dict(model_fingerprint or {})
        rows.append(
            {
                "record_type": PROMPT_RECORD,
                "target_id": target_id,
                "prompt_id": f"{target_id}:p{index}",
                "prompt": item["prompt"],
                "category": item.get("category") or "unspecified",
                "operator": operator,
                "model_fingerprint": fingerprint,
                "model_family": fingerprint.get("family", "unknown"),
                "rationale": item.get("rationale") or "",
            }
        )
    return len(kept)


def _unique_target_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        target_id = str(row.get("target_id") or "")
        if not target_id or target_id in seen:
            continue
        seen.add(target_id)
        unique.append(row)
    return unique


def _indexed_image_for_sha(sha256_bytes: bytes) -> dict[str, Any] | None:
    with pg_store.get_cursor() as cur:
        cur.execute(
            """SELECT i.image_id, i.source_uri, i.width, i.height, i.format,
                      item.ocr_text, item.caption_literal, item.caption_figurative,
                      item.template_name, item.tags
               FROM core.images i
               LEFT JOIN core.image_items item ON item.image_id = i.image_id
               WHERE i.sha256 = %s""",
            (sha256_bytes,),
        )
        row = cur.fetchone()
    if not row:
        return None
    return {
        "image_id": row[0],
        "source_uri": row[1],
        "width": row[2],
        "height": row[3],
        "format": row[4],
        "ocr_excerpt": (row[5] or "")[:240],
        "caption_literal": row[6] or "",
        "caption_figurative": row[7] or "",
        "template_name": row[8] or "",
        "tags": list(row[9] or []),
    }


def _training_target_image_ids(training_pack: Path) -> set[str]:
    if not training_pack.exists():
        return set()
    ids: set[str] = set()
    for row in _read_jsonl(training_pack):
        image_id = row.get("target_image_id")
        if image_id:
            ids.add(str(image_id))
    return ids


def _metadata_for_db_row(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "ocr_excerpt": (row[5] or "")[:240],
        "caption_literal": row[6] or "",
        "caption_figurative": row[7] or "",
        "template_name": row[8] or "",
        "tags": list(row[9] or []),
    }


def build_target_pack(
    *,
    folder: Path,
    output_path: Path,
    limit: int | None = None,
) -> dict[str, Any]:
    paths = sorted(
        path for path in folder.iterdir() if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    )
    if limit is not None:
        paths = paths[:limit]

    rows: list[dict[str, Any]] = []
    indexed = 0
    for path in paths:
        sha_hex, sha_bytes = _sha256_file(path)
        image = _indexed_image_for_sha(sha_bytes)
        if image:
            indexed += 1
        target_id = f"target-{sha_hex[:16]}"
        rows.append(
            {
                "record_type": TARGET_RECORD,
                "target_id": target_id,
                "target_path": str(path.resolve()),
                "filename": path.name,
                "sha256": sha_hex,
                "indexed": bool(image),
                "target_image_id": image["image_id"] if image else None,
                "indexed_source_uri": image["source_uri"] if image else None,
                "metadata_for_reviewer": {
                    "ocr_excerpt": image["ocr_excerpt"] if image else "",
                    "caption_literal": image["caption_literal"] if image else "",
                    "caption_figurative": image["caption_figurative"] if image else "",
                    "template_name": image["template_name"] if image else "",
                    "tags": image["tags"] if image else [],
                },
            }
        )

    _write_jsonl(output_path, rows)
    return {
        "status": "written",
        "output": str(output_path),
        "targets": len(rows),
        "indexed_targets": indexed,
        "unindexed_targets": len(rows) - indexed,
    }


def build_disjoint_holdout_pack(
    *,
    training_pack: Path,
    output_path: Path,
    corpus_root: Path = Path("."),
    limit: int = 100,
    seed: int = 20260427,
) -> dict[str, Any]:
    training_ids = _training_target_image_ids(training_pack)
    with pg_store.get_cursor() as cur:
        cur.execute(
            """SELECT i.image_id, encode(i.sha256, 'hex'), i.source_uri, i.width, i.height,
                      item.ocr_text, item.caption_literal, item.caption_figurative,
                      item.template_name, item.tags
               FROM core.images i
               LEFT JOIN core.image_items item ON item.image_id = i.image_id
               WHERE NOT (i.image_id = ANY(%s))
               ORDER BY i.image_id""",
            (list(training_ids),),
        )
        candidates = list(cur.fetchall())

    rng = random.Random(seed)
    rng.shuffle(candidates)

    rows: list[dict[str, Any]] = []
    skipped_missing_files = 0
    for row in candidates:
        image_id, sha_hex, source_uri = str(row[0]), str(row[1]), str(row[2])
        target_path = corpus_root / Path(source_uri)
        if not target_path.exists():
            skipped_missing_files += 1
            continue
        rows.append(
            {
                "record_type": TARGET_RECORD,
                "target_id": f"holdout-{sha_hex[:16]}",
                "target_path": str(target_path.resolve()),
                "filename": target_path.name,
                "sha256": sha_hex,
                "indexed": True,
                "target_image_id": image_id,
                "indexed_source_uri": source_uri,
                "metadata_for_reviewer": _metadata_for_db_row(row),
                "holdout_policy": {
                    "source": "data/meme indexed corpus",
                    "excluded_training_pack": str(training_pack),
                    "seed": seed,
                    "disjoint_from_training_target_ids": True,
                },
            }
        )
        if len(rows) >= limit:
            break

    _write_jsonl(output_path, rows)
    return {
        "status": "written",
        "output": str(output_path),
        "targets": len(rows),
        "training_targets_excluded": len(training_ids),
        "candidate_rows": len(candidates),
        "skipped_missing_files": skipped_missing_files,
        "seed": seed,
    }


def write_target_prompt(
    *,
    pack_path: Path,
    output_path: Path,
    prompts_per_image: int = 5,
    labels_output: Path | None = None,
) -> dict[str, Any]:
    labels_output = labels_output or output_path.with_name("target_prompts.jsonl")
    prompt = f"""# Target-Image Prompt Labeling Task

You are standing in for the human who knows which meme they wanted.

Input target pack: `{pack_path}`
Output JSONL path: `{labels_output}`

For each `record_type="{TARGET_RECORD}"` row:
- Open or inspect `target_path`.
- Write {prompts_per_image}-10 natural prompts that a real user might type to retrieve that exact meme.
- Prefer human search language over academic image descriptions.
- Include exact text/OCR only when a user would naturally remember it.
- Include fuzzy paraphrases and multilingual variants when useful.
- Do not put the filename, sha256, or `target_image_id` into the prompt text.
- Do not assume the search system will be given the target image. It will only receive the prompt.

Write one JSONL row per prompt using exactly this shape:

```json
{{"record_type":"{PROMPT_RECORD}","target_id":"target-...","prompt_id":"target-...:p1","prompt":"find me a meme on ...","category":"exact_memory|paraphrase|emotion|topic|named_entity|short_sloppy|multilingual","operator":"codex|claude|opencode","rationale":"short reason"}}
```

After the prompt file is written, run it with:

```powershell
.\\scripts\\rlhf_target_benchmark.ps1 -Pack {pack_path} -Prompts {labels_output} -ReplacePrefix
```

Found targets create normal ranking feedback. Missing targets are written as `{MISSING_RECORD}` rows and must be fixed by retrieval, not by the ranker.
"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(prompt, encoding="utf-8")
    return {"status": "written", "output": str(output_path), "labels_output": str(labels_output)}


def generate_prompts_ollama(
    *,
    pack_path: Path,
    output_path: Path,
    model: str = DEFAULT_VLM_MODEL,
    ollama_url: str = DEFAULT_OLLAMA_URL,
    prompts_per_image: int = 5,
    limit: int | None = None,
    resume: bool = False,
    timeout_seconds: float = 180.0,
) -> dict[str, Any]:
    target_rows = _unique_target_rows([row for row in _read_jsonl(pack_path) if row.get("record_type") == TARGET_RECORD])
    if limit is not None:
        target_rows = target_rows[:limit]

    rows: list[dict[str, Any]] = []
    existing_targets: set[str] = set()
    if resume and output_path.exists():
        rows = _read_jsonl(output_path)
        existing_targets = {
            str(row.get("target_id")) for row in rows if row.get("record_type") == PROMPT_RECORD and row.get("target_id")
        }

    generated = 0
    errors: list[dict[str, Any]] = []
    fingerprint = _model_fingerprint(provider="ollama", model=model, endpoint=ollama_url, mode="image")
    for target in target_rows:
        target_id = str(target["target_id"])
        if target_id in existing_targets:
            continue
        image_path = Path(str(target["target_path"]))
        prompt_text = _target_prompt_text(target, prompts_per_image=prompts_per_image)
        try:
            payload = {
                "model": model,
                "prompt": prompt_text,
                "images": [_image_base64_for_vlm(image_path)],
                "stream": False,
                "format": "json",
                "options": {"temperature": 0.2},
            }
            response = httpx.post(f"{ollama_url.rstrip('/')}/api/generate", json=payload, timeout=timeout_seconds)
            response.raise_for_status()
            response_text = str(response.json().get("response") or "")
            prompt_items = _parse_prompt_response(response_text)[: max(prompts_per_image, 1)]
            if not prompt_items:
                raise ValueError("VLM returned no parseable prompts")
            _append_prompt_items(
                rows,
                target_id=target_id,
                prompt_items=prompt_items,
                prompts_per_image=prompts_per_image,
                operator=f"ollama:{model}",
                model_fingerprint=fingerprint,
            )
            generated += 1
            _write_jsonl(output_path, rows)
        except Exception as exc:  # pragma: no cover - local VLM failures vary by machine.
            errors.append({"target_id": target_id, "target_path": str(image_path), "error": str(exc)})
            _write_jsonl(output_path, rows)

    return {
        "status": "failed" if errors else "complete",
        "pack": str(pack_path),
        "output": str(output_path),
        "model": model,
        "model_fingerprint": fingerprint,
        "targets_requested": len(target_rows),
        "targets_generated": generated,
        "prompt_rows": len(rows),
        "errors": errors,
    }


def generate_prompts_gateway(
    *,
    pack_path: Path,
    output_path: Path,
    model: str = DEFAULT_GATEWAY_MODEL,
    gateway_url: str = DEFAULT_GATEWAY_URL,
    api_key: str | None = None,
    prompts_per_image: int = 5,
    limit: int | None = None,
    resume: bool = False,
    timeout_seconds: float = 180.0,
) -> dict[str, Any]:
    target_rows = _unique_target_rows([row for row in _read_jsonl(pack_path) if row.get("record_type") == TARGET_RECORD])
    if limit is not None:
        target_rows = target_rows[:limit]

    rows: list[dict[str, Any]] = []
    existing_targets: set[str] = set()
    if resume and output_path.exists():
        rows = _read_jsonl(output_path)
        existing_targets = {
            str(row.get("target_id")) for row in rows if row.get("record_type") == PROMPT_RECORD and row.get("target_id")
        }

    key = api_key if api_key is not None else os.environ.get("LITELLM_MASTER_KEY", "")
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"

    generated = 0
    errors: list[dict[str, Any]] = []
    fingerprint = _model_fingerprint(provider="litellm", model=model, endpoint=gateway_url, mode="image")
    models_url = f"{gateway_url.rstrip('/')}/v1/models"
    chat_url = f"{gateway_url.rstrip('/')}/v1/chat/completions"
    try:
        probe = httpx.get(models_url, headers=headers, timeout=15.0)
        probe.raise_for_status()
    except Exception as exc:
        return {
            "status": "failed",
            "pack": str(pack_path),
            "output": str(output_path),
            "model": model,
            "model_fingerprint": fingerprint,
            "gateway_url": gateway_url,
            "targets_requested": len(target_rows),
            "targets_generated": 0,
            "prompt_rows": len(rows),
            "errors": [{"error": f"LiteLLM gateway is not reachable/authenticated at {models_url}: {exc}"}],
        }

    for target in target_rows:
        target_id = str(target["target_id"])
        if target_id in existing_targets:
            continue
        image_path = Path(str(target["target_path"]))
        prompt_text = _target_prompt_text(target, prompts_per_image=prompts_per_image)
        try:
            payload = {
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt_text},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/jpeg;base64,{_image_base64_for_vlm(image_path)}"},
                            },
                        ],
                    }
                ],
                "temperature": 0.2,
                "response_format": {"type": "json_object"},
            }
            response = httpx.post(chat_url, json=payload, headers=headers, timeout=timeout_seconds)
            response.raise_for_status()
            response_json = response.json()
            response_text = str(response_json["choices"][0]["message"].get("content") or "")
            prompt_items = _parse_prompt_response(response_text)[: max(prompts_per_image, 1)]
            if not prompt_items:
                raise ValueError("gateway VLM returned no parseable prompts")
            _append_prompt_items(
                rows,
                target_id=target_id,
                prompt_items=prompt_items,
                prompts_per_image=prompts_per_image,
                operator=f"litellm:{model}",
                model_fingerprint=fingerprint,
            )
            generated += 1
            _write_jsonl(output_path, rows)
        except Exception as exc:  # pragma: no cover - gateway failures vary by machine.
            errors.append({"target_id": target_id, "target_path": str(image_path), "error": str(exc)})
            _write_jsonl(output_path, rows)

    return {
        "status": "failed" if errors else "complete",
        "pack": str(pack_path),
        "output": str(output_path),
        "model": model,
        "model_fingerprint": fingerprint,
        "gateway_url": gateway_url,
        "targets_requested": len(target_rows),
        "targets_generated": generated,
        "prompt_rows": len(rows),
        "errors": errors,
    }


def generate_prompts_metadata_gateway(
    *,
    pack_path: Path,
    output_path: Path,
    model: str = DEFAULT_GATEWAY_TEXT_MODEL,
    gateway_url: str = DEFAULT_GATEWAY_URL,
    api_key: str | None = None,
    prompts_per_image: int = 1,
    limit: int | None = None,
    resume: bool = False,
    timeout_seconds: float = 90.0,
    batch_size: int = 1,
) -> dict[str, Any]:
    target_rows = _unique_target_rows([row for row in _read_jsonl(pack_path) if row.get("record_type") == TARGET_RECORD])
    if limit is not None:
        target_rows = target_rows[:limit]

    rows: list[dict[str, Any]] = []
    existing_targets: set[str] = set()
    if resume and output_path.exists():
        rows = _read_jsonl(output_path)
        existing_targets = {
            str(row.get("target_id")) for row in rows if row.get("record_type") == PROMPT_RECORD and row.get("target_id")
        }

    key = api_key if api_key is not None else os.environ.get("LITELLM_MASTER_KEY", "")
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"

    generated = 0
    errors: list[dict[str, Any]] = []
    fingerprint = _model_fingerprint(provider="litellm", model=model, endpoint=gateway_url, mode="metadata")
    models_url = f"{gateway_url.rstrip('/')}/v1/models"
    chat_url = f"{gateway_url.rstrip('/')}/v1/chat/completions"
    try:
        probe = httpx.get(models_url, headers=headers, timeout=15.0)
        probe.raise_for_status()
    except Exception as exc:
        return {
            "status": "failed",
            "pack": str(pack_path),
            "output": str(output_path),
            "model": model,
            "model_fingerprint": fingerprint,
            "gateway_url": gateway_url,
            "targets_requested": len(target_rows),
            "targets_generated": 0,
            "prompt_rows": len(rows),
            "errors": [{"error": f"LiteLLM gateway is not reachable/authenticated at {models_url}: {exc}"}],
        }

    pending_targets = [target for target in target_rows if str(target["target_id"]) not in existing_targets]
    if batch_size > 1:
        for start in range(0, len(pending_targets), batch_size):
            batch_targets = pending_targets[start : start + batch_size]
            batch_ids = [str(target["target_id"]) for target in batch_targets]
            try:
                payload = {
                    "model": model,
                    "messages": [
                        {
                            "role": "user",
                            "content": _metadata_batch_prompt_text(batch_targets, prompts_per_image=prompts_per_image),
                        }
                    ],
                    "temperature": 0.2,
                    "response_format": {"type": "json_object"},
                }
                response = httpx.post(chat_url, json=payload, headers=headers, timeout=timeout_seconds)
                response.raise_for_status()
                response_json = response.json()
                response_text = str(response_json["choices"][0]["message"].get("content") or "")
                parsed_by_target = _parse_batch_prompt_response(response_text)
                for target in batch_targets:
                    target_id = str(target["target_id"])
                    prompt_items = parsed_by_target.get(target_id, [])[: max(prompts_per_image, 1)]
                    if not prompt_items:
                        errors.append(
                            {
                                "target_id": target_id,
                                "target_path": str(target.get("target_path") or ""),
                                "error": "gateway text model returned no parseable prompts for target in batch",
                            }
                        )
                        continue
                    _append_prompt_items(
                        rows,
                        target_id=target_id,
                        prompt_items=prompt_items,
                        prompts_per_image=prompts_per_image,
                        operator=f"litellm-metadata:{model}",
                        model_fingerprint=fingerprint,
                    )
                    generated += 1
                _write_jsonl(output_path, rows)
            except Exception as exc:  # pragma: no cover - gateway failures vary by machine.
                for target_id, target in zip(batch_ids, batch_targets, strict=False):
                    errors.append(
                        {
                            "target_id": target_id,
                            "target_path": str(target.get("target_path") or ""),
                            "error": str(exc),
                        }
                    )
                _write_jsonl(output_path, rows)

        return {
            "status": "failed" if errors else "complete",
            "pack": str(pack_path),
            "output": str(output_path),
            "model": model,
            "model_fingerprint": fingerprint,
            "gateway_url": gateway_url,
            "targets_requested": len(target_rows),
            "targets_generated": generated,
            "prompt_rows": len(rows),
            "errors": errors,
        }

    for target in pending_targets:
        target_id = str(target["target_id"])
        try:
            payload = {
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": _metadata_prompt_text(target, prompts_per_image=prompts_per_image),
                    }
                ],
                "temperature": 0.2,
                "response_format": {"type": "json_object"},
            }
            response = httpx.post(chat_url, json=payload, headers=headers, timeout=timeout_seconds)
            response.raise_for_status()
            response_json = response.json()
            response_text = str(response_json["choices"][0]["message"].get("content") or "")
            prompt_items = _parse_prompt_response(response_text)[: max(prompts_per_image, 1)]
            if not prompt_items:
                raise ValueError("gateway text model returned no parseable prompts")
            _append_prompt_items(
                rows,
                target_id=target_id,
                prompt_items=prompt_items,
                prompts_per_image=prompts_per_image,
                operator=f"litellm-metadata:{model}",
                model_fingerprint=fingerprint,
            )
            generated += 1
            _write_jsonl(output_path, rows)
        except Exception as exc:  # pragma: no cover - gateway failures vary by machine.
            errors.append(
                {
                    "target_id": target_id,
                    "target_path": str(target.get("target_path") or ""),
                    "error": str(exc),
                }
            )
            _write_jsonl(output_path, rows)

    return {
        "status": "failed" if errors else "complete",
        "pack": str(pack_path),
        "output": str(output_path),
        "model": model,
        "model_fingerprint": fingerprint,
        "gateway_url": gateway_url,
        "targets_requested": len(target_rows),
        "targets_generated": generated,
        "prompt_rows": len(rows),
        "errors": errors,
    }


def validate_prompt_rows(
    *,
    pack_rows: list[dict[str, Any]],
    prompt_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    targets = {row["target_id"]: row for row in pack_rows if row.get("record_type") == TARGET_RECORD}
    prompts: list[dict[str, Any]] = []
    seen_prompt_ids: set[str] = set()
    errors: list[str] = []
    for index, row in enumerate(prompt_rows, start=1):
        if row.get("record_type") != PROMPT_RECORD:
            continue
        target_id = str(row.get("target_id") or "")
        prompt_id = str(row.get("prompt_id") or "")
        prompt = str(row.get("prompt") or "").strip()
        if target_id not in targets:
            errors.append(f"row {index}: unknown target_id {target_id!r}")
        if not prompt_id:
            errors.append(f"row {index}: missing prompt_id")
        if prompt_id in seen_prompt_ids:
            errors.append(f"row {index}: duplicate prompt_id {prompt_id!r}")
        if not prompt:
            errors.append(f"row {index}: empty prompt")
        seen_prompt_ids.add(prompt_id)
        prompts.append({**row, "prompt": prompt})
    if not prompts:
        errors.append("no prompt rows found")
    if errors:
        raise ValueError("; ".join(errors[:10]))
    return prompts


def validate_target_prompts(*, pack_path: Path, prompts_path: Path) -> dict[str, Any]:
    pack_rows = _read_jsonl(pack_path)
    prompt_rows = _read_jsonl(prompts_path)
    prompts = validate_prompt_rows(pack_rows=pack_rows, prompt_rows=prompt_rows)
    return {"status": "valid", "targets": len({row["target_id"] for row in pack_rows}), "prompts": len(prompts)}


def _post_search(
    *,
    api_base_url: str,
    prompt: str,
    top_k: int,
    client_session_id: str,
    operator: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    response = httpx.post(
        f"{api_base_url.rstrip('/')}/search",
        json={
            "query": prompt,
            "limit": top_k,
            "client_session_id": client_session_id,
            "owui_user_id": operator,
        },
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    return response.json()


def run_target_searches(
    *,
    pack_path: Path,
    prompts_path: Path,
    output_path: Path,
    misses_output_path: Path,
    client_session_prefix: str,
    operator: str,
    api_base_url: str = DEFAULT_API_BASE_URL,
    top_k: int = 20,
    replace_prefix: bool = False,
    timeout_seconds: float = 60.0,
    validate_only: bool = False,
) -> dict[str, Any]:
    pack_rows = _read_jsonl(pack_path)
    prompt_rows = _read_jsonl(prompts_path)
    prompts = validate_prompt_rows(pack_rows=pack_rows, prompt_rows=prompt_rows)
    targets = {row["target_id"]: row for row in pack_rows if row.get("record_type") == TARGET_RECORD}

    if validate_only:
        return {
            "status": "valid",
            "targets": len(targets),
            "prompts": len(prompts),
            "api_base_url": api_base_url,
            "top_k": top_k,
        }

    existing = _count_sessions_for_prefix(client_session_prefix)
    deleted = 0
    if existing and not replace_prefix:
        raise ValueError(
            f"{existing} feedback sessions already exist for prefix {client_session_prefix!r}; "
            "use --replace-prefix to delete and recreate them"
        )
    if existing and replace_prefix:
        deleted = _delete_sessions_for_prefix(client_session_prefix)

    results: list[dict[str, Any]] = []
    misses: list[dict[str, Any]] = []
    selected = 0
    not_found = 0
    not_indexed = 0
    errors: list[dict[str, Any]] = []

    for prompt_row in prompts:
        target = targets[str(prompt_row["target_id"])]
        target_image_id = target.get("target_image_id")
        prompt_id = str(prompt_row["prompt_id"])
        base_result = {
            "record_type": RESULT_RECORD,
            "target_id": target["target_id"],
            "target_image_id": target_image_id,
            "target_path": target["target_path"],
            "prompt_id": prompt_id,
            "prompt": prompt_row["prompt"],
            "prompt_category": prompt_row.get("category") or "unspecified",
            "prompt_rationale": prompt_row.get("rationale") or "",
            "operator": operator,
            "client_session_id": _session_id(client_session_prefix, prompt_id),
            "top_k": top_k,
        }

        if not target_image_id:
            not_indexed += 1
            row = {
                **base_result,
                "status": "target_not_indexed",
                "failure_type": "target_not_indexed",
                "message": "Target image hash was not found in core.images.",
            }
            results.append(row)
            misses.append({**row, "record_type": MISSING_RECORD})
            continue

        try:
            search_response = _post_search(
                api_base_url=api_base_url,
                prompt=str(prompt_row["prompt"]),
                top_k=top_k,
                client_session_id=base_result["client_session_id"],
                operator=operator,
                timeout_seconds=timeout_seconds,
            )
            hits = list(search_response.get("hits") or [])
            found_hit = next((hit for hit in hits if hit.get("image_id") == target_image_id), None)
            top_image_ids = [str(hit.get("image_id")) for hit in hits]
            if found_hit:
                token = _token_from_feedback_url(str(found_hit.get("feedback_select_url") or ""))
                judgment = record_judgment_from_token(token)
                selected += 1
                results.append(
                    {
                        **base_result,
                        "status": "found_selected",
                        "search_id": search_response.get("search_id"),
                        "rank": int(found_hit.get("rank") or 0),
                        "base_rank": found_hit.get("base_rank"),
                        "judgment": judgment,
                        "top_image_ids": top_image_ids,
                    }
                )
                _write_jsonl(output_path, results)
                _write_jsonl(misses_output_path, misses)
            else:
                not_found += 1
                row = {
                    **base_result,
                    "status": "target_not_found",
                    "failure_type": "target_not_in_slate",
                    "search_id": search_response.get("search_id"),
                    "top_image_ids": top_image_ids,
                    "top_source_uris": [str(hit.get("source_uri") or "") for hit in hits],
                }
                results.append(row)
                misses.append({**row, "record_type": MISSING_RECORD})
                _write_jsonl(output_path, results)
                _write_jsonl(misses_output_path, misses)
        except Exception as exc:  # pragma: no cover - integration failures are environment-specific.
            errors.append({**base_result, "error": str(exc)})
            _write_jsonl(output_path, results)
            _write_jsonl(misses_output_path, misses)

    _write_jsonl(output_path, results)
    _write_jsonl(misses_output_path, misses)
    return {
        "status": "failed" if errors else "complete",
        "targets": len(targets),
        "prompts": len(prompts),
        "selected": selected,
        "target_not_found": not_found,
        "target_not_indexed": not_indexed,
        "errors": errors,
        "deleted_sessions": deleted,
        "output": str(output_path),
        "misses_output": str(misses_output_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Target-image benchmark loop for retrieval feedback and LTR labels.")
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build-target-pack", help="Build JSONL target image tasks from data/meme_rlhf.")
    build.add_argument("--folder", default="data/meme_rlhf")
    build.add_argument("--output", default="artifacts/feedback_targets/target_pack.jsonl")
    build.add_argument("--limit", type=int)

    holdout = sub.add_parser(
        "build-disjoint-holdout-pack",
        help="Build a held-out target pack from indexed data/meme images excluding the training target pack.",
    )
    holdout.add_argument("--training-pack", default="artifacts/feedback_targets/target_pack.jsonl")
    holdout.add_argument("--output", default="artifacts/feedback_targets/holdout_target_pack.jsonl")
    holdout.add_argument("--corpus-root", default=".")
    holdout.add_argument("--limit", type=int, default=100)
    holdout.add_argument("--seed", type=int, default=20260427)

    prompt = sub.add_parser("write-target-prompt", help="Write an LLM-agent prompt for target prompt labeling.")
    prompt.add_argument("--pack", default="artifacts/feedback_targets/target_pack.jsonl")
    prompt.add_argument("--output", default="artifacts/feedback_targets/agent_prompt.md")
    prompt.add_argument("--labels-output", default="artifacts/feedback_targets/target_prompts.jsonl")
    prompt.add_argument("--prompts-per-image", type=int, default=5)

    generate_gateway = sub.add_parser(
        "generate-prompts-gateway",
        help="Generate target prompt labels through the LiteLLM OpenAI-compatible gateway.",
    )
    generate_gateway.add_argument("--pack", default="artifacts/feedback_targets/target_pack.jsonl")
    generate_gateway.add_argument("--output", default="artifacts/feedback_targets/target_prompts.jsonl")
    generate_gateway.add_argument("--model", default=DEFAULT_GATEWAY_MODEL)
    generate_gateway.add_argument("--gateway-url", default=DEFAULT_GATEWAY_URL)
    generate_gateway.add_argument("--api-key", default=None)
    generate_gateway.add_argument("--prompts-per-image", type=int, default=5)
    generate_gateway.add_argument("--limit", type=int)
    generate_gateway.add_argument("--resume", action="store_true")
    generate_gateway.add_argument("--timeout-seconds", type=float, default=180.0)

    generate_metadata_gateway = sub.add_parser(
        "generate-prompts-metadata-gateway",
        help="Generate target prompt labels through LiteLLM from target OCR/caption/tag metadata.",
    )
    generate_metadata_gateway.add_argument("--pack", default="artifacts/feedback_targets/target_pack.jsonl")
    generate_metadata_gateway.add_argument("--output", default="artifacts/feedback_targets/target_prompts.jsonl")
    generate_metadata_gateway.add_argument("--model", default=DEFAULT_GATEWAY_TEXT_MODEL)
    generate_metadata_gateway.add_argument("--gateway-url", default=DEFAULT_GATEWAY_URL)
    generate_metadata_gateway.add_argument("--api-key", default=None)
    generate_metadata_gateway.add_argument("--prompts-per-image", type=int, default=1)
    generate_metadata_gateway.add_argument("--limit", type=int)
    generate_metadata_gateway.add_argument("--resume", action="store_true")
    generate_metadata_gateway.add_argument("--timeout-seconds", type=float, default=90.0)
    generate_metadata_gateway.add_argument("--batch-size", type=int, default=1)

    generate = sub.add_parser(
        "generate-prompts-ollama",
        help="Generate target prompt labels with a direct local Ollama VLM. Fallback only; prefer gateway.",
    )
    generate.add_argument("--pack", default="artifacts/feedback_targets/target_pack.jsonl")
    generate.add_argument("--output", default="artifacts/feedback_targets/target_prompts.jsonl")
    generate.add_argument("--model", default=DEFAULT_VLM_MODEL)
    generate.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    generate.add_argument("--prompts-per-image", type=int, default=5)
    generate.add_argument("--limit", type=int)
    generate.add_argument("--resume", action="store_true")
    generate.add_argument("--timeout-seconds", type=float, default=180.0)

    validate = sub.add_parser("validate-prompts", help="Validate target prompt label JSONL.")
    validate.add_argument("--pack", default="artifacts/feedback_targets/target_pack.jsonl")
    validate.add_argument("--prompts", default="artifacts/feedback_targets/target_prompts.jsonl")

    run = sub.add_parser("run-target-searches", help="Run target prompts through the live search API.")
    run.add_argument("--pack", default="artifacts/feedback_targets/target_pack.jsonl")
    run.add_argument("--prompts", default="artifacts/feedback_targets/target_prompts.jsonl")
    run.add_argument("--output", default="artifacts/feedback_targets/results.jsonl")
    run.add_argument("--misses-output", default="artifacts/feedback_targets/target_not_found.jsonl")
    run.add_argument("--client-session-prefix", default="rlhf-target")
    run.add_argument("--operator", default="codex-agent")
    run.add_argument("--api-base-url", default=DEFAULT_API_BASE_URL)
    run.add_argument("--top-k", type=int, default=20)
    run.add_argument("--replace-prefix", action="store_true")
    run.add_argument("--timeout-seconds", type=float, default=60.0)
    run.add_argument("--validate-only", action="store_true")

    args = parser.parse_args()
    if args.command == "build-target-pack":
        result = build_target_pack(folder=Path(args.folder), output_path=Path(args.output), limit=args.limit)
    elif args.command == "build-disjoint-holdout-pack":
        result = build_disjoint_holdout_pack(
            training_pack=Path(args.training_pack),
            output_path=Path(args.output),
            corpus_root=Path(args.corpus_root),
            limit=args.limit,
            seed=args.seed,
        )
    elif args.command == "write-target-prompt":
        result = write_target_prompt(
            pack_path=Path(args.pack),
            output_path=Path(args.output),
            labels_output=Path(args.labels_output),
            prompts_per_image=args.prompts_per_image,
        )
    elif args.command == "generate-prompts-ollama":
        result = generate_prompts_ollama(
            pack_path=Path(args.pack),
            output_path=Path(args.output),
            model=args.model,
            ollama_url=args.ollama_url,
            prompts_per_image=args.prompts_per_image,
            limit=args.limit,
            resume=args.resume,
            timeout_seconds=args.timeout_seconds,
        )
    elif args.command == "generate-prompts-gateway":
        result = generate_prompts_gateway(
            pack_path=Path(args.pack),
            output_path=Path(args.output),
            model=args.model,
            gateway_url=args.gateway_url,
            api_key=args.api_key,
            prompts_per_image=args.prompts_per_image,
            limit=args.limit,
            resume=args.resume,
            timeout_seconds=args.timeout_seconds,
        )
    elif args.command == "generate-prompts-metadata-gateway":
        result = generate_prompts_metadata_gateway(
            pack_path=Path(args.pack),
            output_path=Path(args.output),
            model=args.model,
            gateway_url=args.gateway_url,
            api_key=args.api_key,
            prompts_per_image=args.prompts_per_image,
            limit=args.limit,
            resume=args.resume,
            timeout_seconds=args.timeout_seconds,
            batch_size=args.batch_size,
        )
    elif args.command == "validate-prompts":
        result = validate_target_prompts(pack_path=Path(args.pack), prompts_path=Path(args.prompts))
    else:
        result = run_target_searches(
            pack_path=Path(args.pack),
            prompts_path=Path(args.prompts),
            output_path=Path(args.output),
            misses_output_path=Path(args.misses_output),
            client_session_prefix=args.client_session_prefix,
            operator=args.operator,
            api_base_url=args.api_base_url,
            top_k=args.top_k,
            replace_prefix=args.replace_prefix,
            timeout_seconds=args.timeout_seconds,
            validate_only=args.validate_only,
        )

    print(json.dumps(result, indent=2, sort_keys=True, default=_json_default))
    return 0 if result.get("status") != "failed" else 2


if __name__ == "__main__":
    raise SystemExit(main())

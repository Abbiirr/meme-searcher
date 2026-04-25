from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import yaml

from vidsearch.storage.pg import get_cursor


INTENTS = (
    "exact_text",
    "fuzzy_text",
    "semantic_description",
    "mixed_visual_description",
)
PER_INTENT = 10
OUTPUT_PATH = Path(__file__).resolve().with_name("queries_memes.yaml")


@dataclass
class Candidate:
    image_id: str
    source_uri: str
    ocr_full_text: str
    caption_literal: str
    caption_figurative: str
    template_name: str
    tags: list[str]


def _slug(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _mostly_ascii(text: str) -> bool:
    if not text:
        return False
    ascii_chars = sum(1 for ch in text if ord(ch) < 128)
    return ascii_chars / max(len(text), 1) >= 0.85


def _sentence(text: str, limit: int) -> str:
    cleaned = " ".join(str(text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    clipped = cleaned[:limit].rsplit(" ", 1)[0].strip()
    return clipped or cleaned[:limit].strip()


def _select_phrase(ocr_full_text: str) -> str:
    cleaned = " ".join((ocr_full_text or "").split())
    cleaned = cleaned.replace("There is no text in the image.", "").strip()
    if not cleaned:
        return ""
    for splitter in (".", "!", "?", "\n"):
        if splitter in cleaned:
            cleaned = cleaned.split(splitter, 1)[0].strip()
            break
    tokens = cleaned.split()
    if len(tokens) > 12:
        cleaned = " ".join(tokens[:12])
    return cleaned.strip(" -:;,.").strip()


def _typo(query: str) -> str:
    tokens = query.split()
    if not tokens:
        return query
    pivot = max(tokens, key=len)
    if len(pivot) > 5:
        typo = pivot[:-1]
    elif len(pivot) > 3:
        typo = pivot[:-2] + pivot[-1]
    else:
        typo = pivot
    out = []
    replaced = False
    for token in tokens:
        if token == pivot and not replaced:
            out.append(typo)
            replaced = True
        else:
            out.append(token)
    degraded = " ".join(out)
    return f"something like {degraded}".strip()


def _visual_anchor(candidate: Candidate) -> str:
    literal = _slug(candidate.caption_literal)
    for anchor in (
        "dog",
        "cat",
        "woman",
        "man",
        "guy",
        "girl",
        "goat",
        "cartoon",
        "comic",
        "meme",
        "screenshot",
        "tweet",
        "sign",
        "photo",
    ):
        if anchor in literal:
            return anchor
    for tag in candidate.tags:
        tag_slug = _slug(tag)
        if tag_slug:
            return tag_slug
    return "image"


def _query_id(intent: str, image_id: str) -> str:
    digest = hashlib.sha1(f"{intent}:{image_id}".encode("utf-8")).hexdigest()[:12]
    return f"{intent[:4]}-{digest}"


def _load_candidates() -> list[Candidate]:
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT i.image_id,
                   i.source_uri,
                   COALESCE(ii.ocr_full_text, ''),
                   COALESCE(ii.caption_literal, ''),
                   COALESCE(ii.caption_figurative, ''),
                   COALESCE(ii.template_name, ''),
                   COALESCE(ii.tags, ARRAY[]::text[])
            FROM core.images i
            JOIN core.image_items ii USING (image_id)
            WHERE COALESCE(ii.retrieval_text, '') <> ''
            ORDER BY i.image_id
            """
        )
        rows = cur.fetchall()
    return [
        Candidate(
            image_id=row[0],
            source_uri=row[1],
            ocr_full_text=row[2],
            caption_literal=row[3],
            caption_figurative=row[4],
            template_name=row[5],
            tags=list(row[6] or []),
        )
        for row in rows
    ]


def _take_unique(candidates: Iterable[Candidate], count: int) -> list[Candidate]:
    picked: list[Candidate] = []
    seen_images: set[str] = set()
    seen_queries: set[str] = set()
    for candidate in candidates:
        if candidate.image_id in seen_images:
            continue
        seen_images.add(candidate.image_id)
        picked.append(candidate)
        if len(picked) >= count:
            break
    return picked


def _build_exact(candidate: Candidate) -> dict | None:
    phrase = _select_phrase(candidate.ocr_full_text)
    if len(phrase.split()) < 3:
        return None
    if not _mostly_ascii(phrase):
        return None
    query = f"\"{phrase.lower()}\""
    return {
        "query_id": _query_id("exact_text", candidate.image_id),
        "text": query,
        "intent": "exact_text",
        "target_image_id": candidate.image_id,
        "qrels": [{"image_id": candidate.image_id, "grade": 3}],
        "notes": f"Exact OCR phrase from {candidate.source_uri}",
    }


def _build_fuzzy(candidate: Candidate) -> dict | None:
    phrase = _select_phrase(candidate.ocr_full_text)
    if len(phrase.split()) < 3:
        return None
    if not _mostly_ascii(phrase):
        return None
    query = f"text says something like {_typo(phrase.lower()).removeprefix('something like ').strip()}"
    return {
        "query_id": _query_id("fuzzy_text", candidate.image_id),
        "text": query,
        "intent": "fuzzy_text",
        "target_image_id": candidate.image_id,
        "qrels": [{"image_id": candidate.image_id, "grade": 3}],
        "notes": f"Fuzzy OCR paraphrase from {candidate.source_uri}",
    }


def _build_semantic(candidate: Candidate) -> dict | None:
    description = candidate.caption_figurative or candidate.caption_literal
    description = _sentence(description, 90)
    if len(description.split()) < 4:
        return None
    query = f"meme about {description.lower().rstrip('.')}"
    return {
        "query_id": _query_id("semantic_description", candidate.image_id),
        "text": query,
        "intent": "semantic_description",
        "target_image_id": candidate.image_id,
        "qrels": [{"image_id": candidate.image_id, "grade": 3}],
        "notes": f"Semantic description from captions for {candidate.source_uri}",
    }


def _build_mixed(candidate: Candidate) -> dict | None:
    description = candidate.caption_figurative or candidate.caption_literal
    description = _sentence(description, 72).lower().rstrip(".")
    if len(description.split()) < 3:
        return None
    template = _slug(candidate.template_name)
    if template and template != "unknown":
        prefix = f"{template} meme about"
    else:
        prefix = f"{_visual_anchor(candidate)} meme about"
    query = f"{prefix} {description}"
    return {
        "query_id": _query_id("mixed_visual_description", candidate.image_id),
        "text": query,
        "intent": "mixed_visual_description",
        "target_image_id": candidate.image_id,
        "qrels": [{"image_id": candidate.image_id, "grade": 3}],
        "notes": f"Mixed visual/semantic query derived from {candidate.source_uri}",
    }


def rebuild_queries(seed: int = 42) -> dict:
    rng = random.Random(seed)
    candidates = _load_candidates()

    exact_pool = [
        candidate
        for candidate in candidates
        if len(_select_phrase(candidate.ocr_full_text).split()) >= 3 and _mostly_ascii(_select_phrase(candidate.ocr_full_text))
    ]
    semantic_pool = [
        candidate
        for candidate in candidates
        if len((candidate.caption_figurative or candidate.caption_literal).split()) >= 4
    ]

    rng.shuffle(exact_pool)
    rng.shuffle(semantic_pool)

    exact_items = _take_unique(exact_pool, PER_INTENT)
    fuzzy_items = _take_unique(exact_pool[PER_INTENT:], PER_INTENT)
    semantic_items = _take_unique(semantic_pool, PER_INTENT)
    mixed_items = _take_unique(semantic_pool[PER_INTENT:], PER_INTENT)

    if min(len(exact_items), len(fuzzy_items), len(semantic_items), len(mixed_items)) < PER_INTENT:
        raise RuntimeError(
            "not enough indexed candidates to build a balanced 10/10/10/10 eval set"
        )

    queries: list[dict] = []
    builders = (
        ("exact_text", exact_items, _build_exact),
        ("fuzzy_text", fuzzy_items, _build_fuzzy),
        ("semantic_description", semantic_items, _build_semantic),
        ("mixed_visual_description", mixed_items, _build_mixed),
    )
    for _intent, items, builder in builders:
        for candidate in items:
            query = builder(candidate)
            if query is None:
                raise RuntimeError(f"failed to build {_intent} query for {candidate.source_uri}")
            queries.append(query)

    data = {
        "queries": queries,
    }
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild the Phase 0 meme eval set from the indexed corpus")
    parser.add_argument("--output", default=str(OUTPUT_PATH))
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    data = rebuild_queries(seed=args.seed)
    output = Path(args.output)
    output.write_text(
        "# queries_memes.yaml - rebuilt from the indexed Phase 0 corpus\n"
        "# Generated by `python -m vidsearch.eval.rebuild_queries`\n\n"
        + yaml.safe_dump(data, sort_keys=False, allow_unicode=False),
        encoding="utf-8",
    )
    print(json.dumps({"output": str(output), "queries": len(data["queries"])}, indent=2))


if __name__ == "__main__":
    main()

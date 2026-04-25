# PHASE0_MEME_SEARCHER.md — Follow-up plan for a meme-first search engine

Version: 2026-04-19 (aligned to Phase 0 contract)
Status: Narrative companion to the **authoritative** Phase 0 contract in `PHASE_0_PLAN.md` + `PHASE_0_TODO.md`
Audience: engineers, Codex, future maintainers

> **Source-of-truth notice (2026-04-19):** where this narrative ever disagrees with `PHASE_0_PLAN.md` / `PHASE_0_TODO.md`, **the plan/todo win**. This file is kept for motivation and context; all hard numbers (corpus, query count, class split, acceptance thresholds) have been re-aligned to the plan. See `AGENTS_CONVERSATION.MD` Entries 6, 10, 11 for the reconciliation history.

---

## 1. Purpose

This document narrows the larger multimodal video-search architecture into a **Phase 0 image-only milestone** that is intentionally simpler, faster to build, cheaper to debug, and still highly meaningful.

Phase 0 builds a **meme search engine** over a large personal meme image corpus. It validates the retrieval spine that the full system depends on:

- OCR over embedded text
- dense + sparse text retrieval
- visual retrieval
- reciprocal-rank fusion
- local reranking
- Open WebUI ↔ LiteLLM ↔ backend integration
- evaluation and feedback loops
- content-addressed ingest and idempotent indexing

This phase is not a toy. It is the fastest way to prove the core of the system before adding audio, segmentation, temporal reasoning, and long-video summarization.

---

## 2. Why memes first

Memes are a strong Phase 0 dataset because they stress exactly the parts of multimodal retrieval that are hardest to get right early:

1. **OCR-heavy retrieval** — many memes are primarily text images.
2. **Image + text interplay** — the joke often depends on both.
3. **Semantic retrieval** — users rarely remember the exact text.
4. **Noise tolerance** — screenshots, watermarks, weird fonts, compression, borders.
5. **Fast iteration** — images are simpler than video and remove ASR/segmentation variables.

If the system can retrieve memes well from meaning, OCR text, and visual appearance, then the move to short clips is a natural extension rather than a fresh architecture.

---

## 3. Scope of Phase 0

### In scope

- `data/meme` corpus only (starting corpus; ~3,107 supported images)
- Image ingest from the local `data/meme` folder (S3-compatible ingest is **deferred to Phase 2+** and is out of Phase 0 scope)
- OCR on all images
- Image embeddings on all images
- Dense+sparse text embeddings on OCR text and optional captions
- Hybrid retrieval in Qdrant
- Local reranking
- Optional VLM verification on top hits
- Open WebUI as the primary frontend
- LiteLLM as the model gateway
- Postgres as the source of truth
- Evaluation set and regression harness

### Explicitly out of scope

- Audio
- ASR
- Shot detection
- Sliding windows
- Long-video summaries
- Graph retrieval
- Multi-tenant hosting
- Public upload UI
- CCTV-specific restoration or deblurring

---

## 4. Success criteria

Phase 0 is complete when all of the following are true:

1. A new image corpus can be ingested end to end without manual editing.
2. A query can retrieve memes by:
   - exact visible text
   - paraphrase of visible text
   - visual scene or object
   - mixed image+text intent
3. Results show thumbnail, source path, and a short “why this matched” explanation.
4. The system has a reproducible eval set with baseline metrics.
5. Re-running ingest on the same images is idempotent.
6. Open WebUI can call the backend as a tool and stream grounded answers.

### Minimum acceptance targets

- Full `data/meme` corpus indexed (corpus-count baseline pinned in `docs/decision_log.md` after the first full run)
- **40-query eval set, exactly 10 per class × 4 classes** (see §8 query types)
- nDCG@10 meets the `P0-G4` thresholds in `PHASE_0_PLAN.md` §9
- zero duplicate rows on re-ingest
- all critical paths work with hosted models disabled except optional VLM verification

---

## 5. Architecture delta from the main system

Phase 0 is the same architecture with the video-specific branches removed.

### Removed for Phase 0

- fetch/probe/remux for video files
- TransNetV2 and PySceneDetect
- window segmentation
- ASR and WhisperX fallback
- video caption backfill queue
- per-segment timeline citations

### Retained from the main architecture

- PostgreSQL metadata store
- MinIO artifact store
- Qdrant vector store
- LiteLLM gateway
- Open WebUI frontend
- FastAPI backend
- BGE-M3 text retrieval stack
- SigLIP-2 visual retrieval stack
- optional Lane C verification through hosted VLM
- local reranker

### Simplified data model

Each image is one searchable unit.

This means:
- one row in `core.images`
- one row in `core.image_items` (or reusing `core.segments` if you want schema continuity)
- one Qdrant point per image

---

## 6. Recommended component choices

These are the concrete choices for Phase 0.

### Storage
- PostgreSQL 18 (bumped from 17; compatibility matrix in `AGENTS_CONVERSATION.MD` Entry 7)
- MinIO
- Qdrant

### Frontend
- Open WebUI

### Gateway
- LiteLLM

### OCR
- PaddleOCR PP-OCRv5

### Text embeddings
- BGE-M3

### Visual embeddings
- SigLIP-2 So400m/16-384

### Local reranker
- jina-reranker-v2-base-multilingual

### Hosted VLM verification
- Gemini 2.5 Flash-Lite primary
- OpenRouter Nemotron Nano 12B v2 VL :free fallback

### Optional local VLM
- Qwen3-VL-8B first
- Qwen3-VL-30B-A3B UD-IQ2_XXS only as a stretch experiment after the stable meme searcher works

---

## 7. Phase 0 ingest pipeline

```text
image file
  -> sha256
  -> metadata extract (path, size, format, dimensions)
  -> OCR (PaddleOCR PP-OCRv5)
  -> optional cleanup / normalization of OCR text
  -> text embeddings (BGE-M3 dense + sparse + multivector)
  -> visual embeddings (SigLIP-2)
  -> optional caption if OCR is weak and image is hard to interpret
  -> PostgreSQL rows
  -> Qdrant point upsert
```

### Important design rule

Captions are optional in Phase 0.

Do not caption every meme at the start. Start with:
- OCR text
- image embedding
- text embedding over OCR text

Only add captioning for low-text or failed-retrieval cases.

---

## 8. Phase 0 query pipeline

```text
query text (text-only in Phase 0; query-image path is deferred)
  -> intent parse
  -> BGE-M3 query encode (dense + sparse)
  -> Qdrant prefetch:
       - text-dense
       - text-sparse
       - visual (query-text projected into SigLIP text tower; no user-supplied image in Phase 0)
  -> RRF fusion
  -> local reranker
  -> optional VLM verify top-5
  -> grounded answer synthesis
  -> stream to OWUI
```

### Query types to support in Phase 0 (four canonical classes)

These are the only Phase 0 classes. `OCR-fuzzy` is **folded into `fuzzy_text`**; it is not a separate class.

1. `exact_text` — exact visible text ("find the meme with 'im tired boss'")
2. `fuzzy_text` — text remembered badly or partially (covers OCR-style fuzziness)
3. `semantic_description` — meaning-only ("the meme where someone is completely done with life")
4. `mixed_visual_description` — image + text intent ("the Drake meme about code reviews")

Query-image input is out of Phase 0 scope.

---

## 9. Data model for Phase 0

Use a simplified image schema instead of forcing full video semantics too early.

### Suggested tables

#### `core.images`
- `image_id`
- `sha256`
- `source_uri`
- `width`
- `height`
- `format`
- `created_at`
- `metadata jsonb`

#### `core.image_items`
- `image_id`
- `thumbnail_uri`
- `ocr_text`
- `ocr_boxes jsonb`
- `caption_text`
- `caption_model`
- `has_ocr`
- `created_at`

You may optionally reuse the video `core.segments` idea with one “segment” per image, but I recommend image-specific tables first for clarity.

### Qdrant payload
- `image_id`
- `source_uri`
- `has_ocr`
- `format`
- `width`
- `height`
- `ingested_at`
- `model_version`

### Qdrant vector fields
- `text-dense` (BGE-M3)
- `text-sparse` (BGE-M3 sparse)
- `text-colbert` (optional later)
- `visual` (SigLIP-2)

---

## 10. Evaluation plan for Phase 0

### Eval set
**40 queries, exactly 10 per class × 4 classes** (see §8):

- 10 `exact_text`
- 10 `fuzzy_text`
- 10 `semantic_description`
- 10 `mixed_visual_description`

Every query carries graded relevance labels (`0..3` for at least the top-10 candidates per query) and references real files in `data/meme`. Source of truth: `PHASE_0_PLAN.md` §10.1.

### Metrics
- nDCG@10
- Recall@10
- Recall@50
- MRR
- top-1 exact hit rate
- reranker uplift over raw fusion

### Human labeling
Create a small gold set manually. This matters more than synthetic evals in Phase 0.

### Regression rule
Every major retrieval change must run the same eval set before merge.

---

## 11. Phase progression after memes

This is the sequence the whole project should follow.

### Phase 0 — Meme searcher
One image = one indexed item.

### Phase 1 — Short clips
Add:
- ASR
- short segment extraction
- one keyframe per segment
- no long-video handling yet

### Phase 2 — High-quality movie segment search
Add:
- dual segmentation
- more refined keyframe policies
- richer captions where needed
- sequence-aware queries

### Phase 3 — Personal CCTV search
Add:
- low-quality / blurry frame handling
- lower OCR confidence thresholds with care
- stronger visual-only retrieval paths
- optional frame enhancement only if proven necessary

Important: CCTV is a retrieval-quality and noise-tolerance problem first, not a “super-resolution everything” problem.

---

## 12. Implementation order

### Stage 0
Bring up infrastructure only.

### Stage 1
Ingest a 5-image smoke batch from `data/meme` and confirm `pg image_count == qdrant point_count`.

### Stage 2
Wire search endpoint + OWUI tool.

### Stage 3
Run the **40-query eval set (10 per class × 4 classes)** with graded qrels.

### Stage 4
Scale to the full `data/meme` corpus (~3,107 supported images); pin the corpus-count baseline as an ADR in `docs/decision_log.md`.

### Stage 5
Only then add optional captioning.

### Stage 6
Move to short clips.

---

## 13. Strict do-not-do list

Do not do these in Phase 0:
- do not start with video segmentation
- do not caption every meme up front
- do not introduce graph retrieval
- do not try to run 30B local VLM as a requirement
- do not build a custom frontend before OWUI is working
- do not overfit to hosted free tiers
- do not skip evals because “the search looks good”

---

## 14. Exit criteria to move from memes to clips

You may move from memes to short clips only when all of these are true:

- Full `data/meme` corpus ingested and corpus-count baseline pinned in `docs/decision_log.md`
- **40-query (10 per class × 4 classes)** eval set completed with graded qrels; `P0-G4` thresholds met
- hybrid retrieval demonstrably useful
- OCR path reliable enough and model fingerprint recorded in `ops.model_versions`
- reranker demonstrably improves results (≥ +2 pp nDCG@10 over RRF-only)
- OWUI tool flow works cleanly
- idempotent re-ingest proven
- indexes and backups documented; delete + backup/restore drill executed

---

## 15. References

- Qdrant hybrid queries and Query API: https://qdrant.tech/documentation/concepts/hybrid-queries/
- Qdrant named vectors: https://qdrant.tech/documentation/manage-data/vectors/
- Open WebUI OpenAI-compatible backend setup: https://docs.openwebui.com/getting-started/quick-start/connect-a-provider/starting-with-openai-compatible
- Open WebUI OpenAI/OpenAI-compatible setup: https://docs.openwebui.com/getting-started/quick-start/starting-with-openai
- PaddleOCR PP-OCRv5 multilingual docs: https://www.paddleocr.ai/main/en/version3.x/algorithm/PP-OCRv5/PP-OCRv5_multi_languages.html
- PaddleOCR PP-OCRv5 overview: https://www.paddleocr.ai/main/en/version3.x/algorithm/PP-OCRv5/PP-OCRv5.html
- BGE-M3 model card: https://huggingface.co/BAAI/bge-m3

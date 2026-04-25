# PHASE_0_PLAN.md - Meme searcher

**Version:** 2026-04-19
**Status:** Implementation-ready
**Owner:** primary builder (OpenCode)
**Reviewers:** Codex, Claude
**Primary outcome:** describe a meme in natural language and fetch it if it exists in `data/meme`
**Authoritative Phase 0 docs:** this file and `PHASE_0_TODO.md`
**Downstream:** unlocks `PHASE_1_PLAN.md`

---

## 1. Purpose

Phase 0 builds an **image-only local meme search engine** over the corpus already present at `data/meme`.

The point of Phase 0 is not "stand up infrastructure for later". The point is to ship a working retrieval system that lets the operator type things like:

- "the meme where the cat is yelling at the dinner table"
- "the one that says i am once again asking"
- "the drake meme about code review"
- "that meme where someone looks exhausted and done with life"

and get the actual image back when it exists in the local corpus.

If Phase 0 does not reliably retrieve memes from natural-language description, it has failed, even if all infrastructure is running.

## 2. Success Outcome

Phase 0 is successful when all of the following are true:

- The system indexes the supported files under `data/meme` recursively.
- A user can query by text description from the API and from Open WebUI.
- **The end-user deliverable is a working chat app:** the operator opens Open WebUI, types a natural-language description of a meme (text, vibe, or mixed visual+text intent), and the backend fetches the matching image from `data/meme` and renders it inline with the source path and thumbnail. No separate dev UI is required; OWUI is the product surface.
- Search results return the original image path and a thumbnail for quick inspection.
- The top results are usually correct on a curated local evaluation set built from the actual corpus.
- Re-running ingest is idempotent.

This is a **local single-user tool**. No public uploads, multi-tenancy, or hosted serving requirements are part of Phase 0.

## 3. Corpus Contract

Phase 0 starts with the local corpus at `data/meme`.

### 3.1 Starting source

- Initial source root: `data/meme`
- Recursive scan: yes
- Follow subdirectories: yes
- Hidden files: ignore unless they match the supported image extensions
- Symlinks: do not follow in Phase 0

### 3.2 Supported input formats

Phase 0 must ingest these file extensions:

- `.jpg`
- `.jpeg`
- `.png`
- `.webp`
- `.gif` using the first frame only
- `.jfif` treated as JPEG

### 3.3 Explicitly skipped in Phase 0

These must be logged as skipped, not treated as ingest failures:

- `.avif`
- `.heic`
- `.svg`
- `.pdf`
- `.html`
- `.js`
- `.mp3`
- `.mp4`
- `.mkv`
- `.zip`
- files with no extension

If support for any skipped type is later added, that is a documented scope change, not an ad hoc implementation choice.

Additional corpora may be added later, but the implementation must work correctly with `data/meme` first before any expansion.

### 3.4 Current starting corpus snapshot

As of **2026-04-19**, the local `data/meme` tree contains roughly:

- `3157` files total
- `3107` files in the supported Phase 0 image extensions
- `50` files outside the supported set

These counts are a verification baseline, not a hardcoded assumption. The implementation must scan the current filesystem state each run.

### 3.5 Inclusion rule

All supported images under `data/meme` are in scope, including legacy folders such as `Old Memes` and template folders. Do not invent category-based exclusions during implementation.

## 4. Scope

### In scope

- Local folder ingest from `data/meme`
- OCR on every supported image
- Dense and sparse text embeddings over OCR text
- Visual embeddings on every supported image
- Hybrid retrieval over OCR text plus visual semantics
- Local reranking
- FastAPI search API
- Open WebUI integration through LiteLLM
- Evaluation harness built from the actual local corpus
- Idempotent ingest and delete

### Out of scope

- S3 ingest
- Video ingest
- Audio or ASR
- Shot detection, segmentation, or timeline logic
- Public upload UI
- Multi-user auth
- Near-duplicate or perceptual dedupe
- Query-by-image UI
- Caption-everything pipelines

### Stretch only

- Optional caption generation for weak-OCR images, flag-off by default
- Query-by-image API support

## 5. Architecture

Phase 0 keeps the planned retrieval stack, but narrows it to the smallest form that solves the actual local problem.

### Required components

- PostgreSQL 17 as source of truth
- Qdrant with named vectors
- MinIO for thumbnails
- LiteLLM as the model gateway
- FastAPI backend
- Open WebUI as the operator UI
- PaddleOCR PP-OCRv5 for OCR
- BGE-M3 for dense plus sparse text embeddings
- SigLIP-2 So400m/16-384 for visual embeddings
- `jina-reranker-v2-base-multilingual` for reranking

### Data model principle

One indexed unit equals one image.

- `core.images` stores identity and file-level facts
- `core.image_items` stores searchable text and display metadata
- Qdrant stores one point per image with named vectors

## 6. Ingest Pipeline

```text
filesystem path under data/meme
  -> extension policy check (supported / skipped)
  -> SHA-256 of raw bytes
  -> image decode + metadata extract
  -> thumbnail generation (JPEG, max 512 px longest side)
  -> OCR
  -> OCR normalization
  -> BGE-M3 dense + sparse embedding
  -> SigLIP-2 visual embedding
  -> PostgreSQL upsert
  -> Qdrant upsert
```

### 6.1 Idempotency rule

The identity key is the SHA-256 of the image bytes.

- The same bytes found at multiple paths map to one `image_id`
- The first seen path becomes `source_uri`
- Later duplicate paths are recorded in metadata if desired, but do not create new image rows
- Re-running ingest over the same folder must not duplicate rows or vector points

### 6.2 OCR normalization contract

- Normalize Unicode with NFKC
- Lowercase for embedding text
- Collapse repeated whitespace
- Keep full OCR output in `ocr_full_text`
- Only exclude OCR tokens below confidence `0.6` from the embedding text
- Do not drop low-confidence tokens from the stored raw OCR box data

## 7. Query Pipeline

Search is text-first. The user should not need to know whether a query matches via OCR, semantic text, or visual semantics.

```text
query text
  -> normalize query
  -> classify query intent
  -> BGE-M3 dense + sparse encode
  -> SigLIP-2 text-tower encode for visual semantics
  -> Qdrant prefetch:
       text-dense
       text-sparse
       visual
  -> RRF fusion
  -> top 50 candidates
  -> local reranker
  -> top N response
```

### 7.1 Canonical query classes

Phase 0 uses exactly four classes:

1. `exact_text`
2. `fuzzy_text`
3. `semantic_description`
4. `mixed_visual_description`

Definitions:

- `exact_text`: the user remembers near-verbatim text from the meme
- `fuzzy_text`: the user remembers partial or slightly wrong OCR text
- `semantic_description`: the user describes the meaning or situation
- `mixed_visual_description`: the user combines subject, layout, and meaning such as "drake meme about code review"

These four classes are the only ones used in tests and eval. Do not create a fifth class during implementation.

### 7.2 Retrieval policy

- Always run dense and sparse text retrieval
- Always run SigLIP text-to-image retrieval for text queries
- Fuse with Reciprocal Rank Fusion
- Rerank the fused top 50
- Return the top 10 by default

## 8. API Contract

Phase 0 must define one canonical search contract and use it everywhere: backend tests, OWUI tool wiring, and eval runner.

### 8.1 `POST /search`

Request body:

```json
{
  "query": "the drake meme about code review",
  "limit": 10,
  "include_debug": false
}
```

Request rules:

- `query` is required, non-empty, UTF-8 text
- `limit` default is `10`, allowed range `1..20`
- `include_debug` default is `false`

Response body:

```json
{
  "query": "the drake meme about code review",
  "intent": "mixed_visual_description",
  "total_returned": 5,
  "hits": [
    {
      "rank": 1,
      "image_id": "img_sha256_...",
      "source_uri": "data/meme/Old Memes/Templates/Drake.jpg",
      "thumbnail_uri": "minio://thumbnails/ab/cd/ef.jpg",
      "ocr_excerpt": "code review ...",
      "retrieval_score": 0.4821,
      "rerank_score": 0.9123
    }
  ]
}
```

Response rules:

- Results are ordered by final reranked order
- Every hit includes `image_id`, `source_uri`, and `thumbnail_uri`
- `ocr_excerpt` may be empty if OCR is absent
- `retrieval_score` is the fused retrieval score before reranking
- `rerank_score` is present when reranking ran

### 8.2 SSE behavior

Phase 0 may stream the search flow, but the final payload schema must still match the non-streaming `SearchResponse`.

Allowed SSE events:

- `search_started`
- `retrieval_complete`
- `rerank_complete`
- `search_completed`
- `search_error`

The `search_completed` event payload must contain the same JSON shape as the normal `SearchResponse`.

### 8.3 Open WebUI tool contract

Open WebUI integration must call the same `POST /search` endpoint. Do not invent a second search format for the UI.

## 9. Data Model

### 9.1 `core.images`

| column | type | notes |
|---|---|---|
| `image_id` | TEXT PK | stable ID derived from SHA-256 |
| `sha256` | BYTEA UNIQUE | raw digest bytes |
| `source_uri` | TEXT | first observed filesystem path |
| `width` | INT | required |
| `height` | INT | required |
| `format` | TEXT | normalized format label |
| `ingested_at` | TIMESTAMPTZ | default `now()` |
| `metadata` | JSONB | optional |

### 9.2 `core.image_items`

| column | type | notes |
|---|---|---|
| `image_id` | TEXT PK FK | one row per image |
| `thumbnail_uri` | TEXT | MinIO object key or URI |
| `ocr_text` | TEXT | normalized OCR text used for embeddings |
| `ocr_full_text` | TEXT | full OCR text for debug and trigram recall |
| `ocr_boxes` | JSONB | list of `{text, conf, bbox}` |
| `caption_text` | TEXT NULL | stretch only |
| `caption_model` | TEXT NULL | stretch only |
| `has_ocr` | BOOL | required |
| `has_caption` | BOOL | required |
| `created_at` | TIMESTAMPTZ | default `now()` |

### 9.3 `ops.ingest_steps`

Required checkpoints per image:

- `hash`
- `decode`
- `thumbnail`
- `ocr`
- `embed_text`
- `embed_visual`
- `upsert_pg`
- `upsert_qdrant`

State values:

- `pending`
- `done`
- `skipped`
- `error`

### 9.4 Qdrant

- Collection alias: `memes`
- Physical collection: `memes_v1`
- Named vectors:
  - `text-dense` = 1024 dims, cosine
  - `text-sparse` = BGE-M3 sparse
  - `visual` = 1152 dims, cosine
- Required payload fields:
  - `image_id`
  - `source_uri`
  - `thumbnail_uri`
  - `format`
  - `width`
  - `height`
  - `has_ocr`
  - `has_caption`
  - `ingested_at`
  - `model_version`

## 10. Evaluation

The evaluation set must be built from actual files inside `data/meme`.

### 10.1 Eval size and split

Phase 0 uses a curated **40-query** eval set:

- 10 `exact_text`
- 10 `fuzzy_text`
- 10 `semantic_description`
- 10 `mixed_visual_description`

### 10.2 Labels

- Label against the local corpus only
- Graded relevance `0..3`
- At least top 10 candidates judged per query

### 10.3 Metrics

- `nDCG@10`
- `Recall@10`
- `Recall@50`
- `MRR`
- `top_1_hit_rate`
- `reranker_uplift_ndcg10`

### 10.4 Acceptance thresholds

Phase 0 is not done unless the baseline run on the local corpus meets all of these:

- `Recall@10 >= 0.90`
- `top_1_hit_rate >= 0.70`
- `reranker_uplift_ndcg10 >= 0.02`
- No exact-text query misses the correct meme outside the top 10

If the first full run misses these thresholds, the team tunes retrieval and reranking before declaring Phase 0 complete.

## 11. Gates

Gates are sequential. A later gate cannot close before every earlier gate is green.

### P0-G1 - Infra boots

- `docker compose up -d` is healthy
- Postgres schema applies cleanly
- Qdrant collection and alias exist
- LiteLLM config passes validation
- Open WebUI connects through LiteLLM
- README and `.env.example` are sufficient to boot from a fresh clone

### P0-G2 - Ingest works on the real corpus

- One image ingests end-to-end
- Re-running one image is idempotent
- Folder ingest over `data/meme` processes all supported files recursively
- Unsupported files are reported as skipped
- End-of-run summary reports success, skipped, duplicate, failed

### P0-G3 - Search serves (chat-app end-state)

- `POST /search` returns useful ranked hits for known queries
- Response contract matches the canonical schema
- Open WebUI can call the search tool
- Hits include source path and thumbnail
- **Chat-app walk-through passes:** in OWUI, the operator types a plain-English meme description, the tool call fires `POST /search`, and the rendered chat response shows the matching image from `data/meme` inline with the source path and thumbnail. The transcript from at least 5 such queries (one per intent class plus one general probe) is pasted into `docs/owui_integration.md` as the `P0-G3` evidence log before the gate is declared closed.

### P0-G4 - Retrieval quality is acceptable

- 40-query eval set exists
- Eval runner writes metrics to Postgres
- Baseline metrics meet the thresholds in section 10.4

### P0-G5 - Operations are safe

- Delete flow removes Postgres rows, Qdrant point, and thumbnail
- Backup and restore runbook is proven on a scratch stack
- Structured logging exists for ingest and API flows

### P0-G6 - Transition readiness

- `docs/phase1_short_clips_transition.md` exists
- Phase 1 carry-over interfaces are documented
- No video-specific code is required to use the Phase 0 meme searcher

## 12. Implementation Sequence

Implementation should happen in this order:

1. Infra and local boot
2. Schema and Qdrant bootstrap
3. Single-image ingest
4. Folder ingest over `data/meme`
5. Retrieval and reranking
6. FastAPI contract
7. Open WebUI integration
8. Evaluation harness
9. Delete plus backup plus restore hardening

If work starts in a different order, the builder must justify the deviation in `docs/decision_log.md`.

## 13. Deliverables

- `docker-compose.yml`
- `.env.example`
- `infra/postgres/001_schema.sql`
- `infra/qdrant/bootstrap.py`
- `infra/litellm/config.yaml`
- `vidsearch/ids.py`
- `vidsearch/ingest/images.py`
- `vidsearch/query/encoders.py`
- `vidsearch/query/intent.py`
- `vidsearch/query/retrieve_images.py`
- `vidsearch/query/rerank_images.py`
- `vidsearch/api/main.py`
- `vidsearch/api/contracts.py`
- `vidsearch/eval/queries_memes.yaml`
- `vidsearch/eval/runner.py`
- `vidsearch/eval/metrics.py`
- `docs/decision_log.md`
- `docs/runbook.md`
- `docs/owui_integration.md`
- `docs/phase1_short_clips_transition.md`

## 14. Dependencies and Assumptions

- Docker Desktop is available
- The repo is already a git repo; Phase 0 does not include `git init`
- Local model assets are expected under `K:\models\video_searcher`
- Hosted API keys are optional unless optional verification or captioning is enabled

## 15. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| OCR misses stylized meme text | High | rely on dense, sparse, and visual retrieval together |
| Local corpus contains messy non-image files | High | explicit skip policy and per-run scan summary |
| Duplicate files across folders inflate ingest | Medium | SHA-256 identity and duplicate short-circuit |
| Reranker adds latency without quality gain | Medium | track uplift and disable only if metrics justify it |
| Open WebUI tool wiring drifts from API schema | Medium | single canonical `POST /search` contract |

## 16. Reserved for Phase 1

These interfaces must stay reusable:

- `vidsearch/query/encoders.py`
- `vidsearch/query/retrieve_images.py`
- `vidsearch/query/rerank_images.py`
- `vidsearch/api/contracts.py`

Phase 1 extends them for short video clips. Phase 0 must not depend on any video-only concepts.

---

**Phase 0 exit sentence:** a local meme search engine that indexes `data/meme`, lets the operator describe a meme in text, and returns the image if it exists, with measured retrieval quality and idempotent ingest.

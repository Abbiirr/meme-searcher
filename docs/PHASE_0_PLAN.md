# PHASE_0_PLAN.md — Meme searcher (image-only)

**Version:** 2026-04-18
**Status:** Planning-ready; blocks all Phase 1+ work
**Owner:** primary builder (OpenCode)
**Reviewers:** Codex, Claude
**Upstream docs:** `../ARCHITECTURE.md`, `FINAL_PLAN.md`, `PHASE0_MEME_SEARCHER.md`, `CODEX_PROMPTS_PHASE0.md`
**Downstream:** unlocks `PHASE_1_PLAN.md`

---

## 1. Purpose

Phase 0 builds an **image-only meme search engine** over a large personal meme corpus. It exists to validate the retrieval spine that every later video phase depends on — **OCR, dense + sparse text retrieval, visual retrieval, RRF fusion, local reranking, OWUI ↔ LiteLLM ↔ backend integration, evaluation, idempotent ingest** — on a dataset where mistakes are cheap to spot and iterations are cheap to run. Nothing about video is built in this phase.

Phase 0 replaces the infrastructure-only "Phase 0" defined in the original final plan. It keeps that infra milestone as an internal checkpoint (P0-G1) and extends it to a real, useful product: a meme searcher that you actually use.

## 2. Scope

### In scope
- Personal meme and image corpus ingest from local folders and optional S3-compatible storage.
- OCR on every image.
- BGE-M3 dense + sparse text embeddings over OCR text.
- SigLIP-2 visual embeddings on every image.
- Hybrid retrieval in Qdrant with server-side RRF.
- Local reranking with `jina-reranker-v2-base-multilingual`.
- Optional VLM verification on top-5 (Gemini 2.5 Flash-Lite primary, local Qwen3-VL-8B fallback).
- Open WebUI as the primary frontend.
- LiteLLM as the model gateway.
- PostgreSQL 17 as the source of truth (image-first schema, video-compatible layout).
- Qdrant as the vector store with named vectors (`text-dense`, `text-sparse`, `visual`).
- MinIO for thumbnails and any artifact persistence.
- 50-query evaluation harness with graded relevance.
- Content-addressed image IDs via SHA-256.
- Fully idempotent re-ingest.

### Explicitly out of scope
- Video fetch / probe / remux.
- Audio or ASR.
- Shot detection, window segmentation.
- Long-video summaries.
- Graph retrieval.
- Multi-tenant hosting or public upload UI.
- CCTV-specific restoration or super-resolution.
- Captioning **every** image up front (only as a Phase 0 stretch, threshold-driven).
- Local 30B VLM dependency (the 30B Qwen3-VL UD-IQ2_XXS experiment is deferred to Phase 2).

## 3. Architecture delta from `../ARCHITECTURE.md`

Phase 0 is the same architecture with the video-specific branches removed and the segment unit collapsed to "one image = one indexed item".

### Removed for Phase 0
- `fetch` with `yt-dlp` / HTTP handling (folder and S3 only).
- `probe` / MKV remux.
- TransNetV2, PySceneDetect, overlapping windows.
- ASR (Parakeet, WhisperX).
- Caption backfill queue.
- Per-segment timeline citations.
- `ops.ingest_steps` for 12 video steps (retained for images but with a shorter step list).

### Retained
- PostgreSQL (same schemas: `core`, `ops`, `eval`, `feedback`).
- Qdrant with named vectors + RRF + payload indexes.
- MinIO for artifacts.
- LiteLLM gateway.
- Open WebUI frontend (pinned ≥ v0.6.35, Direct Connections disabled).
- FastAPI backend with SSE.
- BGE-M3 and SigLIP-2 stacks.
- `jina-reranker-v2-base-multilingual`.
- Optional Lane C hosted VLM verification.
- Content-addressed IDs (SHA-256 of image bytes, no segment_id math for Phase 0).

### Simplified data model
- `core.images` — one row per unique image SHA-256.
- `core.image_items` — OCR text, OCR boxes, optional caption, thumbnail URI, flags.
- Qdrant: one point per image with `text-dense`, `text-sparse`, `visual` named vectors.

**Forward compatibility rule:** the `core.images` / `core.image_items` split mirrors the future `core.videos` / `core.segments` split so Phase 1 can extend without data migrations.

## 4. Ingest pipeline

```text
image file (local folder or S3)
  -> SHA-256 (image identity)
  -> metadata extract (path, size, format, width, height)
  -> thumbnail generation (JPEG Q85, max 512px)
  -> PaddleOCR PP-OCRv5 (server det + rec)
  -> OCR normalization (lowercase, collapse whitespace, drop sub-0.6-confidence tokens from embed text only)
  -> BGE-M3 dense + sparse embeddings on OCR text
  -> SigLIP-2 So400m/16-384 visual embedding on image
  -> Postgres upsert (core.images, core.image_items, ops.ingest_steps)
  -> Qdrant upsert (single point, 3 named vectors, payload)
```

Stretch (flag-gated, default off): a caption-on-weak-OCR path routed through LiteLLM `vertical_caption` group, only for images where OCR text length is below a threshold or rank-1 retrieval confidence falls below a threshold.

## 5. Query pipeline

```text
query text (+ optional query image)
  -> intent parse (rule-based: exact-text | semantic | visual | mixed | ocr-fuzzy)
  -> BGE-M3 dense + sparse encode
  -> optional SigLIP-2 encode (if image query or visual intent detected)
  -> Qdrant Query API:
       prefetch text-dense limit=200,
       prefetch text-sparse limit=200,
       prefetch visual limit=200 (conditional)
       -> RRF fusion -> limit=50
  -> jina reranker -> top 20
  -> optional VLM verify on top 5 (Gemini 2.5 Flash-Lite via LiteLLM)
  -> "why this matched" snippet construction (OCR excerpt + score breakdown)
  -> stream through FastAPI /search SSE into OWUI
```

Five query types to support:
1. **Exact text** — "the meme with 'im tired boss'".
2. **Semantic paraphrase** — "the meme where someone is completely done with life".
3. **Pure visual** — "the cat screaming at dinner table meme".
4. **Mixed** — "the Drake meme about code reviews".
5. **OCR-fuzzy** — partial or misremembered text.

## 6. Data model

### `core.images`
| column | type | notes |
|---|---|---|
| `image_id` | TEXT PK | derived from SHA-256; stable identity |
| `sha256` | BYTEA UNIQUE | raw bytes, not hex |
| `source_uri` | TEXT | first observed location |
| `width`, `height` | INT | |
| `format` | TEXT | `jpeg` / `png` / `webp` / `gif` (first frame) |
| `ingested_at` | TIMESTAMPTZ | default `now()` |
| `metadata` | JSONB | optional free-form (exif, album, etc.) |

### `core.image_items`
| column | type | notes |
|---|---|---|
| `image_id` | TEXT FK | one row per image |
| `thumbnail_uri` | TEXT | MinIO key |
| `ocr_text` | TEXT | concatenated, high-confidence tokens only |
| `ocr_full_text` | TEXT | all tokens including low-confidence (used for surface trigram recall) |
| `ocr_boxes` | JSONB | list of `{text, conf, bbox}` |
| `caption_text` | TEXT NULL | optional |
| `caption_model` | TEXT NULL | |
| `has_ocr` | BOOL | |
| `has_caption` | BOOL | |
| `created_at` | TIMESTAMPTZ | |

### Qdrant
- Collection alias `memes` → physical `memes_v1`.
- Named vectors: `text-dense` (1024, BGE-M3 dense, cosine, int8 quant), `text-sparse` (BGE-M3 sparse, IDF modifier), `visual` (1152, SigLIP-2, cosine, binary quant).
- Payload indexes: `image_id`, `has_ocr`, `has_caption`, `format`, `width`, `height`, `ingested_at`, `model_version`.
- Same alias-cutover discipline as the video plan.

## 7. Test strategy

Phase 0 tests sit in four layers: **unit**, **integration**, **end-to-end vertical**, **regression eval**. Every PR runs layers 1–3; layer 4 runs nightly on the full corpus and on merge to `main`.

### 7.1 Unit tests
- `vidsearch/ids.py::image_id` — deterministic for identical bytes, disjoint on re-encoding.
- OCR normalizer — confidence thresholding, whitespace collapse, unicode NFKC, low-confidence exclusion from embed text but retention in `ocr_full_text`.
- Query intent parser — five query types land in the right bucket with a 20-example fixture set.
- BGE-M3 encoder wrapper — dense and sparse shapes/types.
- SigLIP-2 encoder wrapper — 1152-dimensional output, batch-safe.
- LiteLLM client wrapper — 429 advances fallback, never retries past the last provider.

### 7.2 Integration tests (with live local services via `docker compose`)
- Ingest one meme image end-to-end: rows appear in `core.images`, `core.image_items`, `ops.ingest_steps`; a single Qdrant point is present with three named vectors.
- Second invocation on the same image is a full cache hit: `ops.ingest_steps.state='done'` for every step, zero new Postgres rows, zero Qdrant upserts (or an idempotent upsert with identical payload).
- Batch ingest of 100 memes finishes without partial writes; per-file failures are logged and surface in an end-of-run summary.
- `/search` returns at least one hit for a query whose exact OCR text is present in the corpus.
- OWUI → LiteLLM → `/search` round-trip returns a grounded answer with thumbnail URIs.

### 7.3 End-to-end vertical
- **Stage 0 gate (P0-G1):** `docker compose up -d` boots healthy; Postgres schema applied; Qdrant collection and alias created; LiteLLM config passes `--check`; OWUI connects to LiteLLM and lists the Gemini-backed model group.
- **Stage 1 gate (P0-G2):** 1,000 memes ingested; spot-check 10 random memes; all five query types return correct rank-1 in a curated mini-set of 10 queries.
- **Stage 2 gate (P0-G3):** FastAPI `/search`, `/ingest/image`, `/ingest/folder`, `/feedback`, `/health` live; OWUI tool call succeeds.
- **Stage 3 gate (P0-G4):** 50-query eval set executed; metrics written to `eval.metrics`; baseline recorded.
- **Stage 4 gate (P0-G5):** scale to 10,000+ memes; re-run eval; metrics hold.

### 7.4 Regression eval
- 50 graded queries stored in `vidsearch/eval/queries_memes.yaml`.
- Split: 15 exact/OCR, 15 semantic, 10 visual, 10 mixed.
- Metrics: `nDCG@10`, `Recall@10`, `Recall@50`, `MRR`, `top-1 exact hit rate`, `reranker uplift over raw fusion`.
- Judges: seed with LLM judge (Gemini 2.5 Pro via LiteLLM) on a 20-query subset for calibration; the other 30 queries are hand-labelled. Both judges stored in `eval.qrels.judge` for bias analysis.
- CI gate (phase-internal, before P0-G5): a metric regression of more than 3 percentage points versus the previous stored run blocks merge unless the waiver is recorded in `docs/decision_log.md`.

## 8. Verification criteria (quantitative)

Phase 0 is **done** when all of the following hold simultaneously:

| Criterion | Target |
|---|---|
| Images indexed | ≥ 10,000 |
| Idempotent re-ingest | 0 duplicate rows, 0 redundant Qdrant points on second run |
| Eval set size | 50 queries, five classes |
| `nDCG@10` (reranked) | ≥ a project-chosen threshold; at minimum "clearly useful to the operator" with a documented score snapshot committed to `docs/decision_log.md` |
| Reranker uplift | ≥ +2 points `nDCG@10` over raw RRF on the same 50-query set |
| OWUI tool round-trip | < 20 s P95 (latency is not a hard constraint but a sanity cap) |
| Hosted-provider independence | Core retrieval path works with every Lane C provider disabled; only optional VLM verification is allowed to go dark |
| Backup/restore proof | Postgres `pg_dump` + Qdrant snapshot both round-trip on a scratch container |
| Delete/retract flow | Removing an image deletes its Postgres rows, Qdrant point, and thumbnail in one call |

## 9. Closing gates

Gates are ordered. A later gate cannot close until every earlier gate is green.

- **P0-G1 — Infra boots.** Compose stack healthy; DDL applied; Qdrant alias created; LiteLLM `--check` green; OWUI connects; `.env.example` covers every variable; README has a zero-to-boot quickstart.
- **P0-G2 — Ingest one, then many.** One image end-to-end through every Phase 0 step; idempotent re-run proven; 1,000-meme batch ingest with a written summary of failures.
- **P0-G3 — Search serves.** FastAPI endpoints live with Pydantic contracts; OWUI can call `/search` as an OpenAI-compatible tool; hits include thumbnail URI, OCR excerpt, scores, source URI.
- **P0-G4 — Rerank + eval.** Local reranker integrated; 50-query eval run recorded in `eval.runs` with a `config_hash`; reranker uplift documented.
- **P0-G5 — Scale + harden.** 10,000+ memes indexed; delete flow works; backup/restore proven; optional captioning is **flag-off by default** and only tested as a stretch path.
- **P0-G6 — Transition readiness.** `docs/phase1_short_clips_transition.md` drafted; every Phase 0 module has a re-use note for Phase 1; no scope-creep code merged (no ASR, no segmentation, no graph).

Only when P0-G1 through P0-G6 are all closed does Phase 1 begin.

## 10. Dependencies and sequencing

- **External dependencies:** Gemini API key (optional), OpenRouter key (optional), running Docker Desktop with WSL integration, 4060 Ti driver ≥ 566.
- **Internal dependencies:** Git initialised at the repo root (the repo is currently *not* a git repo); this is a one-time action before any Phase 0 work.
- **Model assets already on disk** (`K:\models\video_searcher`): `bge-m3`, `siglip2-so400m-patch16-384`, `PP-OCRv5_server_det`, `PP-OCRv5_server_rec`, `jina-reranker-v2-base-multilingual`, optional `Qwen3-VL-8B-Instruct-GGUF`. ASR directories are ignored in Phase 0.

## 11. Rollback / abort triggers

Abort Phase 0 and re-scope if **any** of the following hold after a real corpus test:

1. Hybrid retrieval quality on the 50-query set is materially worse than dense-only BGE-M3 retrieval alone (signals a Qdrant wiring bug or sparse-index regression).
2. Ingest cannot remain idempotent under real failures (signals a content-addressing bug).
3. PaddleOCR PP-OCRv5 is unusable on the target meme corpus (fallback: swap in `PP-OCR mobile` or an earlier PP-OCR release before changing architecture).
4. BGE-M3 sparse vectors drown out dense in RRF (tune the prefetch sizes and IDF modifier first; architecture change only if tuning does not recover).

## 12. Deliverables

- `docker-compose.yml`, optional `docker-compose.observability.yml`.
- `.env.example` with every variable.
- `infra/postgres/001_schema.sql` (image schema; video schema is a Phase 1 additive migration).
- `infra/qdrant/bootstrap.py` (idempotent `memes_v1` collection + alias).
- `infra/litellm/config.yaml` skeleton (at least `vertical_caption` group using Gemini Flash-Lite and OpenRouter Nemotron fallback, `verify`, `judge`).
- `vidsearch/` package: `ingest/images.py`, `query/retrieve_images.py`, `query/rerank_images.py`, `api/main.py`, `api/contracts.py`, `eval/queries_memes.yaml`, `eval/runner.py`, `eval/metrics.py`.
- 50-query eval set with graded labels.
- `docs/decision_log.md` updated with Phase 0 ADRs (image-first schema, OCR confidence threshold, reranker uplift baseline).
- `docs/runbook.md` quickstart covering boot, ingest, search, eval, backup/restore, delete.
- `docs/phase1_short_clips_transition.md` (Phase 0 exit artifact).

## 13. Risk register (Phase 0-specific)

| Risk | Severity | Mitigation |
|---|---|---|
| PaddleOCR PP-OCRv5 misreads stylised meme text | High | Keep `ocr_full_text` trigram-indexed for fuzzy recall; never gate retrieval on OCR perfection |
| BGE-M3 sparse vectors polluted by OCR noise | Medium | 0.6 confidence threshold for embedded text; `ocr_full_text` retained separately |
| SigLIP-2 underperforms on memes with heavy text overlay | Medium | RRF weighting favours text-dense + text-sparse when OCR text is present; visual prefetch still runs but does not dominate |
| Gemini or OpenRouter free tiers throttle mid-eval | Low | VLM verification is optional; eval uses offline judge runs cached in `eval.qrels` |
| 10,000-image ingest hits local disk I/O limits | Low | MinIO on the same NVMe; batch size tuned; thumbnail compression keeps disk pressure low |
| Re-encoded memes ingested as duplicates | Medium | SHA-256 on raw bytes detects byte-identical files; near-duplicates accepted as distinct IDs in Phase 0 (perceptual-hash dedupe is a Phase 2 item) |
| Scope creep toward video before exit gates close | High | Prompt 15 anti-scope-creep rule enforced; PRs adding ASR/segmentation/graph are rejected at review |

## 14. Interfaces reserved for Phase 1

To keep Phase 1 additive rather than disruptive, Phase 0 commits to these interfaces:

- `vidsearch/query/encoders.py` — encoder functions are format-agnostic; they work on text and images, and will be reused when video segments produce text/keyframes.
- `vidsearch/query/rerank.py` — reranker accepts `(query, list[candidate])` where `candidate` is a generic retrieval hit; adding video segments does not change the signature.
- `core.images` / `core.image_items` mirror `core.videos` / `core.segments`; Phase 1 adds a `kind` column on the segments side, not on images.
- Qdrant collection alias `memes` stays image-only. Phase 1 introduces a parallel `video_segments` alias; both coexist. A unified `all_media` alias is a Phase 3 decision, not Phase 1.

---

**Phase 0 exit sentence:** a meme search engine that the operator actually uses, with a reproducible eval, an idempotent ingest, an OWUI tool flow, and a written transition plan to Phase 1.

# PHASE_0_TODO.md — Meme searcher

**Version:** 2026-04-18
**Scope:** Phase 0 only. See `PHASE_0_PLAN.md` for rationale and gates.
**Execution guide:** `CODEX_PROMPTS_PHASE0.md` (Prompts 0–13 mirror this list).

> **Legend:** every task ends with the gate it unlocks. Testing tasks are explicit; they are not implied by the implementation tasks. Do not mark a gate closed until every task under it is checked.

---

## P0.0 — Repo bootstrap (unlocks P0-G1)

- [ ] `git init` at `K:\projects\video_searcher` and commit the current planning docs as the baseline.
- [ ] Add `.gitignore` (Python, Node, `/data`, `/models`, `.env`, `__pycache__/`, `*.gguf`).
- [x] Normalize planning doc names and keep them organized under `docs/`, with `ARCHITECTURE.md` remaining at the repo root.
- [ ] Record Phase 0 entry decisions in `docs/decision_log.md` (ADRs for: image-first schema, OCR confidence threshold, reranker choice, captioning-off-by-default).

## P0.1 — Infra (unlocks P0-G1)

- [ ] Write `docker-compose.yml` with: `postgres` (PG17), `redis` or `valkey`, `minio`, `qdrant`, `litellm`, `api` (FastAPI), `open-webui`.
- [ ] Write `.env.example` with every required variable: `GEMINI_API_KEY`, `OPENROUTER_API_KEY`, `LITELLM_MASTER_KEY`, `REDIS_PASSWORD`, `DATABASE_URL`, `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`, `QDRANT_URL`, `OPEN_WEBUI_SECRET_KEY`.
- [ ] Pin Open WebUI to `ghcr.io/open-webui/open-webui:v0.6.35+` and disable Direct Connections via config.
- [ ] Add healthchecks for every service.
- [ ] Write optional `docker-compose.observability.yml` (profile) with Langfuse. **Do not enable by default in Phase 0.**
- [ ] Write `infra/litellm/config.yaml` skeleton with `vertical_caption`, `verify`, `judge` groups (Gemini Flash-Lite primary, OpenRouter Nemotron Nano 12B v2 VL :free fallback, local `qwen3-vl-8b` last).
- [ ] README quickstart: from fresh clone to `docker compose up -d` healthy in under 10 minutes.
- [ ] **TEST:** `litellm --config infra/litellm/config.yaml --check` returns green.
- [ ] **TEST:** OWUI can list the `vertical_caption` model group through LiteLLM.
- [ ] **TEST:** a curl `POST /v1/chat/completions` through LiteLLM returns a Gemini reply.

## P0.2 — Database schema (unlocks P0-G1)

- [ ] Write `infra/postgres/001_schema.sql` with schemas `core`, `ops`, `eval`, `feedback`.
- [ ] Tables: `core.images`, `core.image_items`, `ops.jobs`, `ops.ingest_steps`, `ops.model_versions`, `eval.queries`, `eval.qrels`, `eval.runs`, `eval.run_results`, `eval.metrics`, `feedback.events`.
- [ ] Indexes: `images_sha256_idx`, `image_items_image_id_idx`, trigram GIN on `ocr_full_text`, JSONB path ops on `metadata`.
- [ ] Seed `ops.model_versions` with `bge-m3`, `siglip-2-so400m-384`, `paddleocr-pp-ocrv5`, `jina-reranker-v2-base-multilingual`.
- [ ] **TEST:** init container applies the schema on a fresh Postgres volume without errors.
- [ ] **TEST:** schema round-trips through `pg_dump` / restore on a scratch container.

## P0.3 — Qdrant bootstrap (unlocks P0-G1)

- [ ] Write `infra/qdrant/bootstrap.py` — idempotent collection creation.
- [ ] Collection `memes_v1` with named vectors `text-dense` (1024, cosine, int8 quant), `text-sparse` (IDF modifier), `visual` (1152, cosine, binary quant, always_ram).
- [ ] Payload indexes: `image_id` (keyword), `has_ocr` (bool), `has_caption` (bool), `format` (keyword), `width` (integer), `height` (integer), `ingested_at` (integer), `model_version` (keyword).
- [ ] Alias `memes` → `memes_v1`.
- [ ] **TEST:** second run of `bootstrap.py` is a no-op (no alias swap, no collection recreate).
- [ ] **TEST:** snapshot + restore round-trip on a scratch Qdrant container.

## P0.4 — Ingest one image (unlocks P0-G2)

- [ ] `vidsearch/ids.py::image_id(bytes) -> str` using SHA-256.
- [ ] `vidsearch/ingest/images.py::ingest_image(path) -> ImageIngestResult`.
- [ ] Integrate PaddleOCR PP-OCRv5 with server det + rec models from `K:\models\video_searcher`.
- [ ] Integrate BGE-M3 for dense + sparse embeddings.
- [ ] Integrate SigLIP-2 So400m/16-384 for visual embeddings.
- [ ] Thumbnail generation (JPEG Q85, max 512px) written to MinIO under deterministic keys.
- [ ] Write `ops.ingest_steps` checkpoints for each sub-step (`hash`, `ocr`, `embed_text`, `embed_visual`, `upsert_pg`, `upsert_qdrant`).
- [ ] CLI `python -m vidsearch.ingest.images <path>` for a single image.
- [ ] **TEST:** unit — `image_id` is deterministic for identical bytes, distinct for re-encoded images.
- [ ] **TEST:** unit — OCR normalizer keeps full text in `ocr_full_text`, drops sub-0.6 tokens only from the embedder input.
- [ ] **TEST:** integration — one image ends up in `core.images`, `core.image_items`, and one Qdrant point with three named vectors.
- [ ] **TEST:** idempotency — second invocation on the same path is a cache hit across every `ops.ingest_steps` row.

## P0.5 — Batch ingest (unlocks P0-G2)

- [ ] Folder recursion with glob filters for image formats (`.jpg`, `.jpeg`, `.png`, `.webp`, `.gif`).
- [ ] Skip duplicates by SHA-256 lookup before work.
- [ ] Progress logging to `ops.jobs` + `ops.ingest_steps`.
- [ ] Continue-on-failure semantics; per-file errors go to `ops.jobs.error` with structured reason.
- [ ] End-of-run summary: counts of success, skipped (cached), failed with reasons.
- [ ] **TEST:** integration — 100-image folder ingest writes exactly 100 image rows; re-running writes zero.
- [ ] **TEST:** integration — forced IO failure on one file does not halt the batch.
- [ ] **TEST:** manual — 1,000-meme ingest completes and spot-check sampling confirms presence.

## P0.6 — Retrieval (unlocks P0-G3)

- [ ] `vidsearch/query/encoders.py` — BGE-M3 dense + sparse, SigLIP-2 text and image towers.
- [ ] `vidsearch/query/intent.py` — rule-based five-class intent classifier.
- [ ] `vidsearch/query/retrieve_images.py` — Qdrant Query API call with three prefetches and RRF fusion.
- [ ] Payload filter plumbing (`has_ocr`, `format`, `ingested_at`, etc.).
- [ ] Return raw candidate list with `retrieval_score` intact for debugging.
- [ ] **TEST:** unit — intent classifier passes the 20-example fixture set.
- [ ] **TEST:** integration — exact-text query returns the known-good rank-1 hit on a 100-meme fixture.
- [ ] **TEST:** integration — semantic paraphrase query returns the known-good hit in the top 10.
- [ ] **TEST:** integration — image-by-example query returns the known-good hit in the top 5.

## P0.7 — Reranking (unlocks P0-G4)

- [ ] `vidsearch/query/rerank_images.py` — `jina-reranker-v2-base-multilingual` over fused top 50 → top 20.
- [ ] Expose both `retrieval_score` and `rerank_score` on every hit.
- [ ] Benchmark harness `scripts/rerank_benchmark.py` that compares retrieval-only vs reranked on the curated mini-set.
- [ ] **TEST:** integration — rerank uplift ≥ +2 `nDCG@10` points over raw fusion on the mini-set (20 queries) before the full 50 set exists.

## P0.8 — FastAPI (unlocks P0-G3)

- [ ] `vidsearch/api/main.py` — FastAPI app with CORS for OWUI host.
- [ ] Endpoints: `GET /health`, `POST /ingest/image`, `POST /ingest/folder`, `POST /search`, `POST /feedback`, `DELETE /image/{image_id}`.
- [ ] `vidsearch/api/contracts.py` — Pydantic models for every request and response.
- [ ] SSE streaming on `/search` for the answer synthesis step (matches the video plan for continuity).
- [ ] OpenAPI schema published at `/openapi.json` so OWUI auto-imports the tool.
- [ ] **TEST:** contract — round-trip `SearchResponse` JSON parses back into the Pydantic model.
- [ ] **TEST:** integration — `DELETE /image/{id}` removes Postgres rows, the Qdrant point, and the thumbnail.

## P0.9 — OWUI integration (unlocks P0-G3)

- [ ] `docs/owui_integration.md` — LiteLLM connection settings, tool registration walkthrough, screenshots optional.
- [ ] Register `/search` as an OWUI tool using the OpenAI-compatible tool schema.
- [ ] Wire a "memes" workspace in OWUI with the tool attached.
- [ ] Optional: a tiny dev-only retrieval-internals view served from the same FastAPI app under `/debug/*` (read-only, not linked from OWUI).
- [ ] **TEST:** manual — a natural-language query entered in OWUI produces a grounded answer with thumbnail links.
- [ ] **TEST:** manual — clicking a thumbnail opens the source image.

## P0.10 — Evaluation harness (unlocks P0-G4)

- [ ] `vidsearch/eval/queries_memes.yaml` with 50 queries, five-class split (15 exact/OCR, 15 semantic, 10 visual, 10 mixed).
- [ ] Hand-label graded relevance for each query against the 10,000-meme corpus (grades 0–3, top 10 per query).
- [ ] LLM-judge pass (Gemini 2.5 Pro via LiteLLM) on a 20-query calibration subset; store as a separate `eval.qrels.judge='llm'` record.
- [ ] `vidsearch/eval/runner.py` — executes the full set and writes to `eval.runs`, `eval.run_results`, `eval.metrics`.
- [ ] `vidsearch/eval/metrics.py` — `nDCG@10`, `Recall@10`, `Recall@50`, `MRR`, top-1 exact hit rate, reranker uplift.
- [ ] `docs/eval_protocol.md` — how to add a new query, re-grade, re-run.
- [ ] **TEST:** unit — metric computations match a hand-worked fixture.
- [ ] **TEST:** integration — end-to-end run lands in `eval.metrics` with a unique `config_hash`.
- [ ] **GATE:** capture the baseline metrics snapshot in `docs/decision_log.md`.

## P0.11 — Optional captioning (stretch; flag-off by default)

- [ ] `vidsearch/ingest/caption.py` — captions only for images with `ocr_text` length below a threshold **or** retrieval confidence below a threshold on a referenced query set.
- [ ] Route through LiteLLM `vertical_caption` group.
- [ ] Store caption text and model version in `core.image_items.caption_text`, `caption_model`.
- [ ] Feature flag `VIDSEARCH_ENABLE_CAPTIONS=false` by default.
- [ ] **TEST:** integration — with flag off, no caption rows are created.
- [ ] **TEST:** integration — with flag on, captioned images re-enter the index under a new `model_version` without duplicating image rows.

## P0.12 — Hardening (unlocks P0-G5)

- [ ] Delete flow: `DELETE /image/{image_id}` removes Postgres, Qdrant, MinIO thumbnail atomically (best-effort; failures logged to `ops.jobs.error`).
- [ ] Backup docs: `pg_dump` schedule, Qdrant `snapshot` schedule, MinIO `mc mirror` to an external drive if available.
- [ ] Restore drill: a runbook that boots a scratch stack, applies a dump, and verifies a sample query.
- [ ] Structured JSON logging enabled on FastAPI, LiteLLM, and ingest workers.
- [ ] README upgraded to an operator quickstart (zero → search) and a retreat recipe (teardown, wipe, rebuild).

## P0.13 — Transition artifact (unlocks P0-G6)

- [ ] `docs/phase1_short_clips_transition.md` — which Phase 0 modules carry over unchanged, what the minimal Phase 1 delta is (ASR, short segmentation, keyframe extraction, video metadata), and which Phase 0 module signatures become interfaces Phase 1 extends.
- [ ] Reviewer sign-off from Codex and Claude on the transition doc.

---

## Cross-cutting rules

- [ ] Every PR under Phase 0 runs unit + integration + mini-set (20 queries) before merge.
- [ ] Every merge to `main` that touches ingest or retrieval runs the full 50-query eval and blocks on a >3pp `nDCG@10` regression.
- [ ] No Phase 1 code (ASR, segmentation, video fetch, graph retrieval) is merged while Phase 0 is open.
- [ ] Every hosted provider call is logged with provider id, model name, timestamp, and a hash of the input payload.

## Exit checklist (mirrors `PHASE_0_PLAN.md` §8–§9)

- [ ] 10,000+ memes indexed.
- [ ] 50-query eval set labelled and executed; baseline `nDCG@10` recorded with a `config_hash`.
- [ ] Reranker uplift ≥ +2 `nDCG@10` points recorded.
- [ ] Idempotent re-ingest proven on the full corpus (no duplicate rows or points).
- [ ] OWUI tool flow returns grounded meme answers with thumbnails and source URIs.
- [ ] Backup/restore drill logged.
- [ ] Delete flow proven against a sample image.
- [ ] `docs/phase1_short_clips_transition.md` exists and is signed off.

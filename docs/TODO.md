# TODO.md — Strict prioritized implementation checklist

> **2026-04-18 update:** Phase 0 (meme searcher, image-only) is now the first implementation step. Priority 0 covers the shared infra bootstrap; **Priority 0A** covers the Phase-0-specific meme work; Priority 1+ maps to Phase 1 (short-clip vertical slice) and onward. Detailed per-phase checklists live in `PHASE_0_TODO.md` … `PHASE_5_TODO.md`.

## Priority 0 — Bootstrap and freeze (shared infra)
- [ ] Keep `ARCHITECTURE.md` at the project root and the planning set organized under `docs/`.
- [ ] Create `docs/decision_log.md` with ADRs for: Postgres, Qdrant, Prefect, LiteLLM, OWUI, Qwen3-VL vs Qwen3.6, TurboQuant rejection.
- [ ] Create `.env.example` with all required variables.
- [ ] Write `docker-compose.yml` for: Postgres, Redis/Valkey, MinIO, Qdrant, LiteLLM, Prefect server, Prefect worker, FastAPI API, Open WebUI, optional vLLM, optional observability profile.
- [x] Pin Open WebUI to the current patched stable version (`v0.9.1`) and keep Direct Connections disabled.
- [ ] Apply Postgres schema.
- [ ] Bootstrap Qdrant collection + alias.
- [ ] Validate LiteLLM config with `litellm --check`.
- [ ] Confirm OWUI connects to LiteLLM and can list model groups.

## Priority 0A — Phase 0 meme searcher (image-only; blocks Priority 1)
_Full checklist in `PHASE_0_TODO.md`. Closing gates P0-G1…G6 live in `PHASE_0_PLAN.md` §9._
- [ ] Apply image-only schema: `core.images`, `core.image_items` (mirrors forward-compatible with `core.videos` / `core.segments`).
- [ ] Bootstrap Qdrant collection `memes_v1` with named vectors `text-dense` (BGE-M3), `text-sparse` (BGE-M3 SPLADE), `visual` (SigLIP-2 So400m/16-384); alias `memes` points at `memes_v1`.
- [ ] Implement `vidsearch/ids.py` content-addressed `image_id` (SHA-256 of canonical bytes) plus tests.
- [ ] Implement single-image ingest: fetch → probe → PaddleOCR PP-OCRv5 → BGE-M3 dense+sparse on `ocr_text` → SigLIP-2 visual → Postgres upsert → Qdrant upsert; second run is a full cache hit.
- [ ] Implement batch ingest of the `data/meme` corpus (~3,107 supported images) with Prefect concurrency capped at the single-GPU budget; pin the corpus-count baseline (seen / supported / ingested / duplicate / skipped / failed) as an ADR in `docs/decision_log.md` after the first full run.
- [ ] Implement `/search` with Qdrant prefetch (dense + sparse + visual) + server-side RRF; image-only grouping off.
- [ ] Add `jina-reranker-v2-base-multilingual` local rerank; record uplift vs RRF-only.
- [ ] Register `/search` as an OWUI tool; render thumbnails + grounded citations.
- [ ] Build **40-query meme eval set — exactly 10 per class × 4 classes: `exact_text`, `fuzzy_text`, `semantic_description`, `mixed_visual_description`** — with graded qrels (`0..3` for at least top-10 candidates per query) referencing real files in `data/meme`; record baseline `nDCG@10` with a `config_hash`. Source of truth: `PHASE_0_PLAN.md` §10.1.
- [ ] **Gate P0-G3:** reranker uplift ≥ +2 pp over RRF-only baseline; if not, investigate before promoting.
- [ ] Optional: caption backfill via Lane C (`Gemini 2.5 Flash-Lite`) behind a feature flag; captions feed BGE-M3 only — never required for first-pass retrieval.
- [ ] Prove backup/restore drill for `core.images` + Qdrant snapshot; document in `docs/phase0_restore.md`.
- [ ] Prove delete / retract flow: `/image/{id}` DELETE removes Postgres + Qdrant + MinIO artifacts; tombstone row in `ops.purges`.
- [ ] Write `docs/phase1_short_clips_transition.md` — the forward-compatibility contract Phase 1 must honour.

## Priority 1 — Lane A local stable core (Phase 1 — video vertical slice)
- [ ] Implement `vidsearch/ids.py` content-addressed segment ID function plus tests.
- [ ] Implement `fetch` for `file://`, `s3://`, and `https://` / `yt-dlp`.
- [ ] Implement `probe` with MKV remux fallback.
- [ ] Implement TransNetV2 shot segmentation.
- [ ] Implement optional PySceneDetect refinement.
- [ ] Implement overlapping window generation.
- [ ] Implement keyframe extraction.
- [ ] Implement Parakeet TDT 0.6B v3 ASR.
- [ ] Implement WhisperX fallback routing.
- [ ] Implement PaddleOCR PP-OCRv5.
- [ ] Implement BGE-M3 dense + sparse + multivector embeddings.
- [ ] Implement SigLIP-2 visual embeddings on shot keyframes only.
- [ ] Implement Postgres writes for videos, segments, jobs, ingest steps.
- [ ] Implement Qdrant upsert for named vectors + sparse + multivector.
- [ ] Prove idempotent re-run on the same video.
- [ ] Prove different re-encodes create a new ID space.

## Priority 2 — Query path
- [ ] Implement query intent parsing.
- [ ] Implement BGE-M3 query encoders.
- [ ] Implement optional SigLIP-2 image query path.
- [ ] Implement Qdrant hybrid retrieval with prefetch + RRF + group_by.
- [ ] Implement local rerank with `jina-reranker-v2-base-multilingual`.
- [ ] Implement optional multivector rescore.
- [ ] Implement FastAPI `/search` SSE endpoint.
- [ ] Register `/search` as a tool endpoint for OWUI.

## Priority 3 — Vertical slice proof
- [ ] Ingest one 5-minute sample video end to end.
- [ ] Run the same ingest again and confirm full cache hit.
- [ ] Execute a real search query and return timestamped segments.
- [ ] Stream a grounded answer through OWUI.
- [ ] Build the first 20-query eval set.
- [ ] Record baseline `nDCG@10`.

## Priority 4 — Lane C hosted heavy path
- [ ] Create LiteLLM groups: `vertical_caption`, `verify`, `synthesis-long`, `judge`.
- [ ] Add Redis-backed per-provider daily budget counters.
- [ ] Implement `caption:queue` scheduler.
- [ ] Verify fallback order under forced 429 conditions.
- [ ] Add provider logging and cost/usage traces.

## Priority 5 — Lane B validation
- [ ] Download `unsloth/Qwen3-VL-30B-A3B-Instruct-GGUF` at `UD-IQ2_XXS` plus `mmproj-BF16.gguf`.
- [ ] Download Qwen3-VL-8B fallback artifacts.
- [ ] Download / prepare MiniCPM-V 4.5 int4.
- [ ] Run Gate G1 load test on all candidates.
- [ ] Run Gate G2 VRAM test.
- [ ] Run Gate G3 quality comparison against hosted baseline captions.
- [ ] Run Gate G4 throughput test.
- [ ] Run Gate G5 stability stress loop.
- [ ] Promote at most one Lane B model into LiteLLM config.

## Priority 6 — Scale and evaluation
- [ ] Start bulk ingest on the real corpus.
- [ ] Start caption backfill queue.
- [ ] Grow eval set to 100+ queries across the four canonical intent classes (`exact_text`, `fuzzy_text`, `semantic_description`, `mixed_visual_description`).
- [ ] Add reranker A/B comparisons.
- [ ] Add CI regression checks on a frozen 50-video subset.

## Priority 7 — Graph and benchmark spike
- [ ] Time-box Apache AGE + LightRAG spike to 2 weeks.
- [ ] Benchmark graph-augmented retrieval on entity-heavy queries.
- [ ] Benchmark NVIDIA VSS / LVS-style summarization against local prompts.
- [ ] Keep only what shows measurable lift.

## Priority 8 — Hardening
- [ ] Add snapshots and restore tests for Qdrant.
- [ ] Add Postgres backup + restore flow.
- [ ] Add feedback ingestion and weekly reranker fine-tune pipeline.
- [ ] Add second GPU split if hardware becomes available.
- [ ] Prepare Helm / Kubernetes manifests.

## Absolute rules
- [ ] Do not make captions mandatory for first-pass retrieval.
- [ ] Do not treat provider quotas as guaranteed.
- [ ] Do not promote Lane B models before they pass validation.
- [ ] Do not replace OWUI with a custom frontend unless OWUI proves inadequate.
- [ ] Do not reopen architecture unless retrieval quality, idempotency, or Lane C viability clearly fail.

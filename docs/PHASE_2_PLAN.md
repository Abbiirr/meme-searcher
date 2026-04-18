# PHASE_2_PLAN.md — Corpus scale, Lane B validation, caption backfill

**Version:** 2026-04-18
**Status:** Blocked on Phase 1 exit (gates P1-G1…G6 closed)
**Upstream:** `PHASE_1_PLAN.md`, `../ARCHITECTURE.md` §11–12, §16, §25
**Downstream:** unlocks `PHASE_3_PLAN.md`

---

## 1. Purpose

Phase 2 turns the single-video vertical slice into a working archive. Three things happen in parallel:

1. **Bulk ingest** of the real corpus (thousands of videos, multiple TB).
2. **Caption backfill** through the rate-aware LiteLLM scheduler — the first time Lane C operates at scale.
3. **Lane B validation** (G1–G5) on the local VLM candidates: `Qwen3-VL-30B-A3B UD-IQ2_XXS`, `Qwen3-VL-8B-Instruct`, `MiniCPM-V 4.5 int4`. **At most one** is promoted.

Phase 2 does not change the retrieval spine. It stresses the ingest and captioning paths, proves that the single-GPU discipline holds under real load, and documents the Lane B verdict.

## 2. Scope

### In scope
- Bulk corpus ingest via Prefect queues; resumable on crash; `ops.ingest_steps` drives every retry.
- `caption:queue` Redis ZSET + per-provider-per-day quota counters.
- `vidsearch/flows/caption_backfill.py` long-lived Prefect flow.
- LiteLLM fallback chain for `vertical_caption`: Gemini Flash-Lite → OpenRouter Nemotron Nano 12B v2 VL :free → Groq Llama-4-Scout on pre-sampled frames → local Lane B Qwen3-VL (if promoted).
- Lane B gates G1–G5 executed and documented.
- Perceptual-hash de-duplication for the image corpus (Phase 0 was SHA-only; Phase 2 adds near-duplicate clustering as a best-effort optimisation, not a retrieval change).
- Langfuse observability profile enabled (`--profile observability`).
- Reindex workflow for the first model upgrade (proves alias cutover).
- Capture of operational telemetry: ingest throughput, caption RPD consumption, GPU utilisation.

### Explicitly out of scope
- Reranker A/B tuning (Phase 3).
- 100-query eval set (Phase 3).
- Graph retrieval (Phase 4).
- CCTV noise-tolerance work (Phase 4).
- Multi-GPU (Phase 5).

## 3. Architecture delta from Phase 1

### Added
- `vidsearch/flows/caption_backfill.py` — scheduler with `quota:<provider>:<YYYY-MM-DD>` Redis hashes and `caption:queue` ZSET.
- `vidsearch/ingest/caption/{hosted,local}.py` — LiteLLM-routed hosted captioning and optional local Lane B captioning.
- `vidsearch/validation/lane_b/` — automated G1–G5 runners plus the frozen validation set (`eval/lane_b/200_keyframes.jsonl`, `eval/lane_b/50_clips.jsonl`).
- Optional `vllm` service in `docker-compose.yml` behind `--profile lane-b`.
- Langfuse service(s) in `docker-compose.observability.yml`; LiteLLM `success_callback: ["langfuse"]` wired.
- Reindex flow `vidsearch/flows/reindex.py` exercising the alias-cutover discipline.
- Perceptual-hash dedupe for the image corpus (pHash + near-duplicate cluster rollup in `core.images.metadata.near_dup_cluster`).

### Changed
- `ops.model_versions` becomes the single trigger for reindex decisions; every Phase 2 model update writes a row here.
- Caption columns in `core.segments` are now populated by the backfill flow rather than skipped.

## 4. Caption backfill design

### Queue
- `caption:queue` — Redis sorted set; members are `segment_id`, score is `priority` (recent ingest gets a lower score → earlier pop).
- Reconciliation flow every 6 hours: find segments with `caption_text IS NULL` and missing from the queue → enqueue.

### Provider chain (configurable per deployment)
1. `caption-gemini-flash-lite` — 1,000 RPD free tier.
2. `caption-or-nemotron-free` — 1,000 RPD with $10 top-up, 50 RPD without.
3. `caption-groq-scout` — 1,000 RPD, 30 RPM.
4. `caption-vllm-qwen3vl-8b` or `caption-vllm-qwen3vl-30b-aw3` — only if Lane B is promoted.

### Budget counters
- `quota:<provider>:<YYYY-MM-DD>` — Redis hash; atomic INCR with TTL to next UTC midnight.
- Hard ceilings in `infra/litellm/config.yaml`; scheduler reads those numbers, not in-code constants.

### Failure handling
- 429 → advance to next provider (no retry at the same provider).
- 5xx or network → retry twice with exponential backoff at the same provider before advancing.
- All-providers-exhausted → `sleep_until_next_utc_midnight()`.

### Captioning policy
- **Only segments with `has_speech OR has_ocr` are deprioritised** (they already have signal). Segments with neither get top priority because captions add the most value there.
- Caption texts land in `core.segments.caption_text` and trigger a re-index of `text-dense` + `text-sparse` for that segment (the existing payload is updated in-place; Qdrant upsert is idempotent).
- Multivector `text-colbert` is **not** recomputed on caption-only updates in Phase 2 (deferred until it is proven to matter in Phase 3 A/B).

## 5. Lane B validation

### Candidates (in priority order)
1. `unsloth/Qwen3-VL-30B-A3B-Instruct-GGUF` at `UD-IQ2_XXS` + `mmproj-BF16.gguf` (stretch).
2. `Qwen3-VL-8B-Instruct` — either `cyankiwi/Qwen3-VL-8B-Instruct-AWQ-4bit` on vLLM 0.11+ with `awq_marlin`, or `unsloth/Qwen3-VL-8B-Instruct-GGUF:UD-Q4_K_XL` + `mmproj-F16.gguf` on llama.cpp (Oct-30-2025+ build).
3. `openbmb/MiniCPM-V-4_5-int4` via Transformers (video-efficiency experiment).

### Gates (re-stated from `../ARCHITECTURE.md` §12)
- **G1 — Loads.** Cold start + first caption in < 10 minutes, no OOM at idle.
- **G2 — VRAM under load.** 50 keyframes back-to-back at batch=1, 8K ctx, T=0.7; peak `nvidia-smi` ≤ 14.5 GB.
- **G3 — Quality.** 200-keyframe caption run; Gemini 2.5 Pro judge; relevance non-inferior on ≥ 70% of keyframes; hallucination rate ≤ 10%.
- **G4 — Throughput.** 50-clip batch sustains ≥ 5 captions/minute over 30 consecutive minutes.
- **G5 — Stability.** 10,000-caption stress loop; zero OOM, zero CUDA illegal memory, zero >5-minute hangs.

### Promotion rule
- Pass all five gates → write a row in `ops.model_versions` with `family='lane-b-captioner'` and add the model to the `vertical_caption` chain in `infra/litellm/config.yaml`.
- Fail any gate → document in `docs/lane_b_validation.md` and skip. If every candidate fails, Lane B stays empty and the caption chain ends at Groq Scout. **This is an acceptable outcome.**

### Independence rule
- Lane B validation runs only in `make serve-mode`. The entire validation harness must pause ingest or run on a separate window.

## 6. Test strategy

### 6.1 Unit
- Budget counter: atomic increment, TTL semantics, midnight reset.
- Provider picker: advances on 429, respects RPD caps, short-circuits when a counter hits the cap.
- Caption payload normalization (trim, language tag, optional frame reference).

### 6.2 Integration
- Forced-429 test: hit Gemini with a provider that responds 429 and observe the next provider absorb the load.
- Backfill flow under a synthetic 10,000-segment workload.
- Langfuse trace export: one caption call shows full trace with cost.
- Perceptual-hash dedupe on a controlled near-duplicate image set (10 near-dupes → 1 cluster).
- Reindex flow: bump `bge-m3` model version row, run reindex, confirm alias atomic swap and rollback within 48 hours.

### 6.3 End-to-end
- Ingest 100 real videos in a single Prefect run; median per-video wall clock documented.
- Run caption backfill for 24 hours on the resulting segment set; RPD caps respected.
- Run G1–G5 on the top Lane B candidate; land the verdict in `docs/lane_b_validation.md`.

### 6.4 Regression eval
- Phase 0 50-query meme eval continues to pass (no image-search regression).
- Phase 1 20-query video eval continues to pass.
- A 30-query Phase 2 "caption lift" eval compares retrieval quality with captions present vs absent (to prove the captions improve `nDCG@10` on the "what does this scene look like" class — if they do not, captions are still useful, but we document the finding).

## 7. Verification criteria

| Criterion | Target |
|---|---|
| Real corpus indexed | ≥ 500 videos (full corpus goal is Phase 3 exit, not here) |
| Caption coverage | ≥ 80% of segments have `caption_text` **OR** are justified as "captioning not worthwhile" (has_speech AND has_ocr) |
| Lane B verdict | Documented: promoted / rejected / no candidate tested |
| RPD adherence | Zero provider exceeds its configured RPD in the 24-hour backfill run |
| Langfuse traces | Every captioning call appears with provider, cost, latency |
| Reindex drill | One model version bumped end-to-end with zero downtime on `video_segments` alias |
| Phase 0 + Phase 1 eval | No regression (>3pp) on either |

## 8. Closing gates

- **P2-G1 — Bulk ingest.** Prefect ingest handles 500+ videos; `ops.ingest_steps` is the sole retry surface; no manual intervention needed per-video.
- **P2-G2 — Caption scheduler.** `caption:queue` + quota counters alive; forced-429 test passes; 24-hour run logs show RPDs respected.
- **P2-G3 — Lane B G1–G5.** At least one candidate has a complete G1–G5 result logged. Promotion is optional; **the verdict is mandatory.**
- **P2-G4 — Observability.** Langfuse captures caption traces; a cost dashboard displays per-provider spend.
- **P2-G5 — Reindex drill.** One reindex cycle completed on `video_segments` using the alias-cutover path; old collection kept for 48h rollback.
- **P2-G6 — No regressions.** Phase 0 and Phase 1 eval suites pass on the Phase 2 build.

## 9. Dependencies

- Phase 1 closed.
- Lane C API keys active (Gemini, OpenRouter, Groq, optional NVIDIA NIM, optional Cerebras).
- vLLM build that supports `awq_marlin` (for the 8B AWQ path) OR a llama.cpp build ≥ 2025-10-30 (for the 8B GGUF path).
- Corpus staged under `data/corpus/` or an S3 bucket addressable from the Prefect worker.

## 10. Rollback / abort triggers

- Every Lane B candidate fails any gate → log, move on; Lane C absorbs everything. This is **not** an abort.
- Corpus ingest throws systemic per-video failures (>5% on fixture-like videos) → freeze Phase 2, debug the shared path before enabling captions.
- Lane C providers impose new restrictions that collapse the design → switch to longer batches and lower RPM; the scheduler already supports this without code changes.
- GPU pressure during caption backfill causes retrieval timeouts for users → introduce a hard `serve-mode` time window (e.g., evenings) and throttle backfill outside it.

## 11. Deliverables

- `vidsearch/flows/caption_backfill.py` (Prefect flow).
- `vidsearch/ingest/caption/{hosted,local}.py`.
- `vidsearch/validation/lane_b/` with G1–G5 runners and frozen validation set.
- `docs/lane_b_validation.md` — results table for every candidate tested.
- `docs/caption_backfill_runbook.md` — operator instructions.
- Langfuse Compose overlay wired in `docker-compose.observability.yml`.
- `docs/reindex_runbook.md` — the alias-cutover procedure with a worked example.
- Updated `docs/decision_log.md` entries: Lane B promotion decision, near-duplicate clustering choice.

## 12. Risk register

| Risk | Severity | Mitigation |
|---|---|---|
| 30B UD-IQ2_XXS loads but fails G3 quality | Medium | Expected outcome given the quant aggressiveness; 8B falls back cleanly |
| Lane B resident during ingest causes GPU OOM | High | `make ingest-mode` prerequisite for every ingest run; scheduler cooperates |
| Free-tier RPDs silently changed by a provider | High | Per-provider counters are the only source of truth; alarms on cost telemetry |
| Langfuse ClickHouse disk fills | Medium | Retention policy configured at 30 days for Phase 2 |
| Reindex cutover leaves orphan rows | Low | Alias-cutover keeps the old collection for 48h; deletion is manual after verification |
| Caption backfill throttles OWUI responsiveness | Medium | Backfill runs with `priority < 0`; FastAPI `/search` always beats the queue |
| Perceptual-hash clustering conflates distinct memes | Medium | pHash informational only; does not collapse Qdrant points |

## 13. Exit sentence

Phase 2 is **done** when the real corpus is indexed, captions are flowing through a rate-aware scheduler with zero RPD violations, a Lane B verdict (promoted or rejected) is written into `docs/lane_b_validation.md`, and the reindex drill has been performed on the live `video_segments` alias.

# PHASE_1_PLAN.md — Short-clip / single-video vertical slice

**Version:** 2026-04-18
**Status:** Blocked on Phase 0 exit (all gates P0-G1…G6 closed)
**Owner:** primary builder (OpenCode)
**Reviewers:** Codex, Claude
**Upstream:** `PHASE_0_PLAN.md`, `../ARCHITECTURE.md`, `FINAL_PLAN.md`
**Downstream:** unlocks `PHASE_2_PLAN.md`

---

## 1. Purpose

Phase 1 proves the full video vertical slice on **one** video at a time. It takes the retrieval spine validated in Phase 0 and extends it to the three things that distinguish video from images: **audio (ASR)**, **time-based segmentation**, and **per-segment citations that jump the player to a timestamp**. Nothing about bulk ingest, Lane B local VLMs, or caption backfill is built here — those are Phase 2.

Phase 1 delivers the "one video end to end, re-run is idempotent, search returns timestamped clips, OWUI shows grounded answers" milestone from the final plan and from `../ARCHITECTURE.md` §24 (Week 2 + Week 3).

## 2. Scope

### In scope
- Video fetch for `file://`, `s3://`, and `https://` (with `yt-dlp` for YouTube/Vimeo only; plain HTTP GET otherwise).
- `ffprobe` with MKV remux fallback.
- **Dual segmentation:** TransNetV2 shots + overlapping 8–12 second windows (2–4 second overlap).
- Keyframe extraction per shot and per window.
- ASR via Parakeet TDT 0.6B v3; WhisperX large-v3 fallback on low confidence or unsupported language.
- PaddleOCR PP-OCRv5 on canonical keyframes (same module as Phase 0).
- BGE-M3 dense + sparse + multivector on concatenated `asr_text` + `ocr_text` (high-confidence only).
- SigLIP-2 visual embeddings on shot keyframes only (windows link to the nearest shot's visual vector).
- Qdrant upsert to `video_segments_v1` (parallel to `memes_v1`).
- Reuse of Phase 0 retrieval, reranker, and FastAPI layers.
- `/search` returns timestamped segments with `video_id`, `start_ms`, `end_ms`, `keyframe_uri`.
- OWUI renders clickable timeline chips that jump the player to the cited timestamp.
- 20-query video eval set (smaller than Phase 0's 50; grows in Phase 3).
- Prefect 3 for orchestration of the 12-step ingest flow.

### Explicitly out of scope
- Bulk ingest of the full corpus (deferred to Phase 2).
- Captioning queue / caption backfill scheduler (Phase 2).
- Lane B local VLM validation (Phase 2).
- Graph retrieval (Phase 4).
- CCTV-specific restoration (Phase 4).
- Multi-GPU split (Phase 5).

## 3. Architecture delta from Phase 0

### Added
- `vidsearch/ingest/video/fetch.py`, `probe.py`, `segmentation/{transnetv2,pyscenedetect,windows}.py`, `keyframes.py`, `asr/{parakeet,whisperx}.py`.
- `vidsearch/ingest/indexer.py` — upsert to `video_segments_v1`.
- `vidsearch/flows/ingest_video.py` — Prefect flow chaining the 12 steps.
- `core.videos` and `core.segments` tables (additive migration `002_video_schema.sql`).
- Qdrant collection `video_segments_v1` + alias `video_segments` with the same named-vector design as `memes_v1` plus `text-colbert` multivector for shot-only rescoring.
- Content-addressed `segment_id` via BLAKE3(video_sha256, start_ms, end_ms, seg_ver).
- Prefect server + worker containers added to `docker-compose.yml`.

### Reused unchanged from Phase 0
- OCR module (same PP-OCRv5 call; now runs on keyframes instead of whole images).
- BGE-M3 and SigLIP-2 encoder wrappers.
- Reranker wrapper.
- FastAPI contract shape (additive: `SegmentRecord` joins `SegmentHit` alongside `ImageHit`).
- OWUI tool registration (same `/search` endpoint now returns a mix of image and video hits).
- Evaluation harness (same metrics, new query set).

### Unified retrieval decision
For Phase 1 the default search remains split by media: `/search?media=image` hits `memes_v1`, `/search?media=video` hits `video_segments_v1`. A unified `all_media` alias is deferred to Phase 3 once eval shows the fusion weighting works.

## 4. Ingest pipeline — the 12 steps

1. **Fetch** — URI-scheme dispatch; land under `inbox/<sha256>.<ext>` in MinIO.
2. **Probe** — `ffprobe`; if it fails, `ffmpeg -c copy -f matroska` remux and re-probe. Persistent failures → `ops.jobs.error`.
3. **Shots** — TransNetV2 on the full video; optional PySceneDetect refinement to clean over-segmentation.
4. **Windows** — 8–12 s overlapping windows from the video duration.
5. **Keyframes** — middle I-frame per shot, middle frame per window; written to MinIO as JPEG Q85.
6. **ASR** — Parakeet TDT 0.6B v3 primary; WhisperX large-v3 fallback on confidence < 0.4 (30 s rolling) or unsupported language or explicit diarization request.
7. **OCR** — PP-OCRv5 on every canonical keyframe; `ocr_full_text` retained in Postgres; sub-0.6 confidence tokens excluded from the embedder input.
8. **Text embeddings** — BGE-M3 dense + sparse + multivector on concatenated `asr_text` + high-confidence `ocr_text`.
9. **Visual embeddings** — SigLIP-2 on shot keyframes only. Window segments store a reference to the nearest shot's visual vector rather than recomputing.
10. **Optional captions** — skipped entirely in Phase 1 (flag-off; Phase 2 introduces the backfill queue).
11. **Index** — upsert to Qdrant with named vectors, multivector for shots, and payload (`video_id`, `segmentation_version`, `kind`, `start_ms`, `end_ms`, `language`, `has_ocr`, `has_speech`, `published_ts`, `model_version`).
12. **Publish** — commit `core.segments` rows, mark `ops.jobs.state='done'`.

Every step writes state to `ops.ingest_steps(video_sha, step, state)` and is a no-op on re-run when the row already reads `done`.

## 5. Query pipeline additions

- The Phase 0 query pipeline is extended with a fourth prefetch option: `text-colbert` multivector rescoring as a Phase 1 flag-on-but-optional rescorer for shot candidates.
- `group_by=video_id, group_size=3, limit=50` applied server-side so a single video cannot dominate the top-k.
- Answer synthesis step (`synthesize.py`) is **new** in Phase 1: it composes a grounded answer from the top-K segments and streams via SSE. OWUI renders the citations as clickable timeline chips.

## 6. Data model additions

### `core.videos`
- `video_id TEXT PK`, `sha256 BYTEA UNIQUE`, `source_uri TEXT`, `title`, `duration_ms`, `width`, `height`, `fps`, `container`, `vcodec`, `acodec`, `audio_languages TEXT[]`, `published_ts`, `ingested_at`, `metadata JSONB`.

### `core.segments`
- `segment_id UUID PK` (content-addressed), `video_id FK`, `segmentation_version`, `kind ∈ {shot, window}`, `start_ms`, `end_ms`, `shot_idx`, `keyframe_uri`, `asr_text`, `asr_lang`, `asr_confidence`, `ocr_text`, `ocr_boxes`, `caption_text NULL`, `caption_model NULL`, `has_speech`, `has_ocr`.
- Unique (`video_id`, `segmentation_version`, `start_ms`, `end_ms`).
- Trigram GIN indexes on `asr_text`, `ocr_text`, `caption_text`.

### Qdrant collection `video_segments_v1`
- Named vectors: `text-dense` (1024, int8), `text-colbert` (1024 per token, MAX_SIM, no HNSW), `visual` (1152, binary quant, always_ram).
- Sparse: `text-sparse` (BGE-M3 IDF modifier).
- Payload indexes: `video_id`, `language`, `modality`, `segmentation_version`, `has_ocr`, `has_speech`, `published_ts`, `duration_ms`.

## 7. Test strategy

### 7.1 Unit
- `segment_id()` determinism across runs and disjointness across `SEG_VER` bumps (property tests).
- Fetch dispatch correctness for each URI scheme.
- Probe + remux fallback simulated with a deliberately-corrupt fixture.
- ASR router correctness (confidence thresholds, language triggers, diarization flag).
- Multivector upsert payload shape (no empty vectors, correct token count).

### 7.2 Integration (docker compose up)
- Ingest a 5-minute fixture video (`tests/fixtures/sample_5min.mp4`) end-to-end.
- Second ingest on the same bytes is a full cache hit; zero new Postgres segment rows, zero new Qdrant points.
- Re-encoding the same video to a different bitrate produces a new `video_id` with a disjoint segment ID space.
- `/search?media=video` returns timestamped segments; OWUI renders the clickable timeline chips.

### 7.3 End-to-end
- **P1-G1:** Week-1 style compose bring-up with Prefect server + worker healthy; first Gemini round-trip; OWUI connects to LiteLLM.
- **P1-G2:** Ingest of one fixture video end-to-end; idempotent re-run proven; Postgres row counts match the expected shot/window counts for the fixture.
- **P1-G3:** `/search` + reranker returns timestamped segments; OWUI grounded answer streams with citations.
- **P1-G4:** 20-query video eval set recorded with baseline `nDCG@10`.

### 7.4 Regression eval
- 20 queries across the five intent classes (lookup, semantic, visual, temporal, compositional).
- Metrics: `nDCG@10`, `Recall@100`, `MRR`, `answer-faithfulness` on synthesis.
- Judges: Gemini 2.5 Pro via LiteLLM + hand spot-check on the hardest 4 queries.
- CI gate (same rule as Phase 0): >3pp regression blocks merge without a waiver.

## 8. Verification criteria

| Criterion | Target |
|---|---|
| Videos ingested end-to-end | ≥ 3 fixture videos (short, medium, long) |
| Idempotent re-run | 0 duplicate rows, 0 duplicate Qdrant points |
| Segment counts | Match the known shot/window counts for each fixture video |
| `/search?media=video` returns timestamped hits | Yes on all 20 queries |
| OWUI grounded answer has clickable citations | Yes; player jumps to the cited timestamp |
| `nDCG@10` on the 20-query set | Captured as baseline (no absolute target yet — that lives in Phase 3) |
| `make ingest-mode` / `make serve-mode` toggle works | Yes; `vllm` container stopped during ingest |
| Content addressability | Re-encode produces new SHA-256 and disjoint segment IDs |

## 9. Closing gates

- **P1-G1 — Prefect + fetch online.** Prefect server and worker in compose; fetch, probe, and remux paths implemented and tested against the fixture set.
- **P1-G2 — Full ingest.** Steps 3–12 land on the single fixture video; idempotent re-run proven; `core.videos` and `core.segments` populated to expected counts.
- **P1-G3 — Query returns timestamps.** `/search?media=video` returns a ranked list with `segment_id`, `video_id`, `start_ms`, `end_ms`, `keyframe_uri`; OWUI renders clickable timeline chips.
- **P1-G4 — Synthesis.** `synthesize.py` streams grounded answers with citations; `answer-faithfulness` judge score ≥ 0.6 on a 10-query hand-picked set.
- **P1-G5 — Single-GPU discipline.** `make ingest-mode` and `make serve-mode` switch cleanly; no Lane B residency during ingest.
- **P1-G6 — 20-query eval recorded.** `eval.runs` + `eval.metrics` populated; baseline committed to `docs/decision_log.md`.

## 10. Dependencies

- All of Phase 0 closed.
- `ffmpeg` on `PATH` (already present).
- NVIDIA driver ≥ 576 (present).
- Parakeet TDT 0.6B v3 weights downloaded (directory exists under `K:\models\video_searcher\parakeet-tdt-0.6b-v3`; completeness check required).
- WhisperX large-v3 weights downloaded (directory exists; completeness check required).

## 11. Rollback / abort triggers

- TransNetV2 produces unusable shots on the fixture set → swap to PySceneDetect-first before re-architecting.
- Parakeet fails on the fixture languages consistently → route everything to WhisperX and log the regression; do not block Phase 1 on ASR perfection.
- Prefect 3 orchestration introduces more complexity than it prevents → fall back to a plain Python driver script for Phase 1 and revisit Prefect in Phase 2.

## 12. Deliverables

- `docker-compose.yml` updated with `prefect-server`, `prefect-worker`.
- `infra/postgres/002_video_schema.sql` — additive migration.
- `infra/qdrant/bootstrap.py` extended to also create `video_segments_v1`.
- `vidsearch/ingest/video/` package (fetch, probe, segmentation, keyframes, asr).
- `vidsearch/flows/ingest_video.py` — Prefect flow.
- `vidsearch/query/synthesize.py` — answer composition + SSE streaming.
- `vidsearch/eval/queries_videos.yaml` — 20 queries, graded labels for the fixture set.
- `Makefile` with `ingest-mode` / `serve-mode` toggles.
- Updated `docs/runbook.md` — video ingest quickstart, Prefect UI walkthrough.
- `docs/decision_log.md` — ADRs for TransNetV2 choice, Parakeet primary / WhisperX fallback, dual segmentation mandatory.

## 13. Risk register

| Risk | Severity | Mitigation |
|---|---|---|
| Parakeet weights missing / incomplete on disk | High | Pre-flight download check; fall back to WhisperX-only for Phase 1 if blocked |
| TransNetV2 over-segments fast-cut videos | Medium | PySceneDetect refinement step already in the design |
| SigLIP-2 keyframe-only rule causes window recall loss | Medium | Windows link to nearest shot's visual vector; text path independent |
| Prefect flow brittleness on crashes | Medium | `ops.ingest_steps` checkpointing makes every step resumable |
| `make ingest-mode` / `serve-mode` races | Low | Makefile uses explicit `docker compose stop` / `start` with health waits |
| Phase 0 `memes_v1` collection unchanged but Phase 1 breaks retrieval payload contract | Low | FastAPI contracts are additive; Phase 0 image search unaffected |
| Single-GPU OOM under Parakeet + BGE-M3 + SigLIP-2 concurrently | Medium | Prefect concurrency tags: `gpu-asr=1`, `gpu-embed=2`; serialise on the 4060 Ti |

## 14. Exit sentence

Phase 1 is **done** when a user can drop one video into `data/inbox/`, wait, then ask a natural-language question in OWUI and get a grounded answer with timestamp citations that jump the player to the right moment — and that same ingest, run twice, produces zero duplicate rows or points.

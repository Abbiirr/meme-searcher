# PHASE_1_TODO.md — Short-clip / single-video vertical slice

**Version:** 2026-04-18
**Blocks on:** Phase 0 gates P0-G1…G6 closed.
**See:** `PHASE_1_PLAN.md` for rationale and gates.

---

## P1.0 — Pre-flight (unlocks P1-G1)

- [ ] Verify Phase 0 exit: all P0 gates closed, `docs/phase1_short_clips_transition.md` reviewed.
- [ ] Download / verify Parakeet TDT 0.6B v3 weights (NeMo format or ONNX export).
- [ ] Download / verify WhisperX large-v3 weights (`faster-whisper-large-v3` directory).
- [ ] Model smoke test: Parakeet transcribes a 30-second fixture clip.

## P1.1 — Infra additions (unlocks P1-G1)

- [ ] Add `prefect-server` (port 4200) and `prefect-worker` services to `docker-compose.yml`.
- [ ] Prefect worker mounts the GPU with NVIDIA runtime overlay (`docker-compose.gpu.yml`).
- [ ] Add `Makefile` with `ingest-mode`, `serve-mode`, `eval`, `bootstrap` targets.
- [ ] **TEST:** `make ingest-mode` → `vllm` container stopped, prefect-worker running; `make serve-mode` reverses.
- [ ] **TEST:** Prefect UI accessible at `http://localhost:4200`.

## P1.2 — Schema migration (unlocks P1-G2)

- [ ] Write `infra/postgres/002_video_schema.sql` — additive: `core.videos`, `core.segments`, trigram indexes on `asr_text` / `ocr_text` / `caption_text`.
- [ ] Extend `infra/qdrant/bootstrap.py` to create `video_segments_v1` with `text-dense`, `text-sparse`, `text-colbert`, `visual` vectors plus payload indexes. Alias `video_segments`.
- [ ] **TEST:** migration applies cleanly on a Postgres volume that has Phase 0 data; no Phase 0 rows affected.
- [ ] **TEST:** second run of the Qdrant bootstrap is a no-op.

## P1.3 — Content IDs (unlocks P1-G2)

- [ ] `vidsearch/ids.py::segment_id(video_sha256, start_ms, end_ms, seg_ver) -> uuid.UUID` via BLAKE3.
- [ ] Constant `SEG_VER = "shots-v1"` and `"window-v1"`.
- [ ] **TEST:** property — identical inputs produce identical UUIDs across runs.
- [ ] **TEST:** property — bumping `SEG_VER` yields a disjoint UUID space.
- [ ] **TEST:** unit — re-encoding a fixture to a different bitrate produces a new video SHA-256 and a new segment ID space.

## P1.4 — Fetch, probe (unlocks P1-G2)

- [ ] `vidsearch/ingest/video/fetch.py` with URI-scheme dispatch: `file://` passthrough, `s3://` via boto3, `https://` via `yt-dlp` (YouTube/Vimeo) or plain HTTP GET.
- [ ] Deterministic MinIO key: `inbox/<sha256>.<ext>`.
- [ ] `vidsearch/ingest/video/probe.py` with `ffmpeg -c copy -f matroska remux.mkv` fallback on probe failure.
- [ ] **TEST:** unit — each URI scheme dispatches to the right handler.
- [ ] **TEST:** integration — ingest of a deliberately-corrupt container triggers the remux path and succeeds on re-probe.
- [ ] **TEST:** integration — fetch cache hit on repeat (SHA-256 match).

## P1.5 — Segmentation (unlocks P1-G2)

- [ ] `vidsearch/ingest/video/segmentation/transnetv2.py` — shot boundary detection.
- [ ] `vidsearch/ingest/video/segmentation/pyscenedetect.py` — optional merge-over-segmentation refinement.
- [ ] `vidsearch/ingest/video/segmentation/windows.py` — overlapping 8–12 s windows.
- [ ] `vidsearch/ingest/video/keyframes.py` — middle I-frame per shot, middle frame per window, JPEG Q85 to MinIO.
- [ ] **TEST:** unit — windows cover the full duration with the expected overlap arithmetic.
- [ ] **TEST:** integration — a fixture video yields expected shot counts (±10%).

## P1.6 — ASR (unlocks P1-G2)

- [ ] `vidsearch/ingest/video/asr/parakeet.py` — Parakeet TDT 0.6B v3 with word-level and segment-level timestamps.
- [ ] `vidsearch/ingest/video/asr/whisperx.py` — WhisperX large-v3 fallback with diarization.
- [ ] Routing rules in `vidsearch/ingest/video/asr/__init__.py`: language unsupported by Parakeet OR confidence < 0.4 over a 30-second rolling window OR `diarize=True` in job metadata → WhisperX.
- [ ] **TEST:** unit — routing logic lands on the right backend for each trigger.
- [ ] **TEST:** integration — Parakeet transcribes the fixture; WhisperX path exercised with a forced-language fixture.

## P1.7 — OCR, text embed, visual embed (unlocks P1-G2)

- [ ] Reuse Phase 0 PP-OCRv5 wrapper on canonical keyframes.
- [ ] Concatenate `asr_text` + `ocr_text` (high-confidence only) before BGE-M3; optional prefix tokens for modality weighting.
- [ ] SigLIP-2 only on shot keyframes; window segments store `qdrant_visual_ref = shot_segment_id`.
- [ ] **TEST:** integration — a shot keyframe has all four vectors; a window segment has three vectors plus the visual reference.

## P1.8 — Indexer + publish (unlocks P1-G2)

- [ ] `vidsearch/ingest/indexer.py` — Qdrant upsert with payload fields per §6.
- [ ] Commit `core.segments` rows after successful Qdrant upsert.
- [ ] Mark `ops.jobs.state='done'`.
- [ ] **TEST:** integration — row counts match segment counts; Qdrant point count matches.

## P1.9 — Prefect flow (unlocks P1-G2)

- [ ] `vidsearch/flows/ingest_video.py` — Prefect flow chaining 12 steps with `@task` boundaries.
- [ ] Concurrency tags: `gpu-asr=1`, `gpu-embed=2`, `cpu` unlimited, `io=16`.
- [ ] Every task checkpoints `ops.ingest_steps`.
- [ ] **TEST:** integration — Prefect deployment `ingest-video` runs end-to-end on the fixture.
- [ ] **TEST:** idempotency — two sequential flow runs on the same video: second one is a full cache hit.

## P1.10 — Query + rerank for video (unlocks P1-G3)

- [ ] Introduce `vidsearch/query/retrieve_video.py` (mirror of Phase 0's `retrieve_images.py` shape) targeting the `video_segments` alias. Add a thin `vidsearch/query/retrieve.py` dispatcher that routes `media=image` → `retrieve_images` and `media=video` → `retrieve_video`. Phase 0's `retrieve_images.py` stays untouched; this is additive.
- [ ] `group_by=video_id, group_size=3, limit=50`.
- [ ] Optional multivector rescore via `text-colbert`.
- [ ] Reuse Phase 0 reranker.
- [ ] **TEST:** integration — `/search?media=video` returns segments with `start_ms`, `end_ms`, `keyframe_uri`.
- [ ] **TEST:** a query on an intentionally chatty single video does not let one video dominate the top-k when multiple videos are present.

## P1.11 — Synthesis + citations (unlocks P1-G4)

- [ ] `vidsearch/query/synthesize.py` — compose a grounded answer with segment citations; route through LiteLLM `synthesis-long` group (Groq Llama-3.3-70B primary).
- [ ] SSE streaming on `/search` for the synthesis tokens.
- [ ] Citation markdown format stable enough that OWUI renders clickable timeline chips (document the format in `docs/owui_integration.md`).
- [ ] **TEST:** manual — a query in OWUI returns a grounded answer; clicking a citation chip jumps the player to the timestamp.
- [ ] **TEST:** judge — `answer-faithfulness` ≥ 0.6 on 10 hand-picked queries.

## P1.12 — Evaluation (unlocks P1-G6)

- [ ] `vidsearch/eval/queries_videos.yaml` — 20 queries spanning the five intent classes against the fixture video(s).
- [ ] Hand-label graded relevance for the 20 queries (3 fixture videos is enough; top 10 segments per query).
- [ ] LLM-judge pass on 10 queries; store as `eval.qrels.judge='llm'`.
- [ ] Run `eval/runner.py`; land results in `eval.runs`, `eval.run_results`, `eval.metrics`.
- [ ] **GATE:** baseline `nDCG@10` and `answer-faithfulness` recorded in `docs/decision_log.md`.

## P1.13 — Idempotency + content addressability proofs (unlocks P1-G2)

- [ ] Run `ingest_video` twice on the same fixture; compare `ops.ingest_steps` rows before and after — identical.
- [ ] Re-encode the fixture at a different bitrate; ingest; confirm new `video_id` and disjoint segment IDs.
- [ ] Document both proofs in `docs/runbook.md`.

## P1.14 — Documentation (cross-gate)

- [ ] Update `docs/runbook.md` with Prefect UI walkthrough, `make` targets, idempotency procedure.
- [ ] ADRs added to `docs/decision_log.md`: TransNetV2 choice, Parakeet primary / WhisperX fallback, dual segmentation mandatory, `SEG_VER` discipline.

---

## Cross-cutting rules

- [ ] No Lane B code (local 30B VLM) merged during Phase 1.
- [ ] No caption backfill scheduler merged during Phase 1.
- [ ] Phase 0 eval set continues to pass on every merge (no image-search regression from Phase 1 changes).
- [ ] Every PR that touches `vidsearch/query/*` runs both Phase 0 and Phase 1 eval sets.

## Exit checklist (mirrors `PHASE_1_PLAN.md` §9)

- [ ] Fixture video ingests end-to-end.
- [ ] Idempotent re-run proven.
- [ ] `/search?media=video` returns timestamped segments.
- [ ] OWUI renders clickable citations.
- [ ] `make ingest-mode` / `make serve-mode` toggle verified.
- [ ] 20-query video eval recorded with a `config_hash`.
- [ ] Updated runbook + ADRs merged.

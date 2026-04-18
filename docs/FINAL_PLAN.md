# FINAL_PLAN.md — Delivery plan for the local-first multimodal video search engine

**Version:** 2026-04-18 (updated — Phase 0 meme-searcher folded in)
**Status:** Planning-ready; Phase 0 is the first build step
**Primary references:** `ARCHITECTURE.md`, `PHASE0_MEME_SEARCHER.md`, `PHASE_0_PLAN.md` … `PHASE_5_PLAN.md`

> **Important change vs earlier drafts:** the previous "Phase 0" was an infra-only bootstrap. It has been replaced by a **meme-first Phase 0** that builds a complete image-only search product. The previous phases renumber and shift by one meaning only — the substance of each later phase is unchanged. See `PHASE_0_PLAN.md` for the new Phase 0 in full, and per-phase `PHASE_X_PLAN.md` / `PHASE_X_TODO.md` for the testing strategy, verification criteria, and closing gates of each phase.

## 1. What is being built

Build a single-node proof of concept that ingests a large image + video corpus, indexes speech, OCR, visual cues, and optional captions, and answers natural-language or image-grounded queries with timestamped clip results and grounded explanations.

Execution starts with an **image-only meme search engine (Phase 0)** that validates the entire retrieval spine — OCR, dense + sparse text retrieval, visual retrieval, RRF fusion, local reranking, Open WebUI ↔ LiteLLM ↔ backend integration, evaluation, and idempotent ingest — before any video-specific code is written.

The system is intentionally split into three execution lanes:

- **Lane A — local stable core:** fetch, probe, segmentation, keyframes, ASR, OCR, embeddings, hybrid retrieval, local rerank, metadata, eval, feedback.
- **Lane B — experimental local VLM:** optional local captioning / vision verification after passing validation gates.
- **Lane C — hosted multimodal lane:** default heavy captioning / verification / judge / long-context synthesis through LiteLLM.

The system is also split into four layers:

1. **Offline ingest**
2. **Hybrid retrieval**
3. **Rerank and verify**
4. **Synthesis**

The governing principle is: **caption-when-cheap, retrieve-on-cheap, verify-with-VLM**.

## 2. Locked architectural decisions

### Frontend
- Use **Open WebUI** as the primary frontend.
- OWUI must connect only to the internal LiteLLM backend.
- OWUI must be pinned to a **patched stable release** (at minimum `v0.6.35+` because of CVE-2025-64496).
- Direct Connections remain disabled.

### Core backend
- **PostgreSQL 17** is the source of truth.
- **Qdrant** is the vector and hybrid retrieval engine.
- **MinIO** is the object store.
- **Prefect 3** is the orchestrator.
- **LiteLLM** is the provider gateway.

### Segmentation and retrieval
- Dual segmentation is mandatory: **shots + overlapping windows**.
- Shot segmentation uses **TransNetV2** with optional **PySceneDetect** refinement.
- Retrieval uses **Qdrant Query API** with **dense + sparse + visual prefetch**, **server-side RRF**, and **group_by=video_id**.

### Core models
- **ASR primary:** Parakeet TDT 0.6B v3
- **ASR fallback:** WhisperX large-v3
- **OCR:** PaddleOCR PP-OCRv5
- **Text embeddings:** BGE-M3
- **Visual embeddings:** SigLIP-2 So400m/16-384
- **Local vision family:** Qwen3-VL
- **Controller / synthesis model:** Qwen3.6-35B-A3B

### Local VLM policy
- Qwen3.6 is **not** the primary local vision model.
- Qwen3-VL is the local vision family because it has the more mature GGUF/mmproj/runtime path.
- Lane B remains optional until a model passes validation.

## 3. Final model plan

### Lane A — always on
- Parakeet TDT 0.6B v3
- WhisperX fallback
- PaddleOCR PP-OCRv5
- BGE-M3
- SigLIP-2 So400m/16-384
- jina-reranker-v2-base-multilingual

### Lane B — local VLM candidates
1. **Primary stretch candidate:** `unsloth/Qwen3-VL-30B-A3B-Instruct-GGUF` at `UD-IQ2_XXS` + `mmproj-BF16.gguf`
2. **Safe fallback:** Qwen3-VL-8B-Instruct (AWQ or GGUF)
3. **Efficiency experiment:** MiniCPM-V 4.5 int4

### Lane C — hosted chain
- **Video caption primary:** Gemini 2.5 Flash-Lite
- **Caption fallback:** OpenRouter Nemotron Nano 12B v2 VL :free
- **Caption fallback 2:** Groq Scout on pre-sampled frames
- **Verification:** Gemini 2.5 Flash / hosted VLM chain
- **Synthesis:** Groq Llama-3.3-70B, with local Qwen3.6 fallback if needed

**Important:** provider quotas and throughput numbers are operational estimates, not guarantees. They live in config, Redis counters, and dashboards — not in business logic.

## 4. Ingest flow

1. Fetch source (`file://`, `s3://`, `https://` / `yt-dlp`)
2. Probe with `ffprobe`
3. Remux to MKV if probe fails
4. Run TransNetV2
5. Optionally refine with PySceneDetect
6. Create overlapping windows
7. Extract keyframes
8. Run ASR
9. Run OCR
10. Compute BGE-M3 text embeddings
11. Compute SigLIP-2 visual embeddings on shot keyframes only
12. Optionally caption through Lane C or Lane B
13. Upsert Postgres + Qdrant
14. Publish searchable segments

## 5. Query flow

1. Parse query intent
2. Encode with BGE-M3 and optional SigLIP-2 image path
3. Retrieve from Qdrant with sparse + dense + visual prefetch
4. Fuse with RRF
5. Group by video
6. Local rerank
7. Optional ColBERT-style rescore
8. Optional VLM verify of top candidates
9. Synthesize grounded answer
10. Stream through FastAPI SSE into OWUI

## 6. What changed from earlier drafts

- **OWUI** replaced the custom SvelteKit frontend as the primary UI.
- **Qwen3.6** is now explicitly a controller/synthesis model, not the default VLM.
- **Qwen3-VL** is the local VLM family.
- **YTan2000/Qwen3.6-35B-A3B-TQ3_4S** remains rejected.
- Hosted free-tier throughput is treated as **estimated planning input**, not a hard architectural promise.
- The Unsloth 30B projector filename is corrected to **`mmproj-BF16.gguf`**.

## 7. Risks that still matter

- Single-GPU contention between ingest and Lane B serving.
- Provider quotas changing or disappearing.
- Lane B stretch models not passing validation.
- OCR noise harming sparse retrieval if thresholds are set too low.
- Reindex operational mistakes if aliases are not respected.

## 8. Definition of done for each phase

The detailed exit criteria and closing gates live in the per-phase files. This section is the one-paragraph summary.

### Phase 0 — Meme searcher (image-only)
- Compose stack boots; Postgres schema applied; Qdrant collection + alias created; LiteLLM config validated; OWUI connected.
- 10,000+ memes ingested; idempotent re-ingest proven.
- 50-query meme eval set executed; baseline `nDCG@10` recorded with a `config_hash`; reranker uplift ≥ +2 pp.
- OWUI tool returns grounded meme results with thumbnails.
- Backup/restore drill logged; delete flow proven.
- `docs/phase1_short_clips_transition.md` signed off.
- **Full gates:** see `PHASE_0_PLAN.md` §9 (P0-G1…G6).

### Phase 1 — Short-clip / single-video vertical slice
- One full video ingested end to end through the 12-step Prefect flow; second run is idempotent.
- Query path returns timestamped clips; OWUI renders clickable timeline chips.
- Baseline 20-query video eval recorded.
- Single-GPU discipline (`make ingest-mode` / `make serve-mode`) validated.
- **Full gates:** see `PHASE_1_PLAN.md` §9 (P1-G1…G6).

### Phase 2 — Corpus scale, Lane B validation, caption backfill
- Bulk corpus ingest (≥ 500 videos) running; caption backfill queue operational with RPD adherence proven.
- At least one Lane B candidate has a written G1–G5 verdict; promotion is optional, the verdict is mandatory.
- Langfuse observability online.
- Reindex drill performed on the live `video_segments` alias.
- **Full gates:** see `PHASE_2_PLAN.md` §8 (P2-G1…G6).

### Phase 3 — Evaluation maturity, reranker A/B, CI gating
- 100+ queries per media indexed and graded.
- Reranker A/B complete; winner promoted or incumbent confirmed.
- RRF + group_by tuned; `all_media` alias decision made.
- CI regression gate live and proven to block a deliberately-broken commit.
- `nDCG@10` absolute target committed and met.
- **Full gates:** see `PHASE_3_PLAN.md` §10 (P3-G1…G6).

### Phase 4 — Graph, VSS, CCTV
- Graph booster spike (≤ 2 weeks) — promoted behind flag or shelved with reasoning.
- NVIDIA VSS / LVS benchmark complete — prompt ports promoted or archived.
- CCTV / noise-tolerance eval run — baseline sufficient, or enhancement flag-gated.
- All Phase 0–3 eval suites still pass.
- **Full gates:** see `PHASE_4_PLAN.md` §8 (P4-G1…G5).

### Phase 5 — Hardening and multi-GPU
- Backups live + restore drill timed and documented.
- Chaos test green for every service.
- 30-day uptime simulation passed unattended.
- Helm chart lints + deploys to a local kind cluster.
- Reranker LoRA weekly loop closed at least once.
- Multi-GPU split implemented or deferred with documented reason.
- **Full gates:** see `PHASE_5_PLAN.md` §10 (P5-G1…G7).

## 9. Immediate implementation order

1. **Build Phase 0 meme searcher end-to-end** (image-only): infra up, schemas frozen, Qdrant `memes_v1` created, content-addressed `image_id`, batch ingest idempotent, hybrid retrieval + local rerank proven, FastAPI + OWUI wired, 50-query meme eval baseline recorded. **No video code is written in this phase.** See `PHASE_0_PLAN.md` / `PHASE_0_TODO.md`.
2. **Phase 1 short-clip vertical slice:** additive schema migration (`core.videos`, `core.segments`), 12-step ingest flow on one video, timestamped `/search` + OWUI timeline chips, 20-query video eval baseline.
3. **Phase 2 corpus scale + Lane B validation + caption backfill:** rate-aware scheduler, Lane B G1–G5 verdict, Langfuse online, reindex drill on live alias.
4. **Phase 3 eval maturity + reranker A/B + CI gating:** ≥100 queries per media graded, reranker winner promoted, `all_media` alias decision, CI regression gate blocks merges.
5. **Phase 4 optional boosters:** graph booster, VSS/LVS benchmark, CCTV lane — each time-boxed to 2 weeks with written verdicts.
6. **Phase 5 hardening:** backups + restore drill, chaos tests, 30-day uptime simulation, Helm chart, reranker LoRA weekly loop, optional multi-GPU split.

## 10. Execution rule

Do **not** pause implementation to keep redesigning unless one of these happens:
- Qdrant hybrid retrieval is materially worse than expected on the eval set
- the ingest flow cannot remain idempotent under real failures
- every Lane B candidate fails validation and Lane C is insufficient for your throughput goals

If none of those happen, continue building.

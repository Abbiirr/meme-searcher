# Decision Log

## ADR-001: Phase 0 corpus and search scope

**Date:** 2026-04-19
**Status:** Accepted (amended 2026-04-20)

Initial corpus is `data/meme` containing ~3100 supported image files.
Supported extensions: `.jpg`, `.jpeg`, `.png`, `.webp`, `.gif`, `.jfif`.
Skipped extensions are logged as skipped, not treated as failures.
Search is text-first: user describes a meme in natural language.
Canonical search contract is `POST /search`.

**Amendment (2026-04-20):** empirical count via `ls data/meme | wc -l` returns **293 files**, not ~3,100. This is a ~10× gap from the original planning number. Root cause is unresolved — either the original estimate counted files across nested directories that have since been flattened, or the target corpus was pruned to a representative subset for Phase 0. Either reading is consistent with the P0-G4 thresholds (they are fractions, not absolute counts), so the gates remain valid.

**Operative decision:** Phase 0 proceeds against the 293 images currently in `data/meme`. The qrels data-entry pass (blocker F) targets those 293. If the user later restores the full ~3,100 corpus, P0-G2 (full ingest) and P0-G4 (baseline eval) must rerun — the qrels set needs re-pooling at top-10 against the expanded corpus, since new candidates may outrank existing grade-3 targets. This does not invalidate the ingest pipeline or the retrieval design.

## ADR-002: Identity key is SHA-256

**Date:** 2026-04-19
**Status:** Accepted

The identity key for an image is the SHA-256 of its raw bytes.
Same bytes at multiple paths map to one `image_id`.
Re-running ingest must not duplicate rows or vector points.

## ADR-003: Model locations

**Date:** 2026-04-19
**Status:** Accepted

Local model assets live under `K:\models\video_searcher`.
Models used in Phase 0:
- OCR: PaddleOCR PP-OCRv5 (det + rec) — routed via LiteLLM gateway alias `meme_ocr` (fallback `meme_ocr_fallback`). Direct-local path retained behind `VIDSEARCH_OCR_BACKEND=local`.
- VLM captioning: Qwen3-VL-8B-Instruct — routed via gateway alias `meme_vlm_captioner`. Emits the 4 labels (`literal`, `figurative`, `template`, `tags`) per `PHASE_0_RETRIEVAL_PLAN.md` §2.3.
- Text embeddings: BAAI/bge-m3 — direct-local at `$MODEL_ROOT/embeddings/bge-m3/`. Produces both dense (1024-dim) and sparse vectors.
- Visual embeddings: google/siglip2-so400m-patch16-384 — direct-local at `$MODEL_ROOT/embeddings/siglip2-so400m-patch16-384/` (~4.5 GB `model.safetensors`). Produces 1152-dim vectors.
- Reranker: jinaai/jina-reranker-v2-base-multilingual — direct-local at `$MODEL_ROOT/rerankers/jina-reranker-v2-base-multilingual/`. Takes `(query, document)` pairs where the document is `caption_literal | caption_figurative | template | tags | text:<first 200 chars of OCR>`.

## ADR-004: Fingerprint recipe for `ops.model_versions`

**Date:** 2026-04-20
**Status:** Accepted

Every model referenced by the search hot-path carries a deterministic fingerprint in `ops.model_versions` so that ingest and query runs can be joined to the exact model build that produced them. Two recipes per `docs/MODEL_GATEWAY.md` §4:

- **Gateway models:** sha256 of the `/v1/models` response body from LiteLLM, truncated to 16 hex chars.
- **Direct-local models:** rolling sha256 over `model.safetensors + \x00 + config.json + \x00 + preprocessor_config.json` (preprocessor optional; a null-byte domain-separator between files prevents concatenation-collision attacks).

Implemented in `vidsearch/ingest/fingerprints.py`; stamped on both ingest start (`ingest_folder`) and API boot (`@app.on_event("startup")`). Swallows gateway and DB errors to keep the boot path non-fatal.

## ADR-005: Builder role rotation

**Date:** 2026-04-20
**Status:** Accepted

Per `CLAUDE.md`, Claude defaults to reviewer/architect. The user temporarily assigned builder to Claude during cycles 1–5 of 2026-04-20 while OpenCode's queue stalled. Cycle 5 handoff (see `AGENTS_CONVERSATION.MD` Entry 27) transfers the builder chair to Codex for all Phase 0 residual work. Claude reverts to reviewer/architect. OpenCode may assist on sub-tasks when explicitly directed.

Commit policy is unchanged: only the user creates commits. All agents stage only.

## ADR-006: Phase 0 OWUI integration uses a pipe model, not LLM tool-calling

**Date:** 2026-04-24
**Status:** Accepted

Phase 0's OWUI integration now uses an auto-provisioned Open WebUI `pipe` model named `Meme Search` rather than relying on a general chat model to decide when to call a tool.

Reason:
- the user-visible failure mode was selecting a normal model such as `vision`, which then hallucinated meme descriptions in plain text instead of querying the local corpus
- a dedicated pipe model is deterministic: every prompt sent to `Meme Search` goes straight to FastAPI `POST /search`
- this still preserves the canonical backend contract and avoids a second UI-specific schema

Implementation:
- `docker-compose.yml` mounts `infra/open_webui/` into the `open-webui` container
- `infra/open_webui/start-with-bootstrap.sh` wraps OWUI startup
- `infra/open_webui/provision.py` upserts the `meme_search` pipe into the OWUI DB
- `infra/open_webui/functions/meme_search_pipe.py` formats search hits into markdown with host-reachable thumbnail URLs

Operational consequence:
- use the `Meme Search` model in OWUI for local meme retrieval
- plain chat models remain plain chat models unless separately extended

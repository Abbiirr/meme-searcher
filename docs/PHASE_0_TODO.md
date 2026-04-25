# PHASE_0_TODO.md - Meme searcher

**Version:** 2026-04-19
**Scope:** Phase 0 only
**Source of truth:** `PHASE_0_PLAN.md`
**Outcome target:** given a text description of a meme, return the image from `data/meme` if it exists

> **Legend:** testing tasks are explicit. Do not mark a gate closed until every task under it is complete.

---

## P0.0 - Phase 0 guardrails (unlocks P0-G1)

- [ ] Expand `.gitignore` for local development artifacts relevant to this project: Python caches, virtualenvs, Node artifacts if added, `.env`, MinIO data, Postgres data, Qdrant data, model caches, logs, temp outputs.
- [ ] Create `docs/decision_log.md`.
- [ ] Record the Phase 0 starting ADRs:
  - initial corpus is `data/meme`
  - supported extensions are `.jpg`, `.jpeg`, `.png`, `.webp`, `.gif`, `.jfif`
  - skipped extensions are logged, not treated as failures
  - search is text-first
  - the canonical search contract is `POST /search`
- [ ] Add a short README section naming `docs/PHASE_0_PLAN.md` and `docs/PHASE_0_TODO.md` as the authoritative Phase 0 docs.

## P0.1 - Infra (unlocks P0-G1)

- [ ] Write `docker-compose.yml` with these services: `postgres`, `qdrant`, `minio`, `litellm`, `api`, `open-webui`.
- [ ] Add healthchecks for every service.
- [ ] Write `.env.example` with every required variable:
  - `DATABASE_URL`
  - `POSTGRES_USER`
  - `POSTGRES_PASSWORD`
  - `POSTGRES_DB`
  - `QDRANT_URL`
  - `MINIO_ROOT_USER`
  - `MINIO_ROOT_PASSWORD`
  - `MINIO_BUCKET_THUMBNAILS`
  - `LITELLM_MASTER_KEY`
  - `OPEN_WEBUI_SECRET_KEY`
  - `GEMINI_API_KEY`
  - `OPENROUTER_API_KEY`
- [ ] Pin Open WebUI to a known-good image tag and disable Direct Connections.
- [ ] Write `infra/litellm/config.yaml` with the model groups actually needed for Phase 0:
  - `search_llm` if answer synthesis is used
  - `verify` only if verification remains enabled
  - `judge` for eval judging if used
- [ ] Keep optional features optional. Phase 0 must still work when hosted-provider keys are absent.
- [ ] Update `README.md` with a boot sequence that gets the stack healthy from a fresh clone.
- [ ] **TEST:** `docker compose up -d` reaches healthy status.
- [ ] **TEST:** LiteLLM config validation passes.
- [ ] **TEST:** Open WebUI connects to LiteLLM.

## P0.2 - Database schema (unlocks P0-G1)

- [ ] Write `infra/postgres/001_schema.sql` with schemas `core`, `ops`, `eval`, `feedback`.
- [ ] Create tables:
  - `core.images`
  - `core.image_items`
  - `ops.jobs`
  - `ops.ingest_steps`
  - `ops.model_versions`
  - `eval.queries`
  - `eval.qrels`
  - `eval.runs`
  - `eval.run_results`
  - `eval.metrics`
  - `feedback.events`
- [ ] Add indexes:
  - unique index on `core.images.sha256`
  - lookup index on `core.image_items.image_id`
  - trigram GIN on `core.image_items.ocr_full_text`
  - useful lookup indexes for `ops.ingest_steps`
- [ ] Seed `ops.model_versions` with the concrete model identifiers used in Phase 0.
- [ ] Define `ops.ingest_steps.step_name` from the canonical step list:
  - `hash`
  - `decode`
  - `thumbnail`
  - `ocr`
  - `embed_text`
  - `embed_visual`
  - `upsert_pg`
  - `upsert_qdrant`
- [ ] **TEST:** schema applies cleanly on a fresh Postgres volume.
- [ ] **TEST:** schema round-trips through `pg_dump` and restore.

## P0.3 - Qdrant bootstrap (unlocks P0-G1)

- [ ] Write `infra/qdrant/bootstrap.py`.
- [ ] Create physical collection `memes_v1`.
- [ ] Create alias `memes -> memes_v1`.
- [ ] Configure named vectors:
  - `text-dense` = 1024 dims, cosine
  - `text-sparse` = BGE-M3 sparse
  - `visual` = 1152 dims, cosine
- [ ] Create required payload indexes:
  - `image_id`
  - `source_uri`
  - `format`
  - `width`
  - `height`
  - `has_ocr`
  - `has_caption`
  - `ingested_at`
  - `model_version`
- [ ] Make bootstrap idempotent.
- [ ] **TEST:** a second bootstrap run is a no-op.
- [ ] **TEST:** snapshot and restore succeed on a scratch Qdrant instance.

## P0.4 - Corpus scanner and file policy (unlocks P0-G2)

- [ ] Implement a reusable filesystem scanner for `data/meme`.
- [ ] Scan recursively and classify every file into one of:
  - `supported`
  - `skipped_unsupported_extension`
  - `skipped_no_extension`
  - `failed_stat`
- [ ] Normalize `.jfif` as supported JPEG input.
- [ ] Record scan counts in `ops.jobs` or an equivalent run summary.
- [ ] Make the scanner the only entry point used by folder ingest, eval corpus prep, and delete-path validation.
- [ ] **TEST:** the scanner reports both supported and skipped files on the current local corpus.
- [ ] **TEST:** unsupported files do not enter the ingest queue.

## P0.5 - Single-image ingest (unlocks P0-G2)

- [ ] Implement `vidsearch/ids.py::image_id(bytes) -> str`.
- [ ] Implement `vidsearch/ingest/images.py::ingest_image(path)`.
- [ ] Decode supported images reliably, with `.gif` using the first frame.
- [ ] Extract width, height, format, and source path.
- [ ] Generate thumbnails as JPEG with max 512 px on the longest side.
- [ ] Upload thumbnails to MinIO under deterministic keys derived from `image_id`.
- [ ] Integrate PaddleOCR PP-OCRv5 using local model assets.
- [ ] Implement OCR normalization per the Phase 0 contract.
- [ ] Integrate BGE-M3 dense and sparse embeddings.
- [ ] Integrate SigLIP-2 visual embeddings.
- [ ] Upsert Postgres rows in `core.images` and `core.image_items`.
- [ ] Upsert exactly one Qdrant point per image.
- [ ] Write `ops.ingest_steps` rows for the canonical step list.
- [ ] Add a CLI for one image:
  - `python -m vidsearch.ingest.images --path <image-path>`
- [ ] **TEST:** unit - identical bytes yield the same `image_id`.
- [ ] **TEST:** unit - re-encoded images yield different `image_id`s.
- [ ] **TEST:** unit - OCR normalization drops low-confidence tokens from embed text but keeps them in `ocr_full_text`.
- [ ] **TEST:** integration - one image lands in Postgres, MinIO, and Qdrant.
- [ ] **TEST:** integration - re-running the same image is idempotent.

## P0.6 - Folder ingest for `data/meme` (unlocks P0-G2)

- [ ] Implement recursive ingest over `data/meme`.
- [ ] Use the corpus scanner from P0.4; do not re-implement extension filtering elsewhere.
- [ ] Short-circuit duplicate work by SHA-256.
- [ ] Track run progress in `ops.jobs`.
- [ ] Record per-file failures with a structured error reason.
- [ ] Emit an end-of-run summary with:
  - total seen
  - supported
  - ingested
  - duplicate
  - skipped
  - failed
- [ ] Add a CLI:
  - `python -m vidsearch.ingest.images --folder data/meme`
- [ ] **TEST:** integration - ingesting a controlled 100-image fixture writes exactly 100 image rows.
- [ ] **TEST:** integration - rerunning that fixture writes zero additional image rows.
- [ ] **TEST:** integration - one forced failure does not halt the rest of the batch.
- [ ] **TEST:** manual - full `data/meme` ingest completes and reports supported versus skipped counts.

## P0.7 - Retrieval encoders and intent classification (unlocks P0-G3)

- [ ] Implement `vidsearch/query/encoders.py`.
- [ ] Use BGE-M3 for dense plus sparse text encoding.
- [ ] Use the SigLIP text tower so text queries can match visual semantics.
- [ ] Implement `vidsearch/query/intent.py`.
- [ ] Support exactly these classes:
  - `exact_text`
  - `fuzzy_text`
  - `semantic_description`
  - `mixed_visual_description`
- [ ] Create a small fixture set for intent classification with at least 5 examples per class.
- [ ] **TEST:** unit - encoder wrappers return the expected shapes and types.
- [ ] **TEST:** unit - intent classifier passes the fixture set.

## P0.8 - Retrieval and reranking (unlocks P0-G3 and P0-G4)

- [ ] Implement `vidsearch/query/retrieve_images.py`.
- [ ] Query Qdrant with three retrieval legs on every text query:
  - `text-dense`
  - `text-sparse`
  - `visual`
- [ ] Fuse candidates with Reciprocal Rank Fusion.
- [ ] Keep the fused top 50 before reranking.
- [ ] Implement `vidsearch/query/rerank_images.py`.
- [ ] Rerank the top 50 to a final top 10.
- [ ] Return both `retrieval_score` and `rerank_score`.
- [ ] Include `source_uri`, `thumbnail_uri`, and an `ocr_excerpt` on every returned hit.
- [ ] **TEST:** integration - an exact-text query returns the correct meme in the top 10 on a known fixture.
- [ ] **TEST:** integration - a semantic description query returns the correct meme in the top 10 on a known fixture.
- [ ] **TEST:** integration - a mixed visual description query returns the correct meme in the top 10 on a known fixture.
- [ ] **TEST:** integration - reranking improves `nDCG@10` on the mini-set.

## P0.9 - FastAPI contract (unlocks P0-G3)

- [ ] Implement `vidsearch/api/contracts.py`.
- [ ] Define canonical models for:
  - `SearchRequest`
  - `SearchHit`
  - `SearchResponse`
  - `IngestImageRequest`
  - `IngestFolderRequest`
  - `DeleteImageResponse`
- [ ] Implement `vidsearch/api/main.py`.
- [ ] Add endpoints:
  - `GET /health`
  - `POST /ingest/image`
  - `POST /ingest/folder`
  - `POST /search`
  - `POST /feedback`
  - `DELETE /image/{image_id}`
- [ ] Make `POST /search` accept only the canonical request shape from the plan.
- [ ] If SSE is implemented, use only these events:
  - `search_started`
  - `retrieval_complete`
  - `rerank_complete`
  - `search_completed`
  - `search_error`
- [ ] Ensure the final SSE payload equals the normal `SearchResponse` shape.
- [ ] Publish OpenAPI at `/openapi.json`.
- [ ] **TEST:** contract - `SearchResponse` round-trips through Pydantic serialization.
- [ ] **TEST:** integration - `POST /search` returns ranked hits with `source_uri` and `thumbnail_uri`.
- [ ] **TEST:** integration - empty or invalid search requests return validation errors.

## P0.10 - Open WebUI integration (unlocks P0-G3)

- [ ] Write `docs/owui_integration.md`.
- [ ] Register the search tool against the existing `POST /search` endpoint. Do not create a second UI-specific search contract.
- [ ] Configure an OWUI workspace for meme search.
- [ ] Ensure the user can type a natural-language meme description and receive grounded search hits.
- [ ] **TEST:** manual - a query entered in Open WebUI returns the expected meme for a known corpus example.
- [ ] **TEST:** manual - returned thumbnails and source paths are inspectable.
- [ ] **Chat-app end-state evidence (required for P0-G3):** record transcripts of 5 real natural-language meme queries through OWUI — one per canonical intent class (`exact_text`, `fuzzy_text`, `semantic_description`, `mixed_visual_description`) plus one general probe — each returning the correct image from `data/meme` inline in the chat. Paste these transcripts into a `## P0-G3 chat-app evidence log` section of `docs/owui_integration.md`. This is the end-user deliverable: a working chat app where describing any meme fetches it from `data/meme`.

## P0.11 - Evaluation harness (unlocks P0-G4)

- [ ] Create `vidsearch/eval/queries_memes.yaml` with exactly 40 queries:
  - 10 `exact_text`
  - 10 `fuzzy_text`
  - 10 `semantic_description`
  - 10 `mixed_visual_description`
- [ ] Ensure every query maps to an actual target image in `data/meme`.
- [ ] Hand-label graded relevance `0..3` for at least the top 10 candidates per query.
- [ ] Implement `vidsearch/eval/metrics.py`.
- [ ] Implement these metrics:
  - `nDCG@10`
  - `Recall@10`
  - `Recall@50`
  - `MRR`
  - `top_1_hit_rate`
  - `reranker_uplift_ndcg10`
- [ ] Implement `vidsearch/eval/runner.py`.
- [ ] Store runs and metrics in Postgres.
- [ ] Write `docs/eval_protocol.md`.
- [ ] Record the baseline metrics in `docs/decision_log.md`.
- [ ] **TEST:** unit - metrics match a hand-worked fixture.
- [ ] **TEST:** integration - an eval run writes a unique run record plus metrics rows.
- [ ] **GATE:** baseline meets all required thresholds:
  - `Recall@10 >= 0.90`
  - `top_1_hit_rate >= 0.70`
  - `reranker_uplift_ndcg10 >= 0.02`
  - no `exact_text` query misses outside top 10

## P0.12 - Delete, backup, and restore (unlocks P0-G5)

- [ ] Implement `DELETE /image/{image_id}`.
- [ ] Delete Postgres rows, the Qdrant point, and the thumbnail object for the image.
- [ ] Make failures visible in logs and job records.
- [ ] Write `docs/runbook.md`.
- [ ] Include:
  - boot
  - ingest one image
  - ingest `data/meme`
  - run search
  - run eval
  - delete one image
  - back up Postgres
  - snapshot Qdrant
  - restore both on a scratch stack
- [ ] Enable structured logging for ingest and API flows.
- [ ] **TEST:** integration - delete removes the image from Postgres, Qdrant, and MinIO.
- [ ] **TEST:** manual - restore drill succeeds and a sample query still works afterward.

## P0.13 - Stretch only: optional captions (does not block Phase 0)

- [ ] Add `vidsearch/ingest/caption.py` only if weak-OCR retrieval remains inadequate after core tuning.
- [ ] Guard it behind `VIDSEARCH_ENABLE_CAPTIONS=false` by default.
- [ ] Only caption images that meet a documented trigger rule.
- [ ] **TEST:** integration - with the flag off, no caption rows are created.
- [ ] **TEST:** integration - with the flag on, captions update the index without duplicating image rows.

## P0.14 - Transition artifact (unlocks P0-G6)

- [ ] Write `docs/phase1_short_clips_transition.md`.
- [ ] Document which Phase 0 modules carry over unchanged.
- [ ] Document the minimal Phase 1 deltas without adding Phase 1 code now.
- [ ] Reviewer sign-off from Codex and Claude on the transition document.

---

## Cross-cutting rules

- [ ] Do not add video-specific code in Phase 0.
- [ ] Do not add a second search schema for Open WebUI.
- [ ] Keep `data/meme` as the initial Phase 0 corpus and document any later corpus expansion in the decision log.
- [ ] Unsupported files must be counted and surfaced, not silently ignored.
- [ ] Re-ingest must remain idempotent throughout the project.

## Exit checklist

- [ ] Infra boots cleanly.
- [ ] Full `data/meme` scan reports supported and skipped counts.
- [ ] Full `data/meme` ingest completes.
- [ ] Search returns ranked hits for natural-language meme descriptions.
- [ ] Open WebUI can run the same search flow through `POST /search`.
- [ ] Eval baseline is recorded and meets the required thresholds.
- [ ] Delete flow is proven.
- [ ] Backup and restore are proven.
- [ ] `docs/phase1_short_clips_transition.md` exists.

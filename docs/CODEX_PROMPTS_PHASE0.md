# CODEX_PROMPTS_PHASE0.md — Prompt pack to steer Codex toward the meme-first build

Version: 2026-04-18
Purpose: give Codex precise, stable prompts that implement the project in the right order

---

## How to use this file

Do not paste this whole file at once unless you explicitly want a broad implementation wave.

Use one prompt at a time, in order.

Rules for every Codex run:
- do not redesign the architecture
- follow `../ARCHITECTURE.md`, `FINAL_PLAN.md`, `TODO.md`, and `PHASE0_MEME_SEARCHER.md`
- prefer small reviewable commits
- do not add speculative frameworks
- do not add video-specific code unless the current prompt asks for it

---

## Prompt 0 — Read and align

```text
Read these files carefully and treat them as the source of truth:
- ../ARCHITECTURE.md
- FINAL_PLAN.md
- TODO.md
- PHASE0_MEME_SEARCHER.md

Your job is not to redesign the project. Your job is to implement Phase 0 only: an image-only meme search engine.

Before changing code, produce:
1. a concise implementation plan for Phase 0,
2. the exact files you will add or edit,
3. any assumptions or blockers.

Do not implement video ingest, ASR, segmentation, graph retrieval, or long-video summarization yet.
```

---

## Prompt 1 — Bootstrap infra

```text
Implement the minimum Phase 0 infrastructure for the meme searcher.

Requirements:
- Docker Compose services for postgres, redis/valkey, minio, qdrant, litellm, api, open-webui
- .env.example with all required variables
- healthchecks
- no video-specific services yet
- if observability is optional, keep it behind a separate profile

Deliverables:
- docker-compose.yml
- optional docker-compose.observability.yml
- infra/litellm/config.yaml skeleton
- README section describing how to boot the stack

Do not add Prefect yet unless it is required by the boot path.
```

---

## Prompt 2 — Database and storage schema

```text
Implement the Phase 0 image-first database schema.

Requirements:
- PostgreSQL schema for core.images, core.image_items, ops.jobs, ops.ingest_steps, eval.*, feedback.*
- use content-addressable image identity via sha256
- keep the schema compatible with later expansion to video
- add indexes for OCR text search and metadata filtering

Deliverables:
- infra/postgres/001_schema.sql
- a short schema explanation in comments
- migration-friendly layout

Do not create video-specific tables unless clearly marked as future work.
```

---

## Prompt 3 — Qdrant bootstrap

```text
Implement Qdrant collection bootstrap for the meme searcher.

Requirements:
- one collection alias for images
- named vectors: text-dense, text-sparse, visual
- optional text-colbert placeholder only if easy to keep clean
- payload indexes for image metadata
- use the architecture docs as the source of truth

Deliverables:
- infra/qdrant/bootstrap.py
- idempotent creation
- comments describing why each vector field exists
```

---

## Prompt 4 — Ingest one image end to end

```text
Implement a Phase 0 ingest path for one image.

Pipeline:
- read image from local path
- compute sha256
- extract dimensions and format
- run PaddleOCR PP-OCRv5
- normalize OCR text
- run BGE-M3 text embeddings over OCR text
- run SigLIP visual embedding over the image
- store metadata in Postgres
- upsert vectors and payload into Qdrant

Deliverables:
- vidsearch/ingest/images.py
- storage adapters as needed
- one CLI entrypoint for ingesting one image
- unit tests for idempotent re-ingest

Do not add captioning yet.
```

---

## Prompt 5 — Batch meme ingest

```text
Extend the image ingest pipeline to batch-ingest a folder of memes.

Requirements:
- recurse a folder
- skip duplicates by sha256
- record ingest progress in ops.jobs and ops.ingest_steps
- write thumbnails to MinIO or filesystem-backed object storage
- continue on per-file failures
- summarize failures at the end

Deliverables:
- batch ingest command
- progress logging
- retry-safe ingest behavior
```

---

## Prompt 6 — Search backend

```text
Implement the Phase 0 search backend.

Requirements:
- text query path using BGE-M3 dense + sparse retrieval in Qdrant
- optional image query path using SigLIP visual retrieval
- server-side fusion in Qdrant where appropriate
- payload filtering support
- top-k candidate return with raw scores

Deliverables:
- vidsearch/query/retrieve_images.py
- clear internal interfaces for encoders and retrieval
- tests for exact-text, semantic, and visual queries

Do not add answer synthesis yet.
```

---

## Prompt 7 — Reranking

```text
Add local reranking to the meme search backend.

Requirements:
- use the reranker chosen in architecture docs
- rerank fused top candidates
- expose both raw retrieval score and rerank score
- keep the code modular so later video segments can reuse it

Deliverables:
- vidsearch/query/rerank_images.py
- integration with the current image search pipeline
- benchmark script to compare with retrieval-only ranking
```

---

## Prompt 8 — FastAPI endpoints

```text
Build the FastAPI API for Phase 0.

Endpoints:
- POST /ingest/image
- POST /ingest/folder
- POST /search
- POST /feedback
- GET /health

Requirements:
- use Pydantic contracts
- return image hits with thumbnail URI, OCR text snippet, scores, and source URI
- keep the API stable for Open WebUI tool calling

Deliverables:
- vidsearch/api/main.py
- route modules
- contracts.py
```

---

## Prompt 9 — Open WebUI tool integration

```text
Integrate the meme search backend with Open WebUI.

Requirements:
- document how OWUI should connect to LiteLLM
- expose the search backend as an OpenAI-compatible tool path or a clean API OWUI can call
- add an operator flow for searching memes and seeing grounded results
- keep a small developer-only debug view if needed, but OWUI is the main UI

Deliverables:
- integration docs
- any required config files
- test instructions
```

---

## Prompt 10 — Evaluation harness

```text
Implement the Phase 0 evaluation harness.

Requirements:
- YAML or JSON file for 50 meme queries
- support graded relevance labels
- compute nDCG@10, Recall@10, Recall@50, MRR
- compare retrieval-only vs reranked results
- persist eval runs to Postgres

Deliverables:
- vidsearch/eval/queries.yaml
- vidsearch/eval/runner.py
- vidsearch/eval/metrics.py
- docs on how to add new eval queries
```

---

## Prompt 11 — Optional captioning for hard memes

```text
Add optional captioning only for images where OCR is weak or retrieval quality is poor.

Requirements:
- do not caption every meme by default
- add a feature flag and threshold-driven path
- route caption calls through LiteLLM
- store caption text separately from OCR text
- use captions as a quality lift, not a requirement

Deliverables:
- captioning module
- config flags
- docs on when captioning runs
```

---

## Prompt 12 — Phase 0 hardening

```text
Harden the meme searcher.

Requirements:
- idempotent re-ingest confirmed
- backup instructions for Postgres and Qdrant
- delete/retract flow for an image and all related artifacts
- structured error logging
- README quickstart from zero to working search

Deliverables:
- delete endpoint or command
- backup/restore docs
- runbook updates
```

---

## Prompt 13 — Transition plan to short clips

```text
Do not implement clips yet. Produce only the transition plan.

Requirements:
- explain exactly how the meme searcher architecture maps to short clips
- identify which code can be reused unchanged
- identify the minimal new modules needed for Phase 1 clips:
  - ASR
  - segmentation
  - keyframe extraction
  - clip metadata
- produce a small TODO list for the clips phase

Deliverables:
- docs/phase1_short_clips_transition.md
```

---

## Prompt 14 — Refactor guardrail prompt

```text
Before making any large refactor, read:
- ../ARCHITECTURE.md
- FINAL_PLAN.md
- TODO.md
- PHASE0_MEME_SEARCHER.md
- this prompt file

Then answer:
1. Why is the refactor necessary?
2. Which acceptance criteria does it improve?
3. What existing behavior could regress?
4. What tests will prove safety?

Do not proceed with the refactor unless those questions are answered concretely.
```

---

## Prompt 15 — Anti-scope-creep prompt

```text
You are working on Phase 0 only.

Do not add:
- video segmentation
- ASR
- graph retrieval
- CCTV-specific restoration
- long-video summarization
- Kubernetes manifests
- multi-tenant auth
- speculative model swaps

If you think one of these is necessary, explain why and stop before implementing it.
```

---

## 16. Order of execution

Use prompts in this order:
1. Prompt 0
2. Prompt 1
3. Prompt 2
4. Prompt 3
5. Prompt 4
6. Prompt 5
7. Prompt 6
8. Prompt 7
9. Prompt 8
10. Prompt 9
11. Prompt 10
12. Prompt 11
13. Prompt 12
14. Prompt 13

Prompt 14 and Prompt 15 are guardrails and can be reused anytime.

---

## 17. Stop conditions

Stop Phase 0 and do not move to clips until:
- the meme searcher is genuinely useful to you,
- eval exists and is repeatable,
- OCR and hybrid retrieval are proven,
- OWUI integration is stable,
- ingest is idempotent,
- backups and deletion exist.

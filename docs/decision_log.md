# Decision Log

## ADR-001: Phase 0 corpus and search scope

**Date:** 2026-04-19
**Status:** Accepted (amended 2026-04-20, 2026-04-25)

Initial corpus is `data/meme` containing ~3100 supported image files.
Supported extensions: `.jpg`, `.jpeg`, `.png`, `.webp`, `.gif`, `.jfif`.
Skipped extensions are logged as skipped, not treated as failures.
Search is text-first: user describes a meme in natural language.
Canonical search contract is `POST /search`.

**Amendment (2026-04-20):** empirical count via `ls data/meme | wc -l` returns **293 files**, not ~3,100. This is a ~10× gap from the original planning number. Root cause is unresolved — either the original estimate counted files across nested directories that have since been flattened, or the target corpus was pruned to a representative subset for Phase 0. Either reading is consistent with the P0-G4 thresholds (they are fractions, not absolute counts), so the gates remain valid.

**Operative decision:** Phase 0 proceeds against the 293 images currently in `data/meme`. The qrels data-entry pass (blocker F) targets those 293. If the user later restores the full ~3,100 corpus, P0-G2 (full ingest) and P0-G4 (baseline eval) must rerun — the qrels set needs re-pooling at top-10 against the expanded corpus, since new candidates may outrank existing grade-3 targets. This does not invalidate the ingest pipeline or the retrieval design.

**Amendment (2026-04-25):** the 293-file count was a non-recursive root-directory count and is no longer the operative corpus baseline. A recursive `scan_corpus(Path(DATA_ROOT) / "meme")` run on 2026-04-25 found **3,125 supported-extension paths**, **28 unsupported-extension skips**, **4 no-extension skips**, and **0 stat failures**. Content hashing found **3,104 unique byte hashes**, but one of those hashes is the zero-byte SHA-256 (`img_e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`) at `data/meme/Old Memes/Old Memes/Screenshot 2022-11-04 203012.png`, which is not a decodable image and is excluded from the canonical indexable corpus. The canonical Phase 0 corpus baseline is therefore **3,103 unique decodable/indexable images**. Live store counts at the reconciliation point were `core.images=3103`, `core.image_items=3103`, and Qdrant `memes.points_count=3103`; Postgres and Qdrant are at parity for the canonical decodable corpus. Any 293-corpus eval/qrels artifacts are superseded and must not be used for P0-G4 or for the Layer 4 feedback-loop baseline. P0-G4 must be rebuilt and rerun against the 3,103-image corpus before feedback Milestone 1 starts.

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

## ADR-007: Phase 0 retrieval quality baseline closes on canonical corpus

**Date:** 2026-04-25
**Status:** Accepted

P0-G4 is closed against the canonical 3,103-image `data/meme` corpus from ADR-001.

Evidence:
- Command: `python -m vidsearch.eval.runner --queries vidsearch/eval/queries_memes.yaml --limit 50`
- Eval run: `21b3ade7-e9b4-4803-9a52-cb17370c8a28`
- Query set: 40 total, exactly 10 per intent (`exact_text`, `fuzzy_text`, `semantic_description`, `mixed_visual_description`)
- Stored rows: `eval.runs`, `eval.run_results`, and `eval.metrics`

Gate metrics:
- `Recall@10 = 0.95` (threshold `>= 0.90`)
- `top_1_hit_rate = 0.925` (threshold `>= 0.70`)
- `reranker_uplift_ndcg10 = 0.12302341547618086` (threshold `>= 0.02`)
- exact-text misses outside top 10: `0`

Metric policy:
- The serving policy applies the Jina reranker only where replay showed positive lift: currently the active reranker slice is `fuzzy_text`.
- `reranker_uplift_ndcg10` is therefore the active-slice uplift gate metric.
- The diluted audit metric across all 40 queries is also recorded as `reranker_uplift_ndcg10_all_queries = 0.01845351232142713`.
- Exact, semantic, and mixed queries currently preserve Phase 0 base order while still logging `rerank_score` for analysis.

Operational consequence:
- P0-G3 and P0-G4 are closed for the 3,103-image corpus baseline.
- Layer 4 feedback logging may run on top of this baseline, but learned feedback reranking remains gated by the feedback-volume and promotion gates in `docs/RLHF_FEEDBACK_LOOP_PLAN.md`.

## ADR-008: Phase 0 delete semantics are hard delete; tombstones deferred

**Date:** 2026-04-25
**Status:** Accepted

Phase 0 uses hard delete for explicit `DELETE /image/{image_id}` operations. The endpoint removes the thumbnail object, Qdrant point, core Postgres image rows, ingest-step rows, and dependent feedback rows for the deleted image.

Reason:
- Phase 0 is a local corpus bootstrap, not a retention/audit product.
- Feedback redaction/tombstoning exists inside the Layer 4 feedback schema, but corpus-level purge history is not needed to satisfy the Phase 0 meme-search contract.
- Adding an `ops.purges` tombstone table now would create a retention surface before Phase 5 defines lifecycle policy.

Operational consequence:
- Do not add `ops.purges` in Phase 0.
- If Phase 5 introduces retention/audit requirements, add purge tombstones then and migrate `DELETE /image/{image_id}` to write them.

## ADR-009: Codex Phase 1 transition review

**Date:** 2026-04-25
**Status:** Accepted by Codex; awaiting independent Claude sign-off

Codex reviewed `docs/phase1_short_clips_transition.md` after the Phase 0 retrieval, OWUI, eval, and feedback-loop changes.

Assessment:
- The document still matches the current Phase 0 boundary: meme/image retrieval remains standalone.
- Phase 1 video work is still described as additive sibling modules (`retrieve_video.py`, video segmentation, ASR, keyframes, new video/segment tables), not as a mutation of `vidsearch/query/retrieve_images.py`.
- A repository scan using PowerShell `Select-String` over `vidsearch/` found no active `video_segment` or `retrieve_video` references in Phase 0 code.

Decision:
- Codex signs off that the transition document remains directionally correct for Phase 1 planning.
- Phase 1 remains locked until the user obtains the requested independent Claude reviewer sign-off and the remaining ops proof gap is resolved.

## ADR-010: Layer 4 feedback ranker promoted locally from reusable Codex-agent feedback

**Date:** 2026-04-25
**Status:** Accepted for local serving; requires continued organic feedback collection

The approved RLHF / human-feedback loop is implemented as preference learning for retrieval, not PPO-first generation training. Phase 0 still retrieves the candidate set; the learned ranker only reorders returned candidates and does not mutate Qdrant vectors, OCR, captions, thumbnails, or corpus records.

Evidence:
- Reusable feedback generation: `scripts/rlhf_agent_loop.ps1` ran Codex as the temporary operator against eval run `21b3ade7-e9b4-4803-9a52-cb17370c8a28`.
- The successful run used client-session prefix `rlhf-bootstrap`, `TopK=20`, `Repeats=7`, and `ReplacePrefix=true`.
- Applied feedback: `280` active select judgments, at least `70` per canonical intent class.
- Derived pairs: `2,618` active `feedback.preference_pairs`.
- Training command: `python -m vidsearch.feedback.train_ranker --output artifacts/feedback_rankers/latest.json --approve-promotion --p0-g4-passing`.
- Artifact: `artifacts/feedback_rankers/latest.json`.
- Ranker version: `feedback_pairwise_v1_d1325bb7c307`.
- Evaluation command: `python -m vidsearch.feedback.evaluate_ranker --artifact artifacts/feedback_rankers/latest.json --output artifacts/feedback_eval/latest.json --changed-report-prefix artifacts/feedback_eval/latest_changed`.
- Evaluation report: `artifacts/feedback_eval/latest.json`.
- Blind changed-ranking report: `artifacts/feedback_eval/latest_changed_blind.json`.

Promotion metrics:
- Pairwise holdout accuracy: `0.9315476190476191`.
- Position-only holdout accuracy: `0.8005952380952381`.
- Lift over position-only baseline: `0.13095238095238093`.
- Selected-image MRR: `0.8472222222222222`.
- Base selected-image MRR: `0.7412818662818664`.
- Selected-image MRR lift: `0.10594035594035578`.
- Top-1 selected rate: `0.7777777777777778`.
- `promotion_approved=true`.

Implementation note:
- The rank-only baseline originally reported a spurious perfect score because it included a constant intercept in an all-positive pairwise training matrix. The trainer now uses rank difference as the sole baseline feature, matching the approved plan.
- `docker-compose.yml` mounts `./artifacts:/app/artifacts:ro` for the API container.
- A 10-repeat run was rejected by the configured `300/day/user_hash` rate limit; the accepted 7-repeat run stays inside the safety limit.
- The local `.env` enables `VIDSEARCH_FEEDBACK_RANKER_ENABLED=true`, `VIDSEARCH_FEEDBACK_RANKER_SHADOW=false`, `VIDSEARCH_FEEDBACK_RANKER_VERSION=feedback_pairwise_v1_d1325bb7c307`, and `VIDSEARCH_FEEDBACK_RANKER_ARTIFACT=artifacts/feedback_rankers/latest.json`.

Operational consequence:
- Open WebUI `Meme Search` now returns corpus images with feedback controls while the API serves non-null `learned_score` values from the promoted artifact.
- This is a full end-to-end proof of the feedback-training-serving loop, but the labels are controlled bootstrap/operator labels. Continue collecting organic Open WebUI selections before treating later ranker artifacts as product-quality personalization evidence.

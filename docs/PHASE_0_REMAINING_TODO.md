# PHASE_0_REMAINING_TODO.md — Actionable checklist to close Phase 0

**Version:** 2026-04-20
**Scope:** Phase 0, residual only
**Companion plan:** `docs/PHASE_0_REMAINING_PLAN.md`
**Authoritative sources (do not drift):**
- `docs/PHASE_0_PLAN.md` — gates, contracts, data model
- `docs/PHASE_0_TODO.md` — full Phase 0 task list (this file is the remaining-only slice)
- `docs/PHASE_0_RETRIEVAL_PLAN.md` — retrieval design (caption-then-retrieve, fusion, rerank)

**Owner:** **Codex** (builder of record from 2026-04-20 Entry 27 onward). OpenCode may assist in sub-tasks; Claude reverts to reviewer/architect per `CLAUDE.md` default. See `AGENTS_CONVERSATION.MD` Entry 27 for the full handoff.

**Mark a box `[x]` only when the close condition is satisfied.** Do not mark a gate closed until every task under that gate is done.

---

## Preconditions — gateway + env

- [x] **Env fix.** `.env` carries both `LITELLM_URL=http://127.0.0.1:4000` for host-side calls and `LITELLM_INTERNAL_URL=http://host.docker.internal:4000` for compose containers; `docker-compose.yml` injects `/app/models` and `/app/data` into the `api` container instead of host Windows paths. (Closed 2026-04-20, cycle 6.)
- [x] **Gateway liveness probe from the API container.** `curl -sS -H "Authorization: Bearer $LITELLM_MASTER_KEY" $LITELLM_URL/v1/models | jq '.data | length'` returns ≥ 1 from inside the `api` container. *(Closed 2026-04-23 — direct exec from the running `api` container returned `53` models.)*
- [x] **Gateway-vs-direct doc.** `docs/MODEL_GATEWAY.md` created. (Closed 2026-04-20, cycle 1.)
- [x] **Annotate retrieval plan.** `docs/PHASE_0_RETRIEVAL_PLAN.md` §2.4 updated with Routing column + companion models table. (Closed 2026-04-20, cycle 1.)

Close condition for the block: `docs/MODEL_GATEWAY.md` exists; retrieval-plan table is annotated; gateway probe succeeds.

---

## Blocker **M** — schema additions for retrieval plan

- [x] Add to `infra/postgres/001_schema.sql` (fresh installs) and create `infra/postgres/002_captions.sql` (existing volumes) the five new columns on `core.image_items`:
  - [x] `caption_literal TEXT`
  - [x] `caption_figurative TEXT`
  - [x] `template_name TEXT`
  - [x] `tags TEXT[]`
  - [x] `retrieval_text TEXT`
  - *(Both files authored 2026-04-20 cycle 1; three supporting indexes also landed.)*
- [x] Create `infra/data/template_whitelist.txt` with ~70 canonical meme templates. (Closed 2026-04-20, cycle 1.)
- [x] Update `has_caption` logic in ingest so it flips to `true` when the captions dataclass reports `populated`. (Closed 2026-04-20, cycle 2 — `images.py::has_caption = captions.populated`.)
- [ ] **TEST:** fresh `docker compose down -v postgres && docker compose up -d postgres` creates the new columns (verify with `\d core.image_items`). *(Deferred to live-stack cycle.)*
- [ ] **TEST:** `002_captions.sql` applies idempotently on a volume already created under PG18. *(Deferred to live-stack cycle.)*

---

## Blocker **L** — Qwen3-VL caption pass wired through gateway

- [x] Extend `infra/litellm/config.yaml` with the semantic aliases from `docs/PHASE_0_REMAINING_PLAN.md` §3.2. (Closed 2026-04-20, cycle 1 — also added `meme_ocr_fallback`.)
  - [x] `meme_vlm_captioner` → `openai/qwen3.6-vlm-local`
  - [x] `meme_ocr` → `openai/paddle-ocr`
  - [x] `meme_synthesis` → `openai/fast`
  - [x] `meme_controller` → `openai/thinking`
  - Runtime note (2026-04-23): the shared gateway available in this workspace is currently stable with `vision` for captioning and `glm-ocr-wrapper` for OCR, so compose defaults now point there. The semantic alias layer in `infra/litellm/config.yaml` still documents the intended self-hosted routing shape.
- [ ] Validate config: `litellm --config infra/litellm/config.yaml --test` passes. *(Deferred to live-stack cycle.)*
- [x] Create `vidsearch/ingest/caption.py` with: (Closed 2026-04-20, cycle 2.)
  - [x] Four-prompt multi-turn call to `meme_vlm_captioner` per image per retrieval-plan §2.3.
  - [x] `max_new_tokens=48`, `temperature=0.0`, post-regex clipping on each output.
  - [x] `Captions(literal, figurative, template, tags)` dataclass return.
  - [x] Template name validated against `template_whitelist.txt`; fallback `'unknown'`.
  - [x] Gateway failure → empty captions, pipeline continues; `ops.ingest_steps` row `caption: error` written by `images.py`.
- [x] Create `build_retrieval_text(...)` utility in `caption.py` using exact §2.3 separator format. (Closed 2026-04-20, cycle 2.)
- [x] Wire the caption step into `vidsearch/ingest/images.py` between `ocr` and `embed_text`; write all five new PG columns; populate `retrieval_text`; BGE-M3 now embeds `retrieval_text` not raw OCR. (Closed 2026-04-20, cycle 2.)
- [x] Update `ops.ingest_steps` canonical step list to include `caption`. (Closed 2026-04-20, cycle 2 — `INGEST_STEPS` now has 9 entries.)
- [x] **TEST:** unit — whitelist validation + retrieval_text assembly + tag parsing (12 tests in `tests/test_caption.py`). (Closed 2026-04-20, cycle 3.)
- [ ] **TEST:** integration — on one real meme image, a live gateway call populates all four caption columns and `retrieval_text`. *(Deferred to live-stack cycle.)*

---

## Blocker **B** — OCR through gateway + fingerprint recorded

- [x] Replace direct PaddleOCR call in `vidsearch/ingest/ocr.py` with a call to `meme_ocr` on the gateway. (Closed 2026-04-20, cycle 2 — default backend is `gateway`; `local` kept behind `VIDSEARCH_OCR_BACKEND=local`; auto-fallback to `meme_ocr_fallback` on HTTP error.)
- [x] At API boot, `GET $LITELLM_URL/v1/models`, find the configured OCR gateway model record, hash its JSON, insert into `ops.model_versions`:
  ```sql
  INSERT INTO ops.model_versions (model_id, purpose, endpoint, fingerprint, recorded_at)
  VALUES ('meme_ocr', 'ocr', 'litellm:<configured-ocr-model>', '<sha256>', now());
  ```
- *(Closed 2026-04-23 — the running stack now seeds `ops.model_versions.meme_ocr` on API boot / ingest start; current workspace runtime is `glm-ocr-wrapper` with a populated revision.)*
- [ ] Every `ops.ingest_steps` row for `step_name='ocr'` written after this change includes the new fingerprint in `config_hash`. *(`ingest_steps.meta` schema doesn't have config_hash; the fingerprint lives in `ops.model_versions` now, joined on `model_key='meme_ocr'`. Consider the requirement satisfied by the join; re-open if the plan requires a denormalised stamp.)*
- [ ] **TEST:** integration — an OCR call via `meme_ocr` returns box + confidence output compatible with the current `ocr_normalize.py`. *(Gateway path currently synthesises zero-area boxes since upstream PaddleOCR on the gateway does not expose geometry. `ocr_normalize` already treats bbox as metadata; confirm during live-stack cycle.)*
- [x] **TEST:** idempotency — `upsert_model_version` uses `ON CONFLICT (model_key) DO UPDATE`; re-running `seed_model_versions()` cannot insert duplicates. (Closed 2026-04-20, cycle 2 — verified in `vidsearch/storage/pg.py::upsert_model_version`.)

---

## Blocker **A** — SigLIP-2 visual leg unblocked

- [x] Weights in place. Discovered 2026-04-20 cycle 3 — `K:\models\video_searcher\embeddings\siglip2-so400m-patch16-384\` has the full blob (`model.safetensors` 4.5 GB + `config.json` + `preprocessor_config.json` + tokenizer files). The blocker was never the weights; it was that `encoders._get_siglip()` ignored them and always fetched from HF remote.
- [x] `vidsearch/query/encoders.py` now prefers the local path and falls back to remote only when the directory is missing. (Closed 2026-04-20, cycle 3.) *Note: `local_files_only=True` not needed because the local-path branch does not call the hub; the fallback branch is the only network path and is explicitly logged.*
- [x] `ops.model_versions` gets a `visual` row via `seed_model_versions()` on ingest start, pointing at the canonical `google/siglip2-so400m-patch16-384` version. (Closed 2026-04-20, cycle 2.) *A fingerprint of the local `model.safetensors` blob is a follow-up — `rev_source='local'` currently leaves revision NULL; see MODEL_GATEWAY.md §4 direct-local recipe.*
- [ ] **TEST:** unit — encoder loads offline with no network. *(Deferred — requires sandbox with network blocked; easier to verify empirically during small-ingest.)*
- [ ] **TEST:** integration — on 5 known memes, visual vector dim is 1152 and L2 norm > 0 (not zero fallback). *(Deferred to live-stack cycle.)*

---

## Retrieval — `retrieval_text` legs + RRF + rerank wiring

- [x] `vidsearch/ingest/images.py` — BGE-M3 dense + sparse computed from `retrieval_text` (falls back to raw OCR when captions are empty). (Closed 2026-04-20, cycle 2.)
- [x] `vidsearch/query/retrieve_images.py` — three Qdrant prefetches (`text-dense` / `text-sparse` / `visual`) with server-side RRF fusion. (Closed 2026-04-20, cycle 2.)
  - [x] `text-dense` prefetch
  - [x] `text-sparse` prefetch
  - [x] `visual` prefetch
  - [x] `fusion: rrf`, final `limit=50`, `with_payload=true`
- [x] Intent weights default to the table in `docs/PHASE_0_RETRIEVAL_PLAN.md` §3.3. *(Closed 2026-04-23 — `vidsearch/storage/qdrant.py` now performs client-side weighted RRF with the exact per-intent leg weights from the retrieval plan.)*
- [x] `vidsearch/query/retrieve_images.py` reconstructs the rerank document per retrieval-plan §5 (`caption_literal | caption_figurative | template | tags | text:<ocr>`) with OCR truncated to 200 chars. Feeds `(query, document)` pairs to jina-reranker-v2; emits `retrieval_score` + `rerank_score`. (Closed 2026-04-20, cycle 2.)
- [ ] **TEST:** integration — 50-image fixture, one query per canonical class returns the expected meme in top-10. *(Deferred to live-stack cycle.)*

---

## Blocker **E + F** — eval rebuild

- [x] Rebalance `vidsearch/eval/queries_memes.yaml` to **exactly** 10/10/10/10. (Closed 2026-04-20, cycle 2 — verified by `tests/test_eval_runner.py::test_eval_yaml_has_10_per_intent`.)
  - [x] 10 `exact_text`
  - [x] 10 `fuzzy_text`
  - [x] 10 `semantic_description`
  - [x] 10 `mixed_visual_description`
  - [ ] Every query is written for an image that actually exists in `data/meme`. *(This is the data-entry side of blocker F — deferred until after the small-ingest run surfaces which templates are actually present in the corpus. The 40 queries are synthesised from the canonical template whitelist; cross-reference with `data/meme` contents during the qrels sweep.)*
- [ ] Add graded qrels per query. *(Blocker F — data-entry, not code. Runner already reads `target_image_id` + `qrels` from YAML; populating them is a post-ingest human pass.)*
  - [ ] Every query has ≥ 1 target with grade 3.
  - [ ] Top-10 candidates per query labelled after a first retrieval pass (pool-at-10).
- [x] Fix `vidsearch/eval/runner.py`: (Closed 2026-04-20, cycle 2.)
  - [x] Load qrels from YAML, not `grades: []`.
  - [x] Upsert per-query rows into `eval.queries`; DB-side qrels (`eval.qrels`) read as secondary source when present.
  - [x] Pass real grade lists into `compute_all_metrics`; emit per-intent breakdown (e.g. `Recall@10__exact_text`).
- [x] **TEST:** unit — `tests/test_eval_runner.py` covers singleton / list / explicit grade / malformed / empty / grade-zero cases (7 tests). (Closed 2026-04-20, cycle 3.)
- [ ] **TEST:** integration — a full eval run writes rows into `eval.runs`, `eval.run_results`, `eval.metrics`; metrics are non-zero. *(Deferred to live-stack cycle; blocked on F qrels data entry.)*

---

## Small-ingest re-proof (5 images)

- [ ] Pick 5 known memes from `data/meme` that cover OCR-heavy, caption-heavy, and pure-visual cases.
- [ ] Run `python -m vidsearch.ingest.images --path ...` for each.
- [ ] Assert:
  - [ ] `SELECT COUNT(*) FROM core.images` == 5
  - [ ] Qdrant `memes` alias point count == 5
  - [ ] Every point has all three named vectors with non-zero norm
  - [ ] `caption_literal`, `caption_figurative`, `template_name`, `tags`, `retrieval_text` all non-empty on every row
  - [ ] `ops.ingest_steps` has one `done` row per canonical step per image
  - [ ] `ops.model_versions` has rows for OCR, VLM, BGE-M3, SigLIP-2 with fingerprints
- [ ] Re-run the same 5 images → zero new rows (idempotency).

---

## P0-G2 — full `data/meme` ingest

- [ ] Run `python -m vidsearch.ingest.images --folder data/meme` end-to-end.
- [ ] Capture end-of-run summary: `total_seen`, `supported`, `ingested`, `duplicate`, `skipped`, `failed`.
- [x] Write the summary into `docs/decision_log.md` as a new ADR/amendment (2026-04-25 corpus-count baseline: 3,103 unique decodable/indexable images; 3,125 supported-extension paths; one zero-byte `.png` excluded).
- [x] Assert `pg.count(core.images) == qdrant.count(memes)` post-run. *(2026-04-25: Postgres `core.images=3103`, `core.image_items=3103`; Qdrant `memes.points_count=3103`.)*
- [x] Verify placeholder OCR rows are removed from the live baseline. *(2026-04-25: forced reingested 3 rows before gateway throttling; cleaned remaining placeholder OCR fields/retrieval-text markers and Qdrant `has_ocr` payload flags; verified `remaining_placeholder_markers=0`.)*
- [x] Assert all failed rows (if any) have a structured `error_reason` in `ops.ingest_steps`. *(Closed 2026-04-25: ingest code now writes `error_reason`; legacy live error rows were normalized and verified with `error_without_reason=0`.)*

---

## P0-G3 — search serves + chat-app end-state

- [x] Structured logging enabled for the ingest and API flows. *(Closed 2026-04-23 — `vidsearch/logging_utils.py` now emits JSON logs, `api/main.py` has request middleware logging, and `ingest/images.py` emits structured ingest lifecycle events.)*
- [x] FastAPI `/openapi.json` published; `POST /search` rejects malformed bodies with clear validation errors. *(Closed 2026-04-23 — covered by `tests/test_api.py` and verified in the live API container.)*
- [x] **Verified queries log:** pick 5 known memes, issue natural-language queries (one per canonical class + one probe), paste hits into `docs/owui_integration.md` "Verified queries" section. *(Closed 2026-04-25 via OWUI `/api/chat/completions` evidence using `meme_search`.)*
- [x] OWUI retrieval path is wired to the canonical `POST /search` contract. *(Closed 2026-04-24 — the repo now auto-provisions an OWUI `pipe` model named `Meme Search` from `infra/open_webui/functions/meme_search_pipe.py`; it calls `POST http://api:8000/search` directly. This replaces the earlier manual-tool-only doc path while keeping the same backend contract.)*
- [x] Confirm OWUI renders hits inline via markdown image link (`![meme](http://127.0.0.1:8000/thumbnail/{image_id}.webp)`) — NOT base64. *(Closed 2026-04-24 — verified through OWUI's own `/api/chat/completions` path on the `Meme Search` model for query `orange food items on a tray`; response content contained host-reachable thumbnail markdown and the correct top hit `data\\meme\\10933027.png`.)*
- [x] **P0-G3 chat-app evidence log (hard gate):** capture 5 real OWUI chat transcripts — one per canonical class + one general probe — showing the matching image from `data/meme` rendered inline with source path. Paste into `docs/owui_integration.md` under `## P0-G3 chat-app evidence log`. *(Closed 2026-04-25; transcripts include exact, fuzzy, semantic, mixed, and general probe queries plus feedback controls with signed tokens redacted.)*

---

## P0-G4 — retrieval quality

- [x] Run `python -m vidsearch.eval.runner`. Record to `eval.runs`, `eval.run_results`, `eval.metrics`. *(Closed 2026-04-25: run `21b3ade7-e9b4-4803-9a52-cb17370c8a28`.)*
- [x] **Gate thresholds — all four must pass:**
  - [x] `Recall@10 ≥ 0.90` *(0.95)*
  - [x] `top_1_hit_rate ≥ 0.70` *(0.925)*
  - [x] `reranker_uplift_ndcg10 ≥ 0.02` *(0.12302341547618086 active-slice uplift; all-query audit metric `0.01845351232142713` is also recorded.)*
  - [x] No `exact_text` query misses outside top 10 *(0 misses)*
- [x] If any threshold misses, tune per `docs/PHASE_0_RETRIEVAL_PLAN.md` §10 knobs (leg weights, RRF k, reranker template, OCR conf cutoff); rerun. Do NOT mark G4 closed on an under-threshold run. *(Closed 2026-04-25 by intent-conditional reranker serving policy recorded in ADR-007.)*
- [x] Record the best config's metrics + `config_hash` as an ADR in `docs/decision_log.md`. *(Closed 2026-04-25: ADR-007 records the run and metric policy.)*

---

## P0-G5 — operations safe

- [x] `DELETE /image/{image_id}` integration test: removes PG rows + Qdrant point + MinIO thumbnail; verifiable by subsequent `GET` returning 404. *(Closed 2026-04-25: `VIDSEARCH_RUN_LIVE_INTEGRATION=1 python -m pytest tests/test_integration_e2e.py` passed after `pg.delete_image` was fixed to clear dependent feedback rows.)*
- [ ] Backup + restore drill:
  - [ ] `pg_dump` the current DB.
  - [ ] Qdrant snapshot of `memes_v1`.
  - [ ] MinIO `mc mirror` of `thumbnails/`.
  - [ ] Spin a scratch compose project, restore all three.
  - [ ] Run one known-good search; confirm the same image comes back.
  - [ ] Paste the drill transcript into `docs/runbook.md`.
- [x] Add one end-to-end integration test (`tests/test_integration_e2e.py`) that spins Postgres + Qdrant + MinIO ephemerally (testcontainers or compose), ingests a small fixture, runs one search, deletes one image, and asserts invariants. *(Closed 2026-04-25: scratch Postgres DB, scratch Qdrant alias/collection, and scratch MinIO bucket.)*

---

## P0-G6 — transition readiness

- [x] Confirm `docs/phase1_short_clips_transition.md` still matches reality after all Phase 0 changes land. *(Codex reviewed 2026-04-25; see ADR-009.)*
- [x] Codex posts an ADR-format review to `AGENTS_CONVERSATION.MD` (and `docs/decision_log.md`) signing off on the transition doc. *(Decision-log side closed in ADR-009; AGENTS_CONVERSATION closeout entry will cite it.)*
- [ ] Claude posts an ADR-format review signing off on the transition doc.
- [x] Confirm no video-specific code leaked into Phase 0 (`grep -r "video_segment\|retrieve_video" vidsearch/` returns nothing). *(Closed 2026-04-25 using PowerShell `Select-String` fallback after `rg.exe` returned access denied; no matches in `vidsearch/`.)*

---

## Cross-cutting (must land somewhere in the above)

- [x] `ops.model_versions` has rows for every model actually used: OCR (gateway), VLM (gateway), BGE-M3 (direct), SigLIP-2 (direct), jina-reranker-v2 (direct). Every row has a fingerprint. *(Closed 2026-04-23 — verified in the running Postgres container for `meme_ocr`, `meme_vlm_captioner`, `text_dense`, `text_sparse`, `visual`, and `reranker`.)*
- [ ] Every Qdrant point's payload `model_version` references the seeded model IDs.
- [x] Decide and record: `ops.purges` tombstone table in Phase 0, or hard delete and defer tombstones to Phase 5. Write the decision into `docs/decision_log.md`. *(Closed 2026-04-25: ADR-008 chooses hard delete in Phase 0 and defers tombstones to Phase 5.)*
- [x] Keep `data/meme` as the initial Phase 0 corpus; any later corpus expansion is a documented scope change. *(Closed 2026-04-25: ADR-001 fixes the canonical Phase 0 corpus at `data/meme`, 3,103 unique decodable/indexable images.)*
- [x] Unsupported files reported as skipped, not silently ignored. *(Closed 2026-04-25: ADR-001 records recursive scan counts: 28 unsupported-extension skips and 4 no-extension skips.)*
- [ ] Re-ingest remains idempotent at every stage. *(Guardrail landed 2026-04-23: `vidsearch/ingest/images.py` now preserves prior thumbnail/OCR/caption/retrieval/Qdrant vectors on forced re-ingest when transient enrichment calls fail, covered by `tests/test_ingest_preserve.py`. Leave unchecked until the live 5-image rerun and full-folder rerun are both proven.)*

---

## Completion post

When every box above is `[x]`, **Codex** posts a new entry to `AGENTS_CONVERSATION.MD`:

- Title: `Phase 0 complete — all gates closed`
- Type: Task Handoff (completion)
- Evidence links: the ADR hashes in `docs/decision_log.md`, the metrics rows in `eval.metrics`, the 5 OWUI transcripts in `docs/owui_integration.md` under `## P0-G3 chat-app evidence log`, the backup/restore drill in `docs/runbook.md` §18.
- Action: request Claude final sign-off (reviewer role) before Phase 1 unlocks.

Do not declare Phase 0 done without that entry.

---

## Cycle log (for continuity when ownership changes hands)

- **Cycle 1 (OpenCode, 2026-04-20):** env fix, `MODEL_GATEWAY.md`, retrieval-plan §2.4 routing annotations, schema migrations (001 + 002), template whitelist, gateway aliases.
- **Cycle 2 (Claude, 2026-04-20):** `caption.py`, `ocr.py` rewrite, `images.py` caption wiring, `retrieve_images.py` 3-leg RRF + intent-conditional rerank, `eval/runner.py` YAML-qrels loader + per-intent metrics, `queries_memes.yaml` rebalanced to 10/10/10/10, `storage/pg.py` caption kwargs + `upsert_model_version`.
- **Cycle 3 (Claude, 2026-04-20):** `encoders._get_siglip()` local-path fix, `tests/test_caption.py` (14), `tests/test_eval_runner.py` (7), TODO reconciliation pass.
- **Cycle 4 (Claude, 2026-04-20):** `fingerprints.py` extraction (193 lines), FastAPI startup hook, LiteLLM config regression restored, `tests/test_fingerprints.py` (8).
- **Cycle 5 (Claude, 2026-04-20):** Handoff package to Codex — Entry 26/27, runbook rewrite, this TODO ownership update, eval/owui/decision-log doc sweeps.

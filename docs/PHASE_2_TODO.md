# PHASE_2_TODO.md — Corpus scale, Lane B, caption backfill

**Version:** 2026-04-18
**Blocks on:** Phase 1 gates P1-G1…G6 closed.
**See:** `PHASE_2_PLAN.md` for rationale and gates.

---

## P2.0 — Pre-flight

- [ ] Phase 1 exit sign-off; re-run Phase 0 and Phase 1 eval and record baseline in `docs/decision_log.md`.
- [ ] Stage corpus: confirm `data/corpus/` or S3 bucket listing; record a pre-ingest row count.
- [ ] Confirm all Lane C API keys active; run `litellm --check` green.

## P2.1 — Bulk ingest (unlocks P2-G1)

- [ ] Parametrise `ingest_video` flow for corpus scan: `vidsearch/flows/ingest_corpus.py` enumerates the corpus and enqueues `ingest_video` runs.
- [ ] Concurrency limits: `gpu-asr=1`, `gpu-embed=2`, `cpu unlimited`, `io=16`; add `caption=8` for Phase 2.
- [ ] Resume-on-crash: confirm every `ops.ingest_steps` row with `state='running'` is reset to `pending` on worker startup.
- [ ] **TEST:** ingest of 100 real videos completes; median per-video wall clock captured.
- [ ] **TEST:** deliberate SIGKILL mid-run: worker restart resumes from the last `done` step without duplicates.
- [ ] **GATE:** 500+ videos indexed.

## P2.2 — Caption backfill scheduler (unlocks P2-G2)

- [ ] Redis key design: `caption:queue` ZSET, `quota:<provider>:<YYYY-MM-DD>` hash, `caption:in_flight:<segment>` set (with TTL) for exactly-once semantics.
- [ ] `vidsearch/flows/caption_backfill.py` — long-lived Prefect flow with `zpopmin` batch size 100.
- [ ] Provider picker with atomic INCR + compare-to-cap; advances on 429 or cap-hit.
- [ ] Midnight reset: TTL set to next UTC midnight; scheduler sleeps when every provider is exhausted.
- [ ] Reconciliation job (every 6 h): find segments with `caption_text IS NULL`; enqueue if not present in the set.
- [ ] **TEST:** unit — budget counter increment + TTL is atomic under concurrent Redis clients.
- [ ] **TEST:** integration — forced 429 from a mock Gemini response advances the chain to OpenRouter.
- [ ] **TEST:** integration — 10,000-segment synthetic run observes zero RPD violations against configured caps.
- [ ] **TEST:** integration — killing the worker mid-run does not duplicate captions (in-flight set + idempotent upsert).
- [ ] **GATE:** 24-hour real-corpus backfill run passes RPD caps.

## P2.3 — Captioning modules (unlocks P2-G2)

- [ ] `vidsearch/ingest/caption/hosted.py` — LiteLLM call with provider name propagated to Langfuse metadata.
- [ ] `vidsearch/ingest/caption/local.py` — only active if Lane B promoted; calls a vLLM-hosted Qwen3-VL.
- [ ] Caption text normalisation: strip provider-specific preambles, clamp to 512 tokens, tag language.
- [ ] Store `caption_text`, `caption_model`, `caption_prompt_ver` in `core.segments`.
- [ ] After caption write, enqueue `reindex-segment` task to update `text-dense` + `text-sparse` on that segment's point.
- [ ] **TEST:** unit — caption normaliser preserves language tags; rejects empty outputs.
- [ ] **TEST:** integration — captioning a known segment lands text in Postgres and updates the Qdrant payload (dense+sparse re-embedded).

## P2.4 — Lane B validation harness (unlocks P2-G3)

- [ ] Freeze validation set: `eval/lane_b/200_keyframes.jsonl` (uniformly sampled across the corpus) + `eval/lane_b/50_clips.jsonl` (10–30 s each). Cache Gemini 2.5 Flash baseline captions for each.
- [ ] `vidsearch/validation/lane_b/g1_load.py` — cold-start timer; OOM detection.
- [ ] `vidsearch/validation/lane_b/g2_vram.py` — 50 keyframe sustained run; `nvidia-smi` sampling; peak ≤ 14.5 GB.
- [ ] `vidsearch/validation/lane_b/g3_quality.py` — 200-keyframe captioning + Gemini 2.5 Pro judge; relevance non-inferior on ≥ 70%; hallucination ≤ 10%.
- [ ] `vidsearch/validation/lane_b/g4_throughput.py` — 50-clip batch for 30 min; ≥ 5 captions/min sustained.
- [ ] `vidsearch/validation/lane_b/g5_stability.py` — 10,000-caption loop; zero OOM / illegal memory / >5-min hangs.
- [ ] Results writer: per-candidate JSON + markdown summary in `docs/lane_b_validation.md`.
- [ ] Candidate 1: Qwen3-VL-30B-A3B UD-IQ2_XXS + mmproj-BF16.
- [ ] Candidate 2: Qwen3-VL-8B-Instruct (AWQ-4bit on vLLM).
- [ ] Candidate 3: MiniCPM-V 4.5 int4.
- [ ] **GATE:** at least one candidate has a full G1–G5 result set written. Promotion is optional; the verdict is mandatory.

## P2.5 — Lane B promotion (conditional)

- [ ] If any candidate passes all five gates, write a row in `ops.model_versions` with `family='lane-b-captioner'`.
- [ ] Add the model to the `vertical_caption` chain in `infra/litellm/config.yaml`.
- [ ] Register a Prometheus / Langfuse alert: if Lane B captioning latency exceeds 60 s P95, fall back to Lane C.
- [ ] **TEST:** integration — promoted Lane B model serves a caption through LiteLLM; Langfuse trace visible.

## P2.6 — Observability (unlocks P2-G4)

- [ ] Enable `docker-compose.observability.yml` with Langfuse web + worker + ClickHouse.
- [ ] LiteLLM `success_callback: ["langfuse"]`, `failure_callback: ["langfuse"]`.
- [ ] 30-day Langfuse retention policy.
- [ ] Grafana dashboards: per-provider RPD consumption, GPU utilisation, ingest throughput, caption queue depth.
- [ ] **TEST:** one caption call appears in Langfuse with provider, model, cost, latency.
- [ ] **TEST:** ClickHouse disk usage alarm fires before 80% full.

## P2.7 — Perceptual-hash dedupe (image corpus)

- [ ] Compute pHash during `ingest_image` step (Phase 2 adds this to Phase 0 module).
- [ ] Store cluster id in `core.images.metadata.near_dup_cluster`.
- [ ] Expose a `?exclude_near_dup=true` query option on `/search`.
- [ ] **TEST:** integration — 10 near-duplicate memes resolve to one cluster id; `/search` with the flag returns one representative.
- [ ] **NOTE:** Qdrant points are **not** collapsed; pHash is a retrieval-time option, not an ingest-time loss.

## P2.8 — Reindex drill (unlocks P2-G5)

- [ ] Write `vidsearch/flows/reindex.py` exercising the alias-cutover path.
- [ ] Bump `bge-m3` row in `ops.model_versions` with a fake rev bump to trigger the flow.
- [ ] Create `video_segments_v2` collection, populate in parallel with `v1`, atomic-swap alias.
- [ ] Keep `v1` for 48 h; drop after a manual sign-off.
- [ ] **TEST:** integration — during the cutover window, `/search` still serves; zero failed queries.
- [ ] **TEST:** integration — rollback (swap alias back to `v1`) works from the runbook.

## P2.9 — Regression + caption-lift eval

- [ ] Rerun Phase 0 meme eval (50 queries) post-Phase-2; regression gate at 3pp.
- [ ] Rerun Phase 1 video eval (20 queries) post-Phase-2; regression gate at 3pp.
- [ ] Design 30-query Phase 2 "caption lift" eval (`vidsearch/eval/queries_caption_lift.yaml`) focused on `visual` and `compositional` intents.
- [ ] Run A/B: retrieval with captions present vs absent on the caption-lift set.
- [ ] **GATE:** document the caption lift delta in `docs/decision_log.md`. Captions are kept even if lift is small, because they help synthesis grounding — but we record the number.

## P2.10 — Documentation (cross-gate)

- [ ] `docs/caption_backfill_runbook.md` — operator walkthrough (queue state, provider rotation, midnight reset).
- [ ] `docs/reindex_runbook.md` — alias-cutover walkthrough.
- [ ] `docs/lane_b_validation.md` — candidate results table.
- [ ] Update `docs/runbook.md` main page to cross-link to new runbooks.

---

## Cross-cutting rules

- [ ] Every Phase 2 PR runs the Phase 0 and Phase 1 eval suites.
- [ ] Lane B validation must complete before any Lane B model is routed in production.
- [ ] No reranker A/B or CI regression gating (Phase 3 territory) merged during Phase 2.

## Exit checklist (mirrors `PHASE_2_PLAN.md` §8)

- [ ] Bulk ingest: ≥ 500 videos.
- [ ] Caption coverage: ≥ 80% of segments (captioned or justified skip).
- [ ] Lane B verdict written.
- [ ] RPD adherence: zero violations in 24-hour run.
- [ ] Langfuse traces captured.
- [ ] Reindex drill executed.
- [ ] No regression on Phase 0 or Phase 1 eval suites.

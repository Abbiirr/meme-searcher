# PHASE_4_TODO.md — Graph, VSS, CCTV

**Version:** 2026-04-18
**Blocks on:** Phase 3 gates P3-G1…G6 closed.
**See:** `PHASE_4_PLAN.md` for rationale, entry bars, and time boxes.

---

## P4.0 — Pre-flight

- [ ] Phase 3 exit sign-off.
- [ ] Snapshot Phase 3 baselines into `docs/decision_log.md` (used as the regression floor for all Phase 4 work).
- [ ] Agree the 2-week time box with the user for each spike; record start dates.

## P4.1 — Graph spike (≤ 2 weeks; unlocks P4-G1)

- [ ] Enable Apache AGE: `infra/postgres/003_age.sql` (AGE extension + `SELECT create_graph('video_graph')`).
- [ ] `vidsearch/flows/graph_extract.py` — LightRAG-style entity + relation extraction over `core.segments`.
- [ ] Candidate extraction prompt committed to `vidsearch/prompts/graph_extract/v1.md`.
- [ ] `vidsearch/query/graph.py` — look up query entities, expand to 1–2-hop connected segments, feed as fourth Qdrant prefetch.
- [ ] Feature flag `VIDSEARCH_ENABLE_GRAPH`; default **off**.
- [ ] Build 40-query entity-heavy evaluation sub-slice from Phase 3 queries.
- [ ] **TEST:** unit — prefetch correctness on a toy 3-entity graph.
- [ ] **TEST:** integration — graph extraction on 10 segments yields expected entity counts.
- [ ] **TEST:** A/B — `nDCG@10` delta on entity-heavy subset, with / without graph prefetch.
- [ ] **GATE:** verdict committed in `docs/graph_retrieval.md` + `docs/decision_log.md`.
- [ ] If pass: promote flag to on for entity-heavy intents only; otherwise shelve behind flag-off.

## P4.2 — VSS / LVS benchmark spike (≤ 2 weeks; unlocks P4-G2)

- [ ] Prepare `docker-compose.vss.yml` with the LVS 3.0 microservice image (optional profile).
- [ ] Point LVS at our LiteLLM endpoint as the OpenAI-compatible backend.
- [ ] Port CA-RAG synthesis prompts into `vidsearch/prompts/ca_rag_port/v1.md`.
- [ ] Build a 10-long-video benchmark set (≥ 30 min each) with at least 5 "long summarisation" queries each.
- [ ] Run both pipelines on the benchmark set; capture `answer-faithfulness`, latency, cost.
- [ ] **GATE:** verdict committed in `docs/vss_benchmark.md`:
  - [ ] Prompt port promoted / archived.
  - [ ] Microservice adoption decision (always "don't adopt runtime; record the gap" unless the numbers are dramatic).

## P4.3 — CCTV / noise-tolerance spike (≤ 2 weeks; unlocks P4-G3)

- [ ] Collect a 50-video CCTV fixture set per §5.1 of the plan.
- [ ] Introduce `profile='cctv'` column on `core.videos` or in `metadata` JSONB.
- [ ] Adjust ingest: OCR confidence threshold 0.4 instead of 0.6 (for CCTV profile only); sparse-embed threshold 0.5.
- [ ] Promote visual retrieval weight in RRF when `has_ocr=false AND has_speech=false` on the segment.
- [ ] Build `vidsearch/eval/queries_cctv.yaml` with 20 graded queries.
- [ ] Baseline run (no enhancement): record `nDCG@10` per query.
- [ ] If baseline ≥ 10-of-20 rank-1 hits: stop there; skip enhancement.
- [ ] If baseline is weaker: try Real-ESRGAN x2 preprocessing behind `VIDSEARCH_ENHANCE_FRAMES=true` on a matched subset.
- [ ] **TEST:** integration — CCTV ingest completes within documented wall clock.
- [ ] **TEST:** regression — Phase 0–3 eval suites unaffected.
- [ ] **GATE:** verdict committed in `docs/cctv_profile.md`:
  - [ ] Baseline sufficient, enhancement unnecessary — OR
  - [ ] Enhancement flag merged as optional behind documented GPU cost.

## P4.4 — Frame enhancement wrapper (conditional)

- [ ] `vidsearch/ingest/video/enhance.py` — Real-ESRGAN x2 wrapper; idempotent; feature flag only.
- [ ] Output written to a sibling MinIO key (`enhanced/<sha256>.jpg`); original preserved.
- [ ] **TEST:** unit — enhancement deterministic for identical bytes + same model rev.
- [ ] **TEST:** integration — enhanced keyframe lifts OCR success on a known-hard fixture.

## P4.5 — Regression (cross-gate)

- [ ] Run Phase 0 meme eval; regression gate 3 pp.
- [ ] Run Phase 1 video eval; regression gate 3 pp.
- [ ] Run Phase 2 caption-lift eval; regression gate 3 pp.
- [ ] Run Phase 3 100-query video + meme eval; regression gate 3 pp.
- [ ] **GATE:** every prior phase still passes.

## P4.6 — Flag audit

- [ ] Every Phase 4 addition sits behind a feature flag; default configuration matches Phase 3 behaviour unless a spike earned promotion.
- [ ] Flags documented in `docs/feature_flags.md` with on/off consequences.

---

## Cross-cutting rules

- [ ] No hardening / multi-GPU / Helm work merged during Phase 4 (those are Phase 5).
- [ ] Every spike has a written verdict **or** a waiver entry extending its time box.
- [ ] No Phase 4 code regresses any previous phase's eval beyond 3 pp.

## Exit checklist (mirrors `PHASE_4_PLAN.md` §8)

- [ ] Graph verdict written.
- [ ] VSS / LVS verdict written.
- [ ] CCTV verdict written (baseline sufficient OR enhancement merged with documented cost).
- [ ] Phase 0–3 eval suites all pass.
- [ ] Every new code path is flag-gated with default off (unless explicitly promoted).

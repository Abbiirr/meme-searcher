# PHASE_4_PLAN.md — Graph booster, VSS benchmark, CCTV / noise-tolerance lane

**Version:** 2026-04-18
**Status:** Blocked on Phase 3 exit (gates P3-G1…G6 closed)
**Upstream:** `PHASE_3_PLAN.md`, `../ARCHITECTURE.md` §21, §25, `PHASE0_MEME_SEARCHER.md` §11 (CCTV strand)
**Downstream:** unlocks `PHASE_5_PLAN.md`

---

## 1. Purpose

Phase 4 is the **optional boosters and stretch domain** phase. It bundles three independent spikes that only earn a place if they move the needle on measurable quality:

1. **Graph booster.** Apache AGE + LightRAG entity extraction, graph-augmented retrieval as a third/fourth prefetch lane in Qdrant.
2. **NVIDIA VSS benchmark.** CA-RAG prompt scaffolds + LVS 3.0 microservice benchmarked against our own summarisation prompts.
3. **CCTV / noise-tolerance domain lane.** Stress-tests the retrieval spine on low-quality personal CCTV-style footage and noisy images, with carefully-chosen policy relaxations (lower OCR confidence, visual-only fallback, optional frame enhancement).

Each spike is **time-boxed**. If it does not beat its entry bar in its allotted window, it is shelved with a written verdict, not extended. Phase 4 is the phase where "no" is a valid and cheap outcome.

## 2. Scope

### In scope
- Apache AGE extension in Postgres.
- LightRAG-style entity and relation extraction over `asr_text`, `ocr_text`, `caption_text`.
- Graph-augmented retrieval mode: a third Qdrant prefetch that retrieves segments linked to entities surfaced by the query.
- NVIDIA VSS LVS 3.0 microservice stood up (local container or remote benchmark).
- CA-RAG synthesis prompt scaffolds ported into `vidsearch/prompts/` and A/B'd against the Phase 3 defaults.
- CCTV fixture corpus (small, e.g., 50 videos) with noise characteristics representative of personal CCTV (low light, compression artifacts, fixed camera angle, long idle periods).
- Relaxed OCR thresholds for the CCTV profile (separate collection payload flag, not a global knob change).
- Visual-only retrieval path validated for queries like "find the car that arrived on Tuesday night".
- Optional frame enhancement (e.g., Real-ESRGAN) as a preprocessing experiment, strictly behind a feature flag and only if unenhanced retrieval fails on the CCTV eval set.

### Explicitly out of scope
- Multi-GPU split (Phase 5).
- Helm / Kubernetes manifests (Phase 5).
- Production hardening, backups, chaos testing (Phase 5).

## 3. Spike 1 — Graph booster

### 3.1 Design
- Apache AGE enabled on Postgres. One graph per tenant (single tenant in this POC).
- LightRAG-style extraction pipeline `vidsearch/flows/graph_extract.py` reading `core.segments` and writing entity / relation rows to AGE.
- Graph-augmented retrieval: when a query mentions an entity that appears in the graph (matched via the existing BGE-M3 sparse index), expand the candidate pool with segments connected to that entity through 1–2 hops.
- Implemented as a **fourth Qdrant prefetch** (or a merge-after-RRF augmentation) gated behind a feature flag.

### 3.2 Entry bar
- Must improve `nDCG@10` on entity-heavy queries (a 40-query sub-slice of the Phase 3 eval set) by ≥ 5 percentage points without regressing any other intent class.
- Time box: **2 weeks of calendar time** from the first graph commit to the verdict.

### 3.3 Exit verdict
- Pass → keep as an optional flag in production; document in `docs/graph_retrieval.md`.
- Fail → shelve the code behind a `VIDSEARCH_ENABLE_GRAPH=false` flag (do not rip out); document the verdict in `docs/decision_log.md`.

## 4. Spike 2 — NVIDIA VSS / LVS benchmark

### 4.1 Design
- Stand up the LVS 3.0 microservice (container) pointed at our LiteLLM endpoint.
- Run long-video summarisation on a small benchmark set (10 long-form videos) through both:
  - Our existing `synthesize.py` (via LiteLLM `synthesis-long`).
  - LVS 3.0 microservice on the same inputs.
- Port CA-RAG synthesis prompt scaffolds into `vidsearch/prompts/ca_rag_port/` and A/B against Phase 3 prompts.

### 4.2 Entry bar
- Prompt port wins only if `answer-faithfulness` improves by ≥ 5 percentage points on long-video queries.
- Microservice benchmark is **informational** — we are not adopting LVS as runtime, only comparing.

### 4.3 Exit verdict
- Prompt port win → promote the prompt file; old prompt stays as a previous `prompt_ver` for rollback.
- Microservice benchmark win → `docs/decision_log.md` records the gap and the reason we are or are not adopting. **No automatic adoption.**

## 5. Spike 3 — CCTV / noise-tolerance lane

### 5.1 Design
- Collect a 50-video CCTV-style fixture set with the following properties:
  - 8–12 hour total duration, multiple cameras.
  - Low-light night footage with codec artifacts.
  - Fixed camera angle with long quiet periods.
  - Occasional text on timestamps and vehicle plates.
- Introduce a `profile='cctv'` tag on videos and a corresponding configuration:
  - OCR confidence threshold lowered from 0.6 to 0.4 **but the lower-confidence tokens still stay out of the BGE-M3 sparse embed text unless also above 0.5** (a soft gate).
  - Visual-only retrieval path promoted in RRF for queries with no OCR/ASR signal.
  - Optional frame enhancement (Real-ESRGAN x2) as a preprocessing step behind `VIDSEARCH_ENHANCE_FRAMES=true`.
- Build a 20-query CCTV eval set covering: visual scene recall, object-by-example (a car, a person, a package), temporal queries ("the day it rained", "the night someone walked past the gate").

### 5.2 Entry bar
- Baseline (no enhancement, default thresholds) already returns useful results on at least 10 of the 20 queries. If it already works, skip enhancement.
- Frame enhancement only wins if it improves `nDCG@10` on the CCTV eval by ≥ 5 percentage points relative to the baseline, measured on a matched pair of runs.

### 5.3 Exit verdict
- Baseline sufficient → `profile='cctv'` config is the only deliverable; enhancement experiment is documented as not needed.
- Enhancement wins → add Real-ESRGAN step behind the flag; document GPU cost and runtime impact.
- Both fail → the CCTV domain is **not** declared unsupported; we record the gap and continue to Phase 5. This matches the PHASE0 meme doc's stance that CCTV is a retrieval-quality problem first, not a super-resolution problem.

## 6. Test strategy

### 6.1 Unit
- Graph-augmented prefetch correctness on a toy graph (3 entities, 5 edges).
- OCR confidence gating behaves correctly under the CCTV profile.
- Frame enhancement wrapper is idempotent and deterministic.

### 6.2 Integration
- Graph extraction on a 10-segment fixture produces expected entity counts.
- LVS microservice starts, accepts a request, returns a summary.
- CCTV ingest completes the 50-video fixture set within a documented wall-clock budget.

### 6.3 End-to-end
- `/search` with graph flag on returns top-k with entity-expanded candidates; A/B data captured.
- CCTV eval set run under baseline + enhancement variants; numbers recorded.

### 6.4 Regression eval
- Phase 0, Phase 1, Phase 2, Phase 3 eval sets **all continue to pass** under the Phase 3 regression tolerance (>3 pp blocks merge without waiver).

## 7. Verification criteria

| Criterion | Target |
|---|---|
| Graph booster verdict | Written in `docs/graph_retrieval.md` (pass: promoted behind flag; fail: shelved with reasoning) |
| VSS / LVS benchmark verdict | Written in `docs/vss_benchmark.md` (adopt / don't adopt each component) |
| CCTV eval baseline | ≥ 10 of 20 queries return a useful rank-1 hit |
| Frame enhancement verdict | Written (necessary / unnecessary) |
| No regression | Phase 0–3 eval all within 3 pp of pre-Phase-4 baselines |
| Time discipline | Graph spike ≤ 2 weeks; VSS spike ≤ 2 weeks; CCTV spike ≤ 2 weeks (can parallelise) |

## 8. Closing gates

- **P4-G1 — Graph verdict.** `docs/graph_retrieval.md` committed; code behind flag or merged with flag-on for entity-heavy intents.
- **P4-G2 — VSS verdict.** `docs/vss_benchmark.md` committed; prompt ports either promoted or archived under `vidsearch/prompts/ca_rag_port/`.
- **P4-G3 — CCTV eval ran.** 20-query CCTV eval set scored under baseline and (if used) enhancement variants; verdict in `docs/cctv_profile.md`.
- **P4-G4 — No regressions.** Phase 0–3 eval suites pass on the Phase 4 build.
- **P4-G5 — All Phase 4 code is flag-gated.** Default configuration matches Phase 3 behaviour unless a spike explicitly earned promotion.

## 9. Dependencies

- Phase 3 closed.
- Apache AGE builds against the Postgres 17 image in the compose stack.
- LVS 3.0 container pull access (requires an NGC login for some NVIDIA images).
- Real-ESRGAN weights on disk (if enhancement is tested).
- CCTV fixture corpus collected.

## 10. Rollback / abort triggers

- Any spike consumes more than its 2-week box without a committed verdict → shelved automatically, no extension without a `docs/decision_log.md` entry signed off by at least one reviewer.
- Graph extraction bloats Postgres disk beyond 10% of ingest storage → time-box the nightly pruning job or shelve.
- CCTV ingest overwhelms GPU during normal operation → tag CCTV ingest as `profile='cctv'` with a lower concurrency class; never run CCTV ingest alongside caption backfill on the same card.

## 11. Deliverables

- `vidsearch/flows/graph_extract.py`.
- `infra/postgres/003_age.sql` (enable AGE; create the graph).
- `vidsearch/query/graph.py` (graph-augmented prefetch integration).
- `vidsearch/prompts/ca_rag_port/*` (versioned prompt files; promoted or archived).
- `docker-compose.vss.yml` (profile to start LVS microservice; optional).
- `vidsearch/ingest/video/enhance.py` (Real-ESRGAN wrapper; behind flag).
- `vidsearch/eval/queries_cctv.yaml` + graded labels.
- `docs/graph_retrieval.md`, `docs/vss_benchmark.md`, `docs/cctv_profile.md`.
- Updated `docs/decision_log.md` with all three verdicts.

## 12. Risk register

| Risk | Severity | Mitigation |
|---|---|---|
| LightRAG extraction is slow on the full corpus | High | Run on a Phase 2 sample; scale only if the booster beats the entry bar |
| Graph edges blow up Postgres size | Medium | Cap entities-per-segment; prune low-degree orphans nightly |
| LVS microservice requires a specific NIM stack | High | If too heavy, benchmark by pasting our inputs through LVS's hosted preview (if available) and port only the prompts |
| CCTV fixture set acquisition takes longer than spike | Medium | Start with a 10-video smoke set; promote to 50 only if smoke looks promising |
| Real-ESRGAN costs too much GPU time for marginal lift | Medium | Flag-gated; ship without enhancement if baseline is sufficient |
| Graph booster interacts poorly with `group_by=video_id` | Medium | Graph prefetch runs **before** grouping; unit test the order |

## 13. Exit sentence

Phase 4 is **done** when three written verdicts — graph, VSS, CCTV — sit in `docs/`, the Phase 3 `nDCG@10` target still holds on the Phase 4 build, and any code that was not promoted sits cleanly behind a disabled feature flag.

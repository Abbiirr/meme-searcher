# PHASE_3_TODO.md — Evaluation maturity, reranker A/B, CI regression gating

**Version:** 2026-04-18
**Blocks on:** Phase 2 gates P2-G1…G6 closed.
**See:** `PHASE_3_PLAN.md` for rationale and gates.

---

## P3.0 — Pre-flight

- [ ] Phase 2 exit sign-off.
- [ ] Freeze the current production config (`config_hash` snapshot) as the "pre-Phase 3 baseline" in `docs/decision_log.md`.
- [ ] Confirm Gemini 2.5 Pro judge quota plan (overnight batches are the default).

## P3.1 — Eval set growth (unlocks P3-G1)

- [ ] Add 80 new video queries across the five intent classes per `PHASE_3_PLAN.md` §4.1.
- [ ] Add 50 new meme queries (reach 100 total) balanced per §4.1.
- [ ] 20% non-English queries on both media.
- [ ] Hand-verify top-10 for 50 hardest queries; store as `judge='human'` records.
- [ ] **TEST:** eval loader rejects a malformed YAML entry.
- [ ] **GATE:** query counts + distribution committed; reviewer sign-off.

## P3.2 — Metrics and `config_hash` upgrade (unlocks P3-G1, P3-G5)

- [ ] Extend `eval.runs.metadata` JSONB to include: `reranker`, `prompt_ver`, `prefetch_sizes`, `group_size`, `rrf_k`, `collection_version`, `has_colbert_rescore`, `judge_model`.
- [ ] `vidsearch/eval/config_hash.py` — canonicalise and SHA-256 the metadata; store alongside every run.
- [ ] Add `cost_per_query`, `latency_p50`, `latency_p95` to the metric set; pull from Langfuse + FastAPI logs.
- [ ] **TEST:** unit — `config_hash` is deterministic for identical knobs and distinct for any change.
- [ ] **TEST:** integration — running the same eval twice yields identical `config_hash` and identical per-query scores (within a tolerance documented in code).

## P3.3 — Reranker A/B (unlocks P3-G2)

- [ ] `vidsearch/eval/rerank_ab.py` — runs every candidate on the 50-item sampled set + the full 100-item sets.
- [ ] Candidates:
  - [ ] `jina-reranker-v2-base-multilingual` (incumbent; local).
  - [ ] `BGE-reranker-v2-m3` (local; symmetric with embedder).
  - [ ] `Cohere Rerank` (hosted; benchmark-only; ≤ 30/day).
- [ ] Per-intent-class table output; latency and cost recorded per candidate.
- [ ] **TEST:** integration — A/B run produces a reproducible Markdown table artifact.
- [ ] **GATE:** winner promoted (or incumbent kept) with a `docs/decision_log.md` entry.

## P3.4 — Prompt versioning (unlocks P3-G1)

- [ ] `vidsearch/prompts/` directory with versioned files.
- [ ] `prompt_ver` is derived from the file content hash (not a manual bump).
- [ ] Tag `eval.runs.metadata.prompt_ver` and `core.segments.caption_prompt_ver` (for captions written post-promotion).
- [ ] **TEST:** unit — changing a prompt file yields a new `prompt_ver`; reverting restores the old one.

## P3.5 — RRF + group_by tuning (unlocks P3-G3)

- [ ] Grid sweep over `prefetch_sizes` (at least four combos), `group_size ∈ {1,2,3,5}`, `rrf_k ∈ {30,60,120}`.
- [ ] Report per-intent-class effects; pick a point on the Pareto front.
- [ ] **GATE:** commit new defaults in `vidsearch/config.py`; evidence in `docs/decision_log.md`.
- [ ] **TEST:** regression — new defaults do not regress any intent class by >2 pp vs the Phase 2 baseline.

## P3.6 — Unified alias decision (unlocks P3-G4)

- [ ] Build a mixed intent evaluation subset (queries that genuinely span media).
- [ ] Run Option A (split aliases, current) and Option B (`all_media` alias) on it.
- [ ] Decision per §7 of the plan; write it into `docs/decision_log.md`.
- [ ] If Option B chosen, implement `all_media` alias bootstrap in Qdrant and update `/search` routing.
- [ ] **TEST:** integration — chosen option retains Phase 0 and Phase 1 eval numbers within 1 pp.

## P3.7 — CI regression gate (unlocks P3-G5)

- [ ] Sampled eval subset (50 items total) committed to the repo.
- [ ] `vidsearch/eval/ci_gate.py` — run the sampled eval, load the last green `config_hash` results, diff, exit non-zero on >3 pp regression (aggregate or any intent class).
- [ ] CI workflow (GitHub Actions or equivalent) wired to run on PR open and merge.
- [ ] Waiver mechanism: a PR label plus a `docs/decision_log.md` entry allows merging past a regression.
- [ ] **TEST:** integration — a deliberately-broken PR (e.g., bypassing the reranker) is blocked by CI.
- [ ] **TEST:** integration — a healthy PR passes CI in < 15 minutes.

## P3.8 — ColBERT multivector rescore A/B (optional)

- [ ] `vidsearch/query/retrieve.py` gains a `colbert_rescore: bool` flag.
- [ ] A/B on the sampled set: `colbert_rescore=True` vs `False`.
- [ ] Keep if ≥ 2 pp `nDCG@10` improvement on a media-agnostic subset. Shelve otherwise.
- [ ] **GATE:** verdict in `docs/decision_log.md`.

## P3.9 — Non-English eval strand

- [ ] Queries in at least three non-English languages represented in the fixture corpus.
- [ ] Judge prompts explicitly language-agnostic (document the prompt variant).
- [ ] **TEST:** at least one non-English query per intent class returns a correct rank-1 hit.

## P3.10 — Documentation (cross-gate)

- [ ] `docs/eval_protocol.md` updated with Phase 3 query-growth procedure, judge calibration rules, CI gate rules, waiver process.
- [ ] `docs/decision_log.md` receives entries for: `nDCG@10` absolute target, reranker verdict, RRF tuning, alias decision, ColBERT verdict.

---

## Cross-cutting rules

- [ ] No graph retrieval merged during Phase 3.
- [ ] No CCTV-specific work merged during Phase 3.
- [ ] Every Phase 3 PR runs the sampled CI gate automatically.

## Exit checklist (mirrors `PHASE_3_PLAN.md` §9–§10)

- [ ] 100+ queries per media with labels.
- [ ] Human-calibrated subset ≥ 50 queries.
- [ ] Reranker A/B verdict written.
- [ ] RRF + group_by tuned and committed.
- [ ] Alias decision (split vs unified) committed.
- [ ] CI gate proven to block a regression.
- [ ] `nDCG@10` absolute target committed and met.

# PHASE_3_PLAN.md — Evaluation maturity, reranker A/B, CI regression gating

**Version:** 2026-04-18
**Status:** Blocked on Phase 2 exit (gates P2-G1…G6 closed)
**Upstream:** `PHASE_2_PLAN.md`, `../ARCHITECTURE.md` §17, §25
**Downstream:** unlocks `PHASE_4_PLAN.md`

---

## 1. Purpose

Phase 3 turns evaluation from a baseline into a production-grade quality signal. Three outcomes:

1. **100-query evaluation set** balanced across the five intent classes (lookup, semantic, visual, temporal, compositional).
2. **Reranker A/B complete:** `jina-reranker-v2-base-multilingual` vs `BGE-reranker-v2-m3` vs `Cohere Rerank` (benchmark-only). Winner promoted; loser documented.
3. **CI regression gate** wired to `eval.runs.config_hash`; a deliberately-broken commit is caught by the CI and blocked from merge.

Phase 3 also decides whether the `memes` and `video_segments` aliases should fuse into a unified `all_media` alias — the answer lives in the eval numbers, not in preference.

## 2. Scope

### In scope
- Eval set growth to 100 queries with graded relevance labels.
- LLM judge improvements: calibrated bias (human spot-check on the hardest 10 queries in each intent class).
- Reranker A/B harness in `vidsearch/eval/rerank_ab.py`.
- CI workflow (GitHub Actions or the local CI alternative already in use) that:
  - Runs the sampled eval on a frozen 50-item subset on every merge to `main`.
  - Blocks merge if `nDCG@10` regresses by more than 3 percentage points.
  - Allows a waiver path with a required `docs/decision_log.md` entry.
- RRF prefetch-size tuning + `group_size` tuning on the grown eval set.
- Decision on unified `all_media` alias vs media-split aliases.
- Optional: ColBERT multivector rescoring A/B — keep if it delivers ≥ 2 pp `nDCG@10` on a media-agnostic subset, shelve otherwise.
- Prompt versioning for synthesis: every synthesis prompt rev gets a `prompt_ver` tag in `core.segments.caption_prompt_ver` and in `eval.runs.metadata`.

### Explicitly out of scope
- Graph retrieval (Phase 4).
- CCTV / noise lane (Phase 4).
- Second GPU split (Phase 5).
- Production hardening (Phase 5).

## 3. Architecture delta from Phase 2

### Added
- `vidsearch/eval/rerank_ab.py` — A/B harness with per-query cost and latency tracking.
- `vidsearch/eval/ci_gate.py` — CLI that runs the sampled eval, computes delta vs last green run, exits non-zero on regression.
- `.github/workflows/eval.yml` (or the equivalent) — runs `ci_gate.py` on PR open and on merge.
- Prompt registry `vidsearch/prompts/` with versioned files and `prompt_ver` metadata.
- `vidsearch/query/retrieve.py` gains a `ColBERTRescore` flag and a tunable `prefetch_sizes` config.

### Changed
- `eval.runs` gains a richer `metadata` JSONB column capturing every knob that influences the score (reranker name, prompt_ver, RRF prefetch sizes, group_size, Qdrant collection version).
- `config_hash` is computed over a canonicalised JSON of that metadata so regressions are always reproducible.

## 4. Evaluation design

### 4.1 Query growth plan
- Add 80 queries to the existing 20-query video set (from Phase 1) plus the 50-query meme set (from Phase 0).
- Totals targeted: ~100 video queries + ~100 meme queries; but the regression gate runs on a frozen **50-item subset** (20 video + 20 meme + 10 mixed) to keep CI cycles short.
- Intent class distribution per media:
  - Video: 25 lookup, 25 semantic, 25 visual, 15 temporal, 10 compositional.
  - Meme: 30 exact/OCR, 30 semantic, 20 visual, 20 mixed.

### 4.2 Labels
- Graded 0–3 per segment (or per image) with top-10 per query.
- Primary judge: Gemini 2.5 Pro via LiteLLM `judge` group.
- Calibration: human spot-check on the top-10 of 10 hardest queries per intent class (50 queries total hand-verified).
- Store both labels in `eval.qrels.judge` with `judge='llm'` and `judge='human'`; disagreement metrics computed per run.

### 4.3 Metrics
- `nDCG@10`, `nDCG@20`, `Recall@100`, `MRR` per query.
- `answer-faithfulness` — pairwise judge score on synthesis outputs vs cited segments.
- `latency_p50` / `latency_p95` captured from the FastAPI side.
- `cost_per_query` derived from Langfuse / LiteLLM telemetry.
- Aggregated per intent class.

### 4.4 Regression gate
- Sampled eval (50 items) runs on every PR.
- Compares `nDCG@10` of the PR build vs the last merged green run.
- A drop of > 3 percentage points on the aggregate or on any single intent class blocks merge.
- Waiver = an entry in `docs/decision_log.md` signed off by at least one reviewer.

## 5. Reranker A/B

### Candidates
- `jina-reranker-v2-base-multilingual` (incumbent, Phase 0 default).
- `BGE-reranker-v2-m3` (same model family as the embedder — tempting symmetry).
- `Cohere Rerank` (hosted benchmark lane only; 1,000-call/month cap; trial keys). Use strictly for benchmarking, never for production traffic, to stay inside the cap.

### Harness
- `vidsearch/eval/rerank_ab.py` runs the same candidate list through each reranker.
- Per-intent-class breakdown of `nDCG@10` gains / losses.
- Latency and cost captured for each.
- Tie-breaker: on <1pp differences, prefer the local (non-hosted) candidate.

### Promotion rule
- Clear winner on aggregate `nDCG@10` **and** no worse than -1 pp on any intent class → promote to production.
- Otherwise keep the incumbent. Record the A/B result in `docs/decision_log.md`.

## 6. RRF + group_by tuning

Tune three knobs on the frozen eval set:
- `prefetch_sizes` for dense / sparse / visual (currently 200/200/200). Try 100/300/200, 300/100/200, 200/200/100 as a first grid.
- `group_size` for `group_by=video_id` (currently 3). Try 1, 2, 3, 5.
- RRF constant `k` (default 60). Try 30, 60, 120.

Pick the combo with the highest aggregate `nDCG@10` under a hard constraint: no intent class regresses by more than 2 pp vs the Phase 2 baseline.

## 7. Unified `all_media` alias decision

Run a comparison:
- **Option A — split aliases (status quo):** `/search?media=image|video` targets one alias at a time; union at the API layer.
- **Option B — unified alias:** a single `all_media` alias with both collections merged (or a parent alias routing the query based on intent).

Decision criteria:
- Option B wins only if `nDCG@10` on mixed intent queries improves by ≥ 2 pp with no regression on single-media queries.
- Otherwise keep Option A. This is a pure eval-driven call.

## 8. Test strategy

### 8.1 Unit
- `config_hash` is deterministic for identical knob settings and distinct for any change.
- Metric computations match hand-worked fixtures for `nDCG`, `Recall`, `MRR`, `answer-faithfulness`.
- Regression diff math correct across intent classes.

### 8.2 Integration
- CI gate runs on the sampled eval in under 15 minutes.
- Deliberately-broken commit (e.g., bypassing the reranker) is caught by the CI on a dry-run PR.
- Reranker A/B harness produces per-intent-class tables reproducibly.

### 8.3 End-to-end
- Full 100-query run completes on a weekly schedule; results stored in `eval.metrics`.
- Dashboard in Grafana or Langfuse shows the running metric trend.

## 9. Verification criteria

| Criterion | Target |
|---|---|
| Eval set size | ≥ 100 video queries + ≥ 100 meme queries, balanced across intent classes |
| Human-calibrated subset | ≥ 50 queries with `judge='human'` records |
| Reranker A/B result | Written verdict per candidate in `docs/decision_log.md` |
| CI regression gate | Proven to block a deliberately-broken commit |
| `nDCG@10` target | Project-chosen absolute; agreed by reviewers; committed in `docs/decision_log.md` |
| `answer-faithfulness` | ≥ 0.7 on the hand-curated set |
| Latency P95 | ≤ 20 s through OWUI (POC-level cap, not an SLO) |

## 10. Closing gates

- **P3-G1 — Eval grown.** 100+ queries per media with labels; distribution documented.
- **P3-G2 — Reranker A/B decided.** Verdict + winner promoted (or incumbent kept).
- **P3-G3 — RRF + group_by tuned.** New defaults committed; evidence in `docs/decision_log.md`.
- **P3-G4 — Unified alias decided.** Either `all_media` rollout plan exists or split is confirmed.
- **P3-G5 — CI gate live.** `.github/workflows/eval.yml` (or equivalent) runs on every PR; has caught a deliberately-broken commit.
- **P3-G6 — `nDCG@10` target met.** Absolute target committed and hit on the current build.

## 11. Dependencies

- Phase 2 closed.
- Langfuse functional for cost telemetry.
- Sufficient Gemini 2.5 Pro quota for judge calls (batch overnight if needed).

## 12. Rollback / abort triggers

- All three reranker candidates tie within 1 pp → keep the incumbent; re-architect only if quality is felt to be a blocker.
- CI gate false-positives block healthy merges → increase tolerance to 4 pp **only with a waiver entry**; do not silently relax.
- Unified `all_media` alias improves mixed queries but silently regresses single-media queries → revert immediately.

## 13. Deliverables

- `vidsearch/eval/queries_videos.yaml` and `queries_memes.yaml` expanded to 100+ each.
- `vidsearch/eval/rerank_ab.py`.
- `vidsearch/eval/ci_gate.py` + CI workflow file.
- `vidsearch/prompts/` with versioned synthesis prompts.
- Updated `eval.runs.metadata` schema and `config_hash` logic.
- `docs/decision_log.md` entries: reranker verdict, RRF tuning, alias decision, `nDCG@10` target.

## 14. Risk register

| Risk | Severity | Mitigation |
|---|---|---|
| Gemini 2.5 Pro judge quota insufficient for 200 queries × 2 cycles | Medium | Overnight batching; cache judgements keyed by `(query_id, segment_id, judge_version)` |
| Cohere trial cap exhausted mid-A/B | Low | Sample at ≤ 30/day; do not run Cohere in CI |
| CI gate makes every PR slow | Medium | Sampled (50-item) run in < 15 min; full runs weekly or on-demand |
| Eval set bias toward English queries | Medium | Phase 3 exit explicitly includes 20% non-English in both meme and video sets |
| Prompt versioning drift — two PRs use the same `prompt_ver` for different prompts | Low | Prompt files hashed; `prompt_ver` auto-derived from content hash |

## 15. Exit sentence

Phase 3 is **done** when a natural-language query at any media returns a grounded, ranked, cited answer whose retrieval quality is provably above a committed absolute target, a reranker A/B has a written verdict, and a deliberately-broken commit gets bounced by CI.

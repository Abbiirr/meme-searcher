# Eval Protocol

## Eval set

`vidsearch/eval/queries_memes.yaml` is the authoritative query set. Exactly 40 queries, 10 per intent class:

- 10 `exact_text` (UUID pattern `11111111-…`)
- 10 `fuzzy_text` (UUID pattern `22222222-…`)
- 10 `semantic_description` (UUID pattern `33333333-…`)
- 10 `mixed_visual_description` (UUID pattern `44444444-…`)

The 10/10/10/10 balance and 40-unique-query-IDs invariant are enforced by `tests/test_eval_runner.py::test_eval_yaml_has_10_per_intent` — do not drift.

All queries are written for images that actually exist in `data/meme`. Queries that cannot be matched to a real image at qrels-entry time must be rewritten, not left dangling.

## Query schema

```yaml
- query_id: 11111111-0000-0000-0000-000000000001
  intent: exact_text
  text: "one does not simply walk into mordor"
  target_image_id: <sha256-prefixed-uuid>   # singleton grade-3 target (optional)
  qrels:                                     # graded relevance list (optional)
    - image_id: <id>
      grade: 3
    - image_id: <id>
      grade: 1
```

The runner's `_qrels_from_yaml(q)` merges `target_image_id` (implicitly grade 3) with the explicit `qrels` list; on duplicate image IDs the higher grade wins.

## Labeling

Graded relevance 0–3:

- **0** — not relevant
- **1** — tangentially related
- **2** — relevant but not the best match
- **3** — highly relevant / the correct meme

At least the top 10 candidates per query must be judged after a first retrieval pass (pool-at-10).

Every query must have ≥ 1 target at grade 3 before P0-G4 can run. This is blocker F in `PHASE_0_REMAINING_TODO.md` and is tracked as a human data-entry pass after small-ingest surfaces concrete `image_id`s.

## Qrels sources (precedence)

1. **YAML** (`vidsearch/eval/queries_memes.yaml`) — primary source, loaded by `_qrels_from_yaml()`.
2. **Postgres** (`eval.qrels` table) — secondary source, loaded by `_qrels_from_db(cur, query_id)` which groups by `image_id` and takes `MAX(grade)`.

On conflict the higher grade wins. DB-side qrels exist so label fixes can be made in-place against a running system without editing YAML.

## Metrics

Each metric is emitted twice per run: once aggregate across all 40 queries, and once per-intent with a `__<intent>` suffix.

Aggregate:

- `nDCG@10`
- `Recall@10`
- `Recall@50`
- `MRR`
- `top_1_hit_rate`
- `reranker_uplift_ndcg10`

Per-intent (example): `Recall@10__exact_text`, `nDCG@10__fuzzy_text`, `top_1_hit_rate__semantic_description`, `MRR__mixed_visual_description`, and so on for all six × four = 24 per-intent rows.

Per-intent breakdown is the primary tuning surface — aggregate numbers hide class-specific regressions. When threshold tuning (retrieval-plan §10), watch the per-intent column first.

## Rerank top-K is intent-conditional

Defined in `vidsearch/query/retrieve_images.py::_RERANK_TOP_K_BY_INTENT`:

| Intent | Rerank top-K |
|---|---|
| `exact_text` | 30 |
| `fuzzy_text` | 40 |
| `semantic_description` | 50 |
| `mixed_visual_description` | 50 |

Rationale: `exact_text` queries resolve quickly off strong OCR matches; deeper reranks mostly burn latency. Visual and semantic queries benefit from a wider candidate pool.

## Acceptance thresholds (P0-G4)

All four must pass on the same run:

- `Recall@10 >= 0.90`
- `top_1_hit_rate >= 0.70`
- `reranker_uplift_ndcg10 >= 0.02`
- No `exact_text` query misses the grade-3 target outside top 10

If any threshold misses, tune per `docs/PHASE_0_RETRIEVAL_PLAN.md` §10 knobs (leg weights, RRF k, reranker template, OCR confidence cutoff) and rerun. Do not mark G4 closed on an under-threshold run.

## Running

```bash
python -m vidsearch.eval.runner --queries vidsearch/eval/queries_memes.yaml --limit 10
```

Each run writes:

- One row to `eval.runs` (with `config_hash` capturing leg weights, reranker alias, fingerprints).
- One row per (query, image) pair to `eval.run_results`.
- One row per metric to `eval.metrics` (aggregate + per-intent).

Record the best config's metrics + `config_hash` as an ADR in `docs/decision_log.md` once P0-G4 passes.

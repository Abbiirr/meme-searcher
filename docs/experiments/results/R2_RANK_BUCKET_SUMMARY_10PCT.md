# R2 Rank Bucket Summary

Total rows: `157`
Found rows: `0`
Missing rows: `157`

| Bucket | Count |
| --- | ---: |
| `target_at_rank_1` | `109` |
| `target_in_top_100_not_20` | `4` |
| `target_in_top_10_not_1` | `40` |
| `target_in_top_20_not_10` | `3` |
| `target_not_in_top_100` | `1` |

Eligibility:

- `eligible_for_ranker_training`: `False`
- `eligible_rank_11_to_20`: `3`
- `eligible_rank_2_to_10`: `40`
- `exact_text_eligible`: `0`
- `fuzzy_text_eligible`: `0`
- `stop_reasons`: `['target_in_top_10_not_1 40 < 50', 'target_in_top_20_not_10 3 < 100', 'exact_text eligible judgments 0 < 50', 'fuzzy_text eligible judgments 0 < 50']`

## 10 Percent R2 Replay Diagnostic

Source artifacts are ignored under `artifacts/`; this section summarizes `artifacts/feedback_targets/r2_train_results_top100_10pct.jsonl` and `artifacts/feedback_targets/r2_rank_buckets_10pct.json`.

| Metric | Value |
| --- | ---: |
| Prompt rows replayed | `157` |
| Target found in top 100 | `156` |
| Target not found in top 100 | `1` |
| Target pickup@100 | `0.9936` |
| Target at rank 1 | `109` |
| Target rank 2-10 | `40` |
| Target rank 11-20 | `3` |
| Target rank 21-100 | `4` |
| Median found rank | `1` |
| Max found rank | `62` |

Prompt category mix in this 10% prefix sample: `topic=70`, `emotion=18`, `exact_memory=31`, `short_sloppy=7`, `named_entity=11`, `multilingual=5`, `paraphrase=15`.

### Interpretation

This is the right direction for the project. The 10% replay shows the corrected R2 split is doing what R1 did not: it separates retrieval pickup from ranking eligibility. Candidate generation is strong on this sample (`156/157` targets present in top 100), so the remaining useful learning signal is mostly ranking repair, not broad retrieval failure repair.

The sample is not yet sufficient for training promotion. It has only `40` rank-2-to-10 cases and `3` rank-11-to-20 cases, below the R2 eligibility floors (`50` and `100`). It is also not category-balanced because this is a prefix sample from the prompt file, not a stratified sample. Treat this as a successful diagnostic replay, not a training-ready R2 result.

### Decision

Continue with R2, but do not train or enable a ranker from this 10% sample. The next scientifically correct step is either a stratified sample replay or the full replay after search latency is improved. The current result supports the R2 design principle: `target_not_found` is rare here and should remain retrieval-repair-only, while `target_found_but_low_rank` is the real LTR training pool.

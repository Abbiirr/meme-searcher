# R2 RLAIF-MemeRank Results Template

Experiment ID: `rlaif-memerank-r2`

## Summary

- Serving decision:
- Promotion ready:
- Main failure/pass reason:

## Prompt Balance

| Category | Count | Required | Pass |
| --- | ---: | ---: | --- |
| `exact_text` | | 200 | |
| `fuzzy_text` | | 200 | |
| `semantic_description` | | 200 | |
| `mixed_visual_description` | | 200 | |

## Judge Validation

| Metric | Value | Gate | Pass |
| --- | ---: | ---: | --- |
| AI-human agreement | | `>= 0.85` | |
| False-positive target-found rate | | `<= 0.03` | |
| Position consistency | | `>= 0.95` | |
| Uncertain rate | | `<= 0.15` | |

## Rank Buckets

| Bucket | Count | Use |
| --- | ---: | --- |
| `target_at_rank_1` | | Down-weighted stability |
| `target_in_top_10_not_1` | | Strong LTR |
| `target_in_top_20_not_10` | | Medium LTR |
| `target_in_top_100_not_20` | | Retrieval repair |
| `target_not_in_top_100` | | Retrieval repair only |
| `prompt_bad` | | Exclude |
| `uncertain` | | Exclude |

## Full-Corpus Verification

| Metric | Base | Learned | Delta | Pass |
| --- | ---: | ---: | ---: | --- |
| `Recall@10` | | | | |
| `top_1_hit_rate` | | | | |
| `MRR` | | | | |
| `nDCG@10` | | | | |

## Decision

State explicitly whether the ranker remains offline-only or can proceed to shadow. Do not enable serving in this report.

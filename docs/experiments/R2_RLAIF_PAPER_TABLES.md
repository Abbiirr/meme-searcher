# R2 RLAIF Paper Tables

Populate these tables from committed summaries after the run. Do not paste raw JSONL artifacts.

## Table 1: R1 vs R2 Design Correction

| Dimension | R1 | R2 |
| --- | --- | --- |
| Feedback source | AI/user target selections | AI prompt generation + AI judge + audit |
| Failure separation | Weak | Explicit retrieval vs ranking buckets |
| Training eligibility | Target-found successes | Target-present low-rank only |
| Promotion gate | Failed | Full-corpus and non-overlap no-regression |

## Table 2: R2 Outcome

| Model | Recall@10 | top_1_hit_rate | MRR | nDCG@10 | Promotion |
| --- | ---: | ---: | ---: | ---: | --- |
| Phase 0 | | | | | baseline |
| Pairwise logistic R2 | | | | | |
| LambdaMART R2 | | | | | |

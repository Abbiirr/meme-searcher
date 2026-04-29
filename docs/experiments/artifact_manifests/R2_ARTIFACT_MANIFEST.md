# R2 Artifact Manifest

Raw artifacts are intentionally stored under `artifacts/` and ignored by git. This manifest records expected paths and the committed summaries derived from them.

| Artifact | Purpose | Committed summary |
| --- | --- | --- |
| `artifacts/feedback_targets/r2_target_pack.jsonl` | R2 target pack | `docs/experiments/results/R2_TARGET_SPLIT_SUMMARY.md` |
| `artifacts/feedback_targets/r2_prompts_train.jsonl` | Generated prompts | `docs/experiments/results/R2_PROMPT_BALANCE_SUMMARY.md` |
| `artifacts/feedback_targets/r2_train_results_top100.jsonl` | Top-100 search replay | `docs/experiments/results/R2_RANK_BUCKET_SUMMARY.md` |
| `artifacts/feedback_targets/r2_ai_judgments_model_a.jsonl` | Judge labels | `docs/experiments/results/R2_JUDGE_AUDIT_SUMMARY.md` |
| `artifacts/feedback_eval/r2_pairwise_logistic_post_rlhf.json` | Post-RLAIF verification | `docs/experiments/results/R2_POST_RLAIF_VERIFICATION_SUMMARY.md` |

# R2 RLAIF-MemeRank Runbook

This runbook starts from local Phase 0 services and writes raw experiment artifacts under `artifacts/`, which is gitignored. Commit only markdown summaries under `docs/experiments/results/`.

## 1. Build Target Pack

```powershell
python -m vidsearch.feedback.target_benchmark build-target-pack `
  --folder data/meme_rlhf `
  --output artifacts/feedback_targets/r2_target_pack.jsonl
```

## 2. Build Leakage-Safe Splits

```powershell
python -m vidsearch.feedback.target_split build-splits `
  --pack artifacts/feedback_targets/r2_target_pack.jsonl `
  --output-dir artifacts/feedback_targets/r2_splits `
  --train-count 180 `
  --val-count 45 `
  --holdout-count 45 `
  --group-by target_id,template_family,near_duplicate_cluster,language
```

## 3. Generate Balanced Prompts

Use a prompt-generator family that is disjoint from the captioner/judge family.

```powershell
python -m vidsearch.feedback.target_benchmark generate-prompts-metadata-gateway `
  --pack artifacts/feedback_targets/r2_splits/train_pack.jsonl `
  --output artifacts/feedback_targets/r2_prompts_train.jsonl `
  --model fast `
  --gateway-url $env:LITELLM_URL `
  --prompts-per-image 8 `
  --batch-size 8 `
  --resume
```

Validate balance:

```powershell
python -m vidsearch.feedback.prompt_balance validate `
  --prompts artifacts/feedback_targets/r2_prompts_train.jsonl `
  --output docs/experiments/results/R2_PROMPT_BALANCE_SUMMARY.md `
  --min-exact 200 `
  --min-fuzzy 200 `
  --min-semantic 200 `
  --min-mixed 200
```

## 4. Replay Search Without Auto-Selection

```powershell
python -m vidsearch.feedback.target_benchmark run-target-searches `
  --pack artifacts/feedback_targets/r2_splits/train_pack.jsonl `
  --prompts artifacts/feedback_targets/r2_prompts_train.jsonl `
  --output artifacts/feedback_targets/r2_train_results_top100.jsonl `
  --misses-output artifacts/feedback_targets/r2_train_target_not_found.jsonl `
  --client-session-prefix rlaif-r2-search `
  --operator search-runner `
  --api-base-url http://127.0.0.1:18000 `
  --top-k 100 `
  --replace-prefix `
  --no-auto-select
```

The `--no-auto-select` flag is intentional: R2 first audits slates before creating training labels.

## 5. Judge, Consensus, and Buckets

```powershell
python -m vidsearch.feedback.ai_judge judge-target-slates `
  --results artifacts/feedback_targets/r2_train_results_top100.jsonl `
  --output artifacts/feedback_targets/r2_ai_judgments_model_a.jsonl `
  --judge-model deterministic-id-oracle `
  --shuffle-candidates `
  --repeat-permutations 2
```

```powershell
python -m vidsearch.feedback.consensus build `
  --judgments artifacts/feedback_targets/r2_ai_judgments_model_a.jsonl `
  --output artifacts/feedback_targets/r2_consensus_labels.jsonl
```

```powershell
python -m vidsearch.feedback.rank_bucket_report `
  --results artifacts/feedback_targets/r2_train_results_top100.jsonl `
  --judgments artifacts/feedback_targets/r2_consensus_labels.jsonl `
  --pack artifacts/feedback_targets/r2_splits/train_pack.jsonl `
  --output artifacts/feedback_targets/r2_rank_buckets.json `
  --summary docs/experiments/results/R2_RANK_BUCKET_SUMMARY.md
```

Stop before training if the bucket summary reports failed eligibility.

## 6. Train Offline Only

```powershell
python -m vidsearch.feedback.train_ranker `
  --output artifacts/feedback_rankers/r2_pairwise_logistic.json `
  --client-session-prefix rlaif-r2-train `
  --rank1-weight 0.05 `
  --approve-promotion `
  --p0-g4-passing
```

```powershell
python -m vidsearch.feedback.train_lambdamart `
  --client-session-prefix rlaif-r2-train `
  --output artifacts/feedback_rankers/r2_lambdamart.json
```

These commands do not enable serving.

## 7. Verify and Report

```powershell
python -m vidsearch.feedback.post_rlhf_verify `
  --artifact artifacts/feedback_rankers/r2_pairwise_logistic.json `
  --queries vidsearch/eval/queries_memes.yaml `
  --output artifacts/feedback_eval/r2_pairwise_logistic_post_rlhf.json `
  --limit 100
```

```powershell
python -m vidsearch.feedback.r2_report `
  --prompt-summary docs/experiments/results/R2_PROMPT_BALANCE_SUMMARY.md `
  --judge-summary docs/experiments/results/R2_JUDGE_AUDIT_SUMMARY.md `
  --bucket-summary docs/experiments/results/R2_RANK_BUCKET_SUMMARY.md `
  --post-verify artifacts/feedback_eval/r2_pairwise_logistic_post_rlhf.json `
  --output docs/experiments/results/R2_FINAL_REPORT.md
```

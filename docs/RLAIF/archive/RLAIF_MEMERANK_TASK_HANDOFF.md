# Task Handoff: Implement RLAIF-MemeRank R2

**Project:** `Abbiirr/meme-searcher`  
**Task type:** Research implementation + experiment instrumentation  
**Primary output:** A reproducible RLAIF experiment that follows the R2 plan and does not repeat the R1 failure.  
**Serving policy:** Do not enable a learned ranker unless every full-corpus and non-overlap promotion gate passes.

> Status note, 2026-04-29: this handoff is partially superseded. Use `docs/RLAIF/SELF_LEARNING_EXECUTION_PLAN.md` as the canonical implementation runbook. Keep this file as background for the earlier R2 ranker/judge scaffold.

---

## 0. Mission

Implement the R2 RLAIF-MemeRank experiment.

R1 showed that preference reranking alone can hurt full-corpus top-rank quality. R2 must use AI feedback more carefully: generate balanced prompts, judge retrieved slates with bias controls, separate retrieval failures from ranking failures, train only on eligible target-present rank buckets, and verify against full-corpus held-out metrics.

The goal is not to “make RLHF work” by forcing another ranker. The goal is to produce a scientifically valid self-improvement experiment that can support a paper.

---

## 1. Read first

Read in this order:

```text
docs/experiments/R1_FAILED_RLHF_EXPERIMENT.md
docs/RLHF_TRUE_TRAIN_TEST_PLAN.md
docs/RLHF_FEEDBACK_LOOP_PLAN.md
docs/AGENT_PROMPT_LABELING_INSTRUCTIONS.md
deep-research-report (14).md

vidsearch/feedback/target_benchmark.py
vidsearch/feedback/agent_operator.py
vidsearch/feedback/train_ranker.py
vidsearch/feedback/post_rlhf_verify.py
vidsearch/feedback/evaluate_ranker.py
vidsearch/feedback/ranker.py
vidsearch/feedback/service.py
vidsearch/query/retrieve_images.py
vidsearch/api/contracts.py
infra/postgres/003_feedback_loop.sql
```

Also review the user-provided RLAIF draft and preserve its core framing:

```text
RLAIF for supervision
learning-to-rank for production
AI judge validation before training
target_not_found excluded from ranker training
```

---

## 2. Non-negotiable rules

1. **Do not enable learned ranker serving.**
2. **Do not train from `target_not_found` rows.**
3. **Do not claim unbiased counterfactual evaluation before randomized exploration.**
4. **Do not split train/test by prompt row. Split by target/template family.**
5. **Do not let one model family generate prompts, judge slates, and adjudicate disagreements.**
6. **Do not expose original ranks/scores to the AI judge.**
7. **Do not claim AI labels are equivalent to human labels until judge validation passes.**
8. **Do not commit raw `artifacts/` JSONL files. Commit summarized reports.**
9. **Do not mutate OCR/captions/Qdrant from the RLAIF label path. Retrieval repair must be explicit and reviewed.**
10. **Stop the experiment if rank-bucket counts are insufficient.**

---

## 3. Deliverables

### 3.1 Documentation deliverables

Create:

```text
docs/experiments/R2_RLAIF_MEMERANK_PROTOCOL.md
docs/experiments/R2_RLAIF_RUNBOOK.md
docs/experiments/R2_RLAIF_RESULTS_TEMPLATE.md
```

Optional but recommended:

```text
docs/experiments/R2_RLAIF_JUDGE_AUDIT_TEMPLATE.md
docs/experiments/R2_RLAIF_PAPER_TABLES.md
```

### 3.2 Code deliverables

Add:

```text
vidsearch/feedback/ai_judge.py
vidsearch/feedback/judge_prompts.py
vidsearch/feedback/consensus.py
vidsearch/feedback/rank_bucket_report.py
vidsearch/feedback/train_lambdamart.py
vidsearch/feedback/target_split.py
vidsearch/feedback/r2_report.py
```

Modify:

```text
vidsearch/feedback/target_benchmark.py
vidsearch/feedback/train_ranker.py
vidsearch/feedback/post_rlhf_verify.py
```

### 3.3 Test deliverables

Add:

```text
tests/test_ai_judge_schema.py
tests/test_judge_position_permutation.py
tests/test_consensus_rules.py
tests/test_rank_bucket_eligibility.py
tests/test_target_split_no_leakage.py
tests/test_train_excludes_target_not_found.py
tests/test_prompt_balance_validator.py
tests/test_lambdamart_training_contract.py
tests/test_r2_report_schema.py
```

### 3.4 Artifact summary deliverables

Do not commit raw `artifacts/`. Instead, generate committed summaries:

```text
docs/experiments/artifact_manifests/R2_ARTIFACT_MANIFEST.md
docs/experiments/results/R2_PROMPT_BALANCE_SUMMARY.md
docs/experiments/results/R2_JUDGE_AUDIT_SUMMARY.md
docs/experiments/results/R2_RANK_BUCKET_SUMMARY.md
docs/experiments/results/R2_POST_RLAIF_VERIFICATION_SUMMARY.md
```

---

## 4. Workstream W0 — Freeze R1 and set experiment ID

### Goal

Ensure R2 has a clean baseline and provenance.

### Tasks

- Confirm `docs/experiments/R1_FAILED_RLHF_EXPERIMENT.md` exists.
- Create `docs/experiments/R2_RLAIF_MEMERANK_PROTOCOL.md`.
- Define R2 experiment ID:

```text
rlaif-memerank-r2
```

- Define canonical prefixes:

```text
rlaif-r2-search
rlaif-r2-train
rlaif-r2-holdout
rlaif-r2-judge-a
rlaif-r2-judge-b
```

### Acceptance

- [ ] R1 is referenced as the negative baseline.
- [ ] R2 protocol states that `target_not_found` is excluded from ranker training.
- [ ] R2 protocol states no serving promotion without full-corpus no-regression gates.

---

## 5. Workstream W1 — Target split and holdout construction

### Goal

Prevent leakage across train/validation/holdout.

### Implement

Add:

```text
vidsearch/feedback/target_split.py
```

Required commands:

```powershell
python -m vidsearch.feedback.target_split build-splits `
  --pack artifacts/feedback_targets/target_pack.jsonl `
  --output-dir artifacts/feedback_targets/r2_splits `
  --train-count 180 `
  --val-count 45 `
  --holdout-count 45 `
  --group-by target_id,template_family,near_duplicate_cluster,language
```

Add or expose disjoint holdout command if not already complete:

```powershell
python -m vidsearch.feedback.target_benchmark build-disjoint-holdout-pack `
  --training-pack artifacts/feedback_targets/r2_splits/train_pack.jsonl `
  --output artifacts/feedback_targets/r2_splits/disjoint_holdout_pack.jsonl `
  --limit 100
```

### Required output

```text
artifacts/feedback_targets/r2_splits/train_pack.jsonl
artifacts/feedback_targets/r2_splits/val_pack.jsonl
artifacts/feedback_targets/r2_splits/holdout_pack.jsonl
artifacts/feedback_targets/r2_splits/disjoint_holdout_pack.jsonl
docs/experiments/results/R2_TARGET_SPLIT_SUMMARY.md
```

### Tests

```text
tests/test_target_split_no_leakage.py
```

### Acceptance

- [ ] All prompts for a target stay in the same split.
- [ ] Near-duplicate/template clusters do not cross splits when cluster metadata exists.
- [ ] Disjoint holdout excludes training target IDs.
- [ ] Summary includes counts by intent/language/template family.

---

## 6. Workstream W2 — Balanced prompt generation

### Goal

Generate enough prompts per intent to avoid R1 imbalance.

### Implement

Extend prompt generation and add validator:

```text
vidsearch/feedback/target_benchmark.py
vidsearch/feedback/prompt_balance.py
```

Prompt schema:

```json
{
  "record_type": "target_prompt_label_v2",
  "target_id": "target-...",
  "prompt_id": "target-...:p03",
  "prompt": "find me that meme about not having friends just people I know",
  "category": "exact_text|fuzzy_text|semantic_description|mixed_visual_description|short_sloppy|multilingual",
  "language": "en|bn|mixed|unknown",
  "uses_visible_text": true,
  "expected_difficulty": "easy|medium|hard",
  "operator_model": "model-family-hidden-id",
  "operator_role": "prompt_generator",
  "source_modality": "image|metadata|image_plus_metadata",
  "rationale": "short explanation"
}
```

Run:

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

Validate:

```powershell
python -m vidsearch.feedback.prompt_balance validate `
  --prompts artifacts/feedback_targets/r2_prompts_train.jsonl `
  --output docs/experiments/results/R2_PROMPT_BALANCE_SUMMARY.md `
  --min-exact 200 `
  --min-fuzzy 200 `
  --min-semantic 200 `
  --min-mixed 200
```

### Tests

```text
tests/test_prompt_balance_validator.py
```

### Acceptance

- [ ] Prompt file meets per-intent minimums.
- [ ] Prompt file includes language distribution.
- [ ] Prompt file contains no filename/path/hash/image_id leakage.
- [ ] Prompt file contains realistic user-like queries.

---

## 7. Workstream W3 — Search replay top100

### Goal

Run baseline retrieval over full corpus and log slates without target leakage.

Run:

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
  --replace-prefix
```

### Acceptance

- [ ] `limit=100` is accepted by API.
- [ ] `retrieve_images()` respects `rerank_cap >= requested_limit`.
- [ ] Result file includes top100 slates.
- [ ] No target ID/image/path is passed to retrieval.
- [ ] Search sessions and impressions are logged.

---

## 8. Workstream W4 — AI judge implementation

### Goal

Judge target presence and near-duplicate cases without exposing rank or score.

### Add

```text
vidsearch/feedback/ai_judge.py
vidsearch/feedback/judge_prompts.py
```

Subcommands:

```text
judge-target-slates
validate-judgments
summarize-judge-bias
```

Judge input must include:

```text
target image
query prompt
randomized candidate thumbnails
candidate OCR/caption snippets
blind candidate IDs
```

Judge input must not include:

```text
rank
retrieval_score
rerank_score
learned_score
image_id
path/filename if answer-leaking
```

Run model A:

```powershell
python -m vidsearch.feedback.ai_judge judge-target-slates `
  --pack artifacts/feedback_targets/r2_splits/train_pack.jsonl `
  --results artifacts/feedback_targets/r2_train_results_top100.jsonl `
  --output artifacts/feedback_targets/r2_ai_judgments_model_a.jsonl `
  --judge-model <judge-model-a> `
  --shuffle-candidates `
  --repeat-permutations 2
```

Run model B:

```powershell
python -m vidsearch.feedback.ai_judge judge-target-slates `
  --pack artifacts/feedback_targets/r2_splits/train_pack.jsonl `
  --results artifacts/feedback_targets/r2_train_results_top100.jsonl `
  --output artifacts/feedback_targets/r2_ai_judgments_model_b.jsonl `
  --judge-model <judge-model-b> `
  --shuffle-candidates `
  --repeat-permutations 2
```

Judgment schema:

```json
{
  "record_type": "ai_target_judgment_v1",
  "prompt_id": "target-...:p03",
  "target_id": "target-...",
  "judge_model": "judge-family-hidden-id",
  "judge_role": "primary|secondary",
  "candidate_order_seed": 381927,
  "verdict": "exact_target_found|near_duplicate_found|semantically_relevant_but_not_target|not_found|prompt_bad|uncertain",
  "selected_candidate_index": 7,
  "selected_candidate_blind_id": "C07",
  "confidence": 0.82,
  "evidence": {
    "visual_match": 0.91,
    "ocr_match": 0.75,
    "semantic_match": 0.88,
    "template_match": 0.69
  },
  "short_reason": "..."
}
```

### Tests

```text
tests/test_ai_judge_schema.py
tests/test_judge_position_permutation.py
```

### Acceptance

- [ ] Judge output validates against schema.
- [ ] Same slate judged under two candidate permutations.
- [ ] Position consistency is computed.
- [ ] Low-confidence or inconsistent judgments are marked uncertain.
- [ ] Original ranks/scores are not present in judge prompt.

---

## 9. Workstream W5 — Consensus and human audit

### Goal

Convert AI judgments into conservative labels.

### Add

```text
vidsearch/feedback/consensus.py
```

Run:

```powershell
python -m vidsearch.feedback.consensus build `
  --judgments artifacts/feedback_targets/r2_ai_judgments_model_a.jsonl `
  --judgments artifacts/feedback_targets/r2_ai_judgments_model_b.jsonl `
  --output artifacts/feedback_targets/r2_consensus_labels.jsonl
```

Consensus rules:

```text
Accept target found:
  deterministic ID match
  OR two independent AI judges agree on same candidate
  OR one AI judge + human reviewer agree

Accept target not found:
  target ID absent from top100
  AND AI judge says no exact/near duplicate
  AND no high-confidence duplicate-family match

Mark uncertain:
  any judge disagreement
  any confidence < 0.70
  any duplicate ambiguity
```

Human audit:

```powershell
python -m vidsearch.feedback.consensus sample-human-audit `
  --labels artifacts/feedback_targets/r2_consensus_labels.jsonl `
  --output artifacts/feedback_targets/r2_human_audit_sample.jsonl `
  --per-intent 50
```

Summary:

```powershell
python -m vidsearch.feedback.consensus summarize-audit `
  --labels artifacts/feedback_targets/r2_consensus_labels.jsonl `
  --human-labels artifacts/feedback_targets/r2_human_audit_labels.jsonl `
  --output docs/experiments/results/R2_JUDGE_AUDIT_SUMMARY.md
```

### Tests

```text
tests/test_consensus_rules.py
```

### Acceptance

- [ ] Consensus labels generated.
- [ ] Human audit sample generated.
- [ ] AI-human agreement, false-positive rate, position consistency, and uncertain rate reported.
- [ ] If validation thresholds fail, labels are diagnostic only.

Use thresholds:

```text
AI-human agreement >= 0.85
false positive target-found rate <= 0.03
position consistency >= 0.95
uncertain rate <= 0.15
```

---

## 10. Workstream W6 — Rank buckets and eligibility

### Goal

Separate retrieval repair from ranker training.

### Add

```text
vidsearch/feedback/rank_bucket_report.py
```

Run:

```powershell
python -m vidsearch.feedback.rank_bucket_report `
  --results artifacts/feedback_targets/r2_train_results_top100.jsonl `
  --judgments artifacts/feedback_targets/r2_consensus_labels.jsonl `
  --pack artifacts/feedback_targets/r2_splits/train_pack.jsonl `
  --output artifacts/feedback_targets/r2_rank_buckets.json `
  --summary docs/experiments/results/R2_RANK_BUCKET_SUMMARY.md
```

Buckets:

```text
target_at_rank_1
target_in_top_10_not_1
target_in_top_20_not_10
target_in_top_100_not_20
target_not_in_top_100
prompt_bad
near_duplicate_confusion
uncertain
```

Stop if:

```text
target_in_top_10_not_1 < 50
target_in_top_20_not_10 < 100
exact_text eligible judgments < 50
fuzzy_text eligible judgments < 50
```

### Tests

```text
tests/test_rank_bucket_eligibility.py
```

### Acceptance

- [ ] Bucket report exists.
- [ ] Eligible examples are separated from retrieval failures.
- [ ] `target_not_found` rows are flagged as retrieval repair only.
- [ ] Stop conditions are enforced.

---

## 11. Workstream W7 — Apply eligible labels

### Goal

Record only training-eligible labels into feedback/judgment/pair tables.

Add or extend:

```text
vidsearch.feedback.target_benchmark apply-consensus-labels
```

Run:

```powershell
python -m vidsearch.feedback.target_benchmark apply-consensus-labels `
  --results artifacts/feedback_targets/r2_train_results_top100.jsonl `
  --labels artifacts/feedback_targets/r2_consensus_labels.jsonl `
  --client-session-prefix rlaif-r2-train `
  --eligible-buckets target_in_top_10_not_1,target_in_top_20_not_10 `
  --rank1-weight 0.05 `
  --replace-prefix
```

Rules:

```text
target_not_found -> no pairs
uncertain -> no pairs
prompt_bad -> no pairs
rank1 -> optional, very low weight, capped share
rank2-10 -> strong pairs
rank11-20 -> medium pairs
```

### Tests

```text
tests/test_train_excludes_target_not_found.py
```

### Acceptance

- [ ] No `target_not_found` rows enter `feedback.preference_pairs`.
- [ ] Pair weights match rank bucket policy.
- [ ] Pair count per target/template cluster is capped.
- [ ] Training rows are filterable by `client_session_id LIKE 'rlaif-r2-train%'`.

---

## 12. Workstream W8 — Train and verify models

### 12.1 Pairwise logistic v2

```powershell
python -m vidsearch.feedback.train_ranker `
  --output artifacts/feedback_rankers/r2_pairwise_logistic.json `
  --client-session-prefix rlaif-r2-train `
  --rank1-weight 0.05 `
  --approve-promotion `
  --p0-g4-passing
```

### 12.2 LambdaMART/XGBoost

Add:

```text
vidsearch/feedback/train_lambdamart.py
```

Run:

```powershell
python -m vidsearch.feedback.train_lambdamart `
  --client-session-prefix rlaif-r2-train `
  --output artifacts/feedback_rankers/r2_lambdamart.json `
  --objective rank:ndcg
```

### 12.3 Post-RLAIF verification

```powershell
python -m vidsearch.feedback.post_rlhf_verify `
  --artifact artifacts/feedback_rankers/r2_pairwise_logistic.json `
  --queries vidsearch/eval/queries_memes.yaml `
  --output artifacts/feedback_eval/r2_pairwise_logistic_post_rlhf.json `
  --limit 100
```

Repeat for:

```text
r2_lambdamart.json
```

### 12.4 Disjoint target holdout

Generate holdout prompts and replay:

```powershell
python -m vidsearch.feedback.target_benchmark run-target-searches `
  --pack artifacts/feedback_targets/r2_splits/disjoint_holdout_pack.jsonl `
  --prompts artifacts/feedback_targets/r2_holdout_prompts.jsonl `
  --output artifacts/feedback_targets/r2_holdout_results_baseline.jsonl `
  --client-session-prefix rlaif-r2-holdout `
  --api-base-url http://127.0.0.1:18000 `
  --top-k 100
```

### Tests

```text
tests/test_lambdamart_training_contract.py
tests/test_r2_report_schema.py
```

### Acceptance

- [ ] Logistic and LambdaMART artifacts produced.
- [ ] Reports include base vs learned metrics.
- [ ] Reports include overlap and without-overlap blocks.
- [ ] Reports include per-intent metrics.
- [ ] No ranker is serving-enabled unless gates pass.

---

## 13. Required reporting

Add:

```text
vidsearch/feedback/r2_report.py
```

Generate:

```powershell
python -m vidsearch.feedback.r2_report `
  --prompt-summary docs/experiments/results/R2_PROMPT_BALANCE_SUMMARY.md `
  --judge-summary docs/experiments/results/R2_JUDGE_AUDIT_SUMMARY.md `
  --bucket-summary docs/experiments/results/R2_RANK_BUCKET_SUMMARY.md `
  --post-verify artifacts/feedback_eval/r2_pairwise_logistic_post_rlhf.json `
  --output docs/experiments/results/R2_FINAL_REPORT.md
```

Report must include:

```text
prompt counts by intent/language
judge validation metrics
bucket counts
eligible training counts
pair counts
ranker metrics
full-corpus verification
disjoint holdout verification
failure examples
promotion decision
paper table rows
```

---

## 14. Promotion gates

A ranker can be considered for shadow only if:

```text
AI judge validation passed
target_not_found excluded from pairs
rank bucket counts sufficient
per-intent training floors met
pairwise/logistic or LambdaMART validation passed
full-corpus non-overlap metrics do not regress
top_1_hit_rate >= base
MRR >= base
nDCG@10 >= base
Recall@10 regression <= 1 percentage point
exact_text misses outside top10 = 0
latency p95 increase < 50 ms
blind changed-ranking review accepted
```

If any fail:

```text
ranker remains offline-only
R2 report records failure reason
do not enable VIDSEARCH_FEEDBACK_RANKER_ENABLED
```

---

## 15. Final acceptance checklist

R2 implementation is complete when:

```text
[ ] R2 protocol doc exists.
[ ] Target split prevents leakage.
[ ] Balanced prompt generation exists and passes floors.
[ ] Search replay top100 exists.
[ ] AI judge module exists.
[ ] Candidate randomization/permutation is implemented.
[ ] Consensus module exists.
[ ] Human audit workflow exists.
[ ] Rank-bucket report exists.
[ ] target_not_found rows are excluded from ranker training.
[ ] Pairwise logistic v2 trains from eligible labels only.
[ ] LambdaMART/XGBoost baseline trains from same eligible data.
[ ] Post-RLAIF verifier reports base vs learned metrics.
[ ] Disjoint holdout verifier exists.
[ ] R2 final report is committed as markdown.
[ ] No raw artifacts are committed.
[ ] Learned ranker remains disabled unless every promotion gate passes.
```

---

## 16. Pasteable builder prompt

```text
Implement RLAIF-MemeRank R2 for Abbiirr/meme-searcher.

Goal:
Build a rigorous RLAIF experiment after the failed R1 RLHF/LTR attempt. R1 showed that naive preference reranking preserved recall but worsened top_1_hit_rate and MRR. R2 must use AI feedback only after validating judges, separating retrieval failures from ranking failures, balancing prompt categories, and gating learned rankers against full-corpus held-out metrics.

Read first:
- docs/experiments/R1_FAILED_RLHF_EXPERIMENT.md
- docs/RLHF_TRUE_TRAIN_TEST_PLAN.md
- docs/RLHF_FEEDBACK_LOOP_PLAN.md
- docs/AGENT_PROMPT_LABELING_INSTRUCTIONS.md
- deep-research-report (14).md
- vidsearch/feedback/target_benchmark.py
- vidsearch/feedback/train_ranker.py
- vidsearch/feedback/post_rlhf_verify.py
- vidsearch/query/retrieve_images.py
- vidsearch/api/contracts.py
- infra/postgres/003_feedback_loop.sql

Implement:
- docs/experiments/R2_RLAIF_MEMERANK_PROTOCOL.md
- docs/experiments/R2_RLAIF_RUNBOOK.md
- vidsearch/feedback/target_split.py
- vidsearch/feedback/ai_judge.py
- vidsearch/feedback/judge_prompts.py
- vidsearch/feedback/consensus.py
- vidsearch/feedback/rank_bucket_report.py
- vidsearch/feedback/train_lambdamart.py
- vidsearch/feedback/r2_report.py

Critical rules:
- Do not enable serving ranker.
- Do not train from target_not_found.
- Do not expose ranks/scores/image_ids to AI judges.
- Randomize candidate order and require position consistency.
- Use at least two judge/model-family passes or human adjudication.
- Stop before training if rank-bucket counts are insufficient.
- Commit markdown summaries, not raw artifacts.
- Do not claim unbiased OPE without controlled exploration.

Tests:
- test_ai_judge_schema.py
- test_judge_position_permutation.py
- test_consensus_rules.py
- test_rank_bucket_eligibility.py
- test_target_split_no_leakage.py
- test_train_excludes_target_not_found.py
- test_prompt_balance_validator.py
- test_lambdamart_training_contract.py
- test_r2_report_schema.py

The most important invariant:
target_not_found rows must never enter feedback.preference_pairs.
```

---

## 17. Reference basis

This handoff is based on:
- R1 failed-RLHF experiment documentation
- corrected `RLHF_TRUE_TRAIN_TEST_PLAN.md`
- existing feedback/ranker implementation
- RLAIF and Constitutional AI literature
- LLM-as-judge and position-bias literature
- relevance feedback and learning-to-rank literature
- counterfactual/unbiased LTR literature
- DPO/KTO/ORPO post-training literature

# RLAIF-MemeRank Literature Review and Priority Revision

**Author:** Claude (reviewer/architect, per CLAUDE.md)
**Date:** 2026-04-29
**Status:** Draft for review by Codex, OpenCode, and User
**Companion documents:** `docs/RLAIF/SELF_LEARNING_CANONICAL_PLAN.md`, `docs/experiments/R1_FAILED_RLHF_EXPERIMENT.md`, `AGENTS_CONVERSATION.MD` Entries 91/96

> Status note, 2026-04-29: this is literature background and review evidence. Use `docs/RLAIF/SELF_LEARNING_CANONICAL_PLAN.md` as the single source of truth for implementation. Superseded RLAIF/R2 plan drafts live under `docs/RLAIF/archive/`.

---

## 0. Purpose

This document is a literature-grounded review of the R2 RLAIF-MemeRank plan and the post-R1 brainstorm captured in `AGENTS_CONVERSATION.MD`. It does three things:

1. Validates or revises the load-bearing claims behind the current R2 plan and the brainstorm recommendations against published evidence.
2. Surfaces two specific recommendations that the brainstorm under-weighted and that the literature supports more strongly than R2 currently reflects.
3. Proposes a revised priority order for the next implementation cycle, with each item annotated by the evidence basis.

This is not a paper draft. It is an internal research note intended to keep the next cycle's investments aligned with documented evidence rather than only with R1 retrospection.

---

## 1. R1 baseline and the size of the headroom

The empirical R1 result is the anchor for everything below.

| Metric | Phase 0 base | R1 learned ranker | Delta |
| --- | ---: | ---: | ---: |
| `Recall@10` | `0.95` | `0.95` | `0.00` |
| `top_1_hit_rate` | `0.925` | `0.875` | `-0.050` |
| `MRR` | `0.9333` | `0.9083` | `-0.025` |
| `nDCG@10` | `0.9375` | `0.9190` | `-0.018` |

Source: `artifacts/feedback_eval/post_rlhf_target_repaired.json`, `AGENTS_CONVERSATION.MD` Entry 95.

The structural implication: base retrieval already returns the correct image in top 10 for 95% of queries and at rank 1 for 92.5% of queries. The R1 ranker's job was to move some of the remaining 7.5% from rank 2-10 to rank 1 without disturbing the 92.5% that already worked. With 290 unique targets and 318 search judgments, R1 instead made the system worse on top-1 by 5pp.

Two literature-supported interpretations:

- The reranker layer is not the binding constraint when the base is this strong. The cited reranker-evaluation literature confirms that "application of a reranker does not bring a performance benefit at any depth compared to BM25, with continuous degradation as we rerank more documents" is a recognised failure mode, not unique to this project. See `[15]`.
- The remaining headroom lives in the failures the reranker cannot reach: cases where the target is outside top 10 (recall failures, fixable by retrieval/labeling) and near-duplicate confusion cases (fixable by richer per-image labels, not by reordering).

R2 currently invests almost all engineering capacity in the same reranker layer that R1 demonstrated is the wrong leverage point.

---

## 2. Validated claims (literature supports the brainstorm)

### 2.1 VLM-enriched captions on the index produce large retrieval gains

VeCLIP (`[8]`) reports the following Recall@1 gains on standard image-text retrieval after rewriting captions with a VLM (LLaVA) and fusing with original alt-text:

| Dataset | Scale | R@1 gain |
| --- | --- | ---: |
| COCO image-to-text | 3M | +16.84% |
| COCO image-to-text | 12M | +23.26% |
| COCO image-to-text | 100M | +17.58% |
| COCO image-to-text | 200M | +15.00% |
| Flickr30k image-to-text | 3M | +28.40% |
| Flickr30k image-to-text | 12M | +29.20% |

VeCLIP also reports competitive results with only 14% of CLIP's original training data, indicating that label quality, not label quantity, is the dominant driver.

Implication for this project: the same RLAIF judge infrastructure being built for preference-pair labeling could instead be re-pointed at index-time caption enrichment, where the documented gain is one to two orders of magnitude larger than what any reranker on top of a 92.5% top-1 base could realistically deliver.

### 2.2 Hybrid retrieval with reciprocal rank fusion is well-supported

Documented gains over single-retriever baselines are in the +15-30% recall range on aggregate benchmarks `[13][14]`:

- Hybrid `Recall@10`: `0.91`
- Dense-only `Recall@10`: `0.78`
- Sparse-only `Recall@10`: `0.65`

Reciprocal Rank Fusion (RRF) with `k=60` is the de-facto standard fusion method and has been shown to be more robust than score-normalised learned fusion.

Caveat: gains are domain-dependent. The same source reports `+26-31% NDCG` on BEIR aggregate but only `+1.7%` on the WANDS e-commerce benchmark. The meme corpus's gain must be measured, not assumed.

### 2.3 R2's permutation-self-consistency design is well-grounded

The permutation-self-consistency paper (NAACL 2024, `[5]`) reports `+7-18%` ranking quality gain on GPT-3.5 and `+8-16%` on LLaMA-2-70B from shuffling listwise inputs and aggregating. The systematic position-bias study (AACL 2025, `[6]`) confirms position bias is judge- and task-dependent and is "strongly affected by the quality gap between solutions" - i.e. judges are most biased exactly when items are similar.

The current `vidsearch/feedback/ai_judge.py` design (blind candidate IDs `C01..C100`, hidden rank/score/source_uri, `repeat_permutations=2`, consensus requiring agreement on the same blind ID) is consistent with this literature.

### 2.4 Reranker NDCG plateau and regression on a strong base are documented

Multiple recent surveys and empirical analyses confirm reranker degradation on strong bases is common, including agentic-reranker development logs that explicitly observe overfitting to validation NDCG with regression on test `[15][16]`. R1 was not anomalous; it was textbook.

### 2.5 Synthetic prompt generation is effective with very few real examples

Promptagator (`[3]`) demonstrates that with no more than 8 real examples per task, LLM-generated synthetic queries can outperform ColBERT v2 by `+1.2 nDCG` on the dual encoder, and a further `+5.0 nDCG` once a reranker is trained on the same generated data, across 11 retrieval sets.

InPars (`[4]`) shows that LLM-generated training data beats BM25 and self-supervised dense retrieval, and combined with supervised data yields better zero-shot transfer than supervised-only training.

Implication: R1 already had stronger supervision than Promptagator (deterministic target image IDs for ~290 targets). The R2 prompt-floor of 200 prompts per category times four categories times one captioner family is not justified by the literature; if anything, generating more prompts from a single VLM family amplifies the family-correlation problem the R2 plan correctly identifies as a risk.

---

## 3. Updates to the brainstorm: two recommendations strengthened

### 3.1 HyDE (Hypothetical Document Embeddings) deserves its own R2 track

The brainstorm mentioned query rewriting as a long-term ticket. The literature evidence is stronger than that framing.

HyDE (`[7]`) is purely zero-shot: an instruction-following LLM generates a hypothetical document for the user's query, that hypothetical document is encoded with an unsupervised encoder, and similarity search uses the hypothetical document's embedding rather than the raw query. The reported gains are:

- Significantly outperforms the state-of-the-art unsupervised dense retriever Contriever.
- Strong performance comparable to fine-tuned retrievers across web search, QA, and fact verification.
- Demonstrated effectiveness on low-resource languages including Swahili, Korean, and Japanese.

The dense bottleneck of the encoder filters out incorrect details from the generated hypothetical document, grounding the output to actual corpus content even when the LLM hallucinates.

Why this matters for the meme system specifically:

- The meme corpus has documented short-prompt and Bangla-prompt failure modes (Entry 88 lists `bangla_metadata_under_prompted: 7` in the original miss analysis).
- HyDE's mechanism converts a short or ambiguous user prompt into a richer pseudo-document before retrieval, attacking exactly that failure class.
- The local LiteLLM gateway already exposes the LLM call needed; the integration cost is small relative to the expected gain.
- Multilingual evidence in the original HyDE paper directly covers the Korean/Japanese case, which is the closest analog to Bangla in the available evidence base.

This should be added to R2 as a parallel track, not deferred.

### 3.2 If a learned reranker is required, change the model family

R1 trained a 14-feature pairwise logistic on tabular features. The literature evidence is that the modern path for LLM-supervised reranking is listwise generation, not feature-engineered pairwise classification.

RankGPT (`[2]`) reports that properly instructed LLMs deliver competitive or superior results to state-of-the-art supervised reranking on popular IR benchmarks, and that a distilled 440M-parameter model trained via permutation distillation outperforms 3B supervised models on BEIR.

This implies a fork in the road for the next cycle:

- If the goal is a serving reranker, the architecture should change from tabular pairwise logistic to listwise LLM reranker with permutation distillation, evaluated on the held-out target pack with the existing no-regression gates from `vidsearch/feedback/post_rlhf_verify.py`.
- If the goal is a research/paper artifact, the existing R2 LambdaMART/pairwise pipeline can continue, but with explicit framing that it is a methodology contribution (judge validation, family disjointness, no-leakage splits), not a serving improvement.

---

## 4. Softened claim: small-sample contrastive adapter is real but fragile

The brainstorm proposed fitting a small contrastive adapter (LoRA-style) on top of BGE-M3 using the 290 (prompt, target) pairs.

The CLIP-LoRA literature (`[11]`) confirms this is a documented technique with reported gains of `+5.79%` top-1 zero-shot accuracy with less than 0.3% additional parameters. However, the same literature explicitly warns: "in regimes with severe label scarcity or imbalance, naive LoRA may overfit and degrade performance relative to zero-shot. The gains from LoRA adaptation can depend critically on which layers and submodules are adapted, as well as choice of rank/scaling."

This is the same failure mode that hit the R1 ranker. The recommendation stands but with a stronger guard:

- Start with very low rank (4-8) and a small set of adapted layers.
- Evaluate against the held-out target pack every epoch, not only at the end.
- Use the existing `post_rlhf_verify.py` no-regression gates as the abort criterion.
- Treat the same way R1 should have been treated: an offline experiment whose default outcome is "do not promote".

The Sentence-Transformers documentation (`[10]`) confirms that for small datasets, loss-data alignment is more important than dataset size, and recommends `MultipleNegativesRankingLoss` for query-document pairs and `BatchSamplers.NO_DUPLICATES` for in-batch negative sampling - both directly applicable here.

---

## 5. Methodology updates the literature recommends for R2

These do not change the R2 architecture; they tighten the evaluation surface.

### 5.1 Replace Cohen's kappa with Gwet's AC2 + rank correlation in judge audits

The 2024 LLM-as-judge agreement literature (`[12]`) advises against relying solely on Krippendorff's alpha or Cohen's kappa in skewed distributions. The recommendation is Gwet's AC2 for agreement assessment plus rank correlation coefficients for ordering consistency.

The meme corpus has a heavily skewed label distribution: most queries return the target at rank 1, so any "found vs not-found" agreement metric will be inflated by the dominant class. Gwet's AC2 is the recommended fix.

Concrete change: extend `vidsearch/feedback/consensus.py:summarize_audit` to compute Gwet's AC2 in addition to whatever single-number agreement is currently reported.

### 5.2 Drop the per-category prompt floor of 200

The R2 plan currently requires 200 prompts per category times four categories. With 180 train targets, that is 4-5 prompts per category per target. Promptagator achieves competitive results with 8 real examples per task; R1 already had ~290 targets with deterministic supervision, which is stronger than Promptagator's setup.

The 200-floor is unsupported by the cited literature and risks pushing the team to generate enough synthetic variants that they correlate strongly with the captioner family - which is the family-correlation problem R2 correctly identifies elsewhere.

Concrete change: lower per-category floors to 50-75 in `prompt_balance.py` defaults, and treat them as soft targets in the runbook rather than hard gates that block training.

### 5.3 Do not trust AI judges for near-duplicate disambiguation

The position-bias literature (`[6]`) finds that LLM judge bias is "strongly affected by the quality gap between solutions" - i.e. judges are most biased when items are similar. Near-duplicate meme images are exactly this case.

Concrete change: in `vidsearch/feedback/consensus.py`, route consensus labels with `verdict in {near_duplicate_found}` to a separate "duplicate-family adjudication" track that requires either deterministic ID match or explicit human review, not multi-judge AI consensus.

### 5.4 Hybrid retrieval gain must be measured, not assumed

BEIR-aggregate gains of `+15-30%` recall do not transfer uniformly. The WANDS e-commerce benchmark shows only `+1.7%` from RRF in some configurations. The meme corpus's gain from BM25-over-OCR + dense + RRF must be measured on the held-out target pack before being claimed in the paper.

---

## 6. Revised priority order for the next implementation cycle

| Priority | Action | Evidence basis | Estimated leverage |
| --- | --- | --- | --- |
| 1 | VLM caption enrichment on every indexed image (multi-aspect: visual, OCR-script, template, emotion, language); fuse with current alt-text | VeCLIP `+15-29%` R@1 `[8]` | High; directly attacks the recall-side and near-duplicate failure modes. |
| 2 | HyDE-style query expansion (LLM writes hypothetical meme description for the user prompt; embed that for retrieval) | HyDE; multilingual evidence including Korean/Japanese `[7]` | High; zero-shot, no training, attacks short-prompt and Bangla-prompt failure classes. |
| 3 | Hybrid retrieval: BM25 over OCR text plus existing dense retrieval, fused with RRF (`k=60`) | RRF empirics `[13][14]` | Medium-to-high; gain must be measured on memes specifically. |
| 4 | Small contrastive LoRA-style adapter on BGE-M3 with the 290 (prompt, target) pairs, very low rank (4-8), held-out target-pack gate per epoch, abort on regression | CLIP-LoRA evidence `[11]` plus R1 cautionary tale | Medium; high failure risk, well-documented mitigation. |
| 5 | Continue R2 LTR work (`train_ranker.py`, `train_lambdamart.py`) as research/methodology artifact rather than serving improvement | RankGPT vs tabular pairwise comparison `[2]` | Low for serving, high for paper contribution. |
| 6 | If a serving reranker remains the goal, switch architecture to a listwise LLM reranker with permutation distillation | RankGPT `[2]`; permutation self-consistency `[5]` | Medium-to-high but at higher engineering cost than items 1-3. |
| 7 | Keep the R2 defensive infrastructure: target-grouped splits, blind-ID judges, permutation aggregation, consensus, family disjointness, no-regression gates | NAACL 2024 `[5]`; AACL 2025 `[6]`; LLM-judge survey `[1]` | Methodology floor; do not regress. |

---

## 7. Implications for the R2 plan as written

What the existing R2 plan should keep:

- The two-loop split between retrieval repair and learning-to-rank.
- `vidsearch/feedback/target_split.py` grouped splits by `target_id | template_family | near_duplicate_cluster | language`.
- `vidsearch/feedback/ai_judge.py` blind-ID candidate randomization with permutation aggregation.
- `vidsearch/feedback/consensus.py` confidence-thresholded multi-judge consensus.
- `vidsearch/feedback/prompt_balance.py` answer-leakage regex validation.
- `vidsearch/feedback/post_rlhf_verify.py` overlap-aware verification with `with_overlap` and `without_overlap` blocks.
- The serving-disabled-by-default discipline.

What the existing R2 plan should add:

- A new module or runbook step for VLM-enriched index captions, evaluated on the held-out target pack with the same no-regression gates.
- A new module or runbook step for HyDE-style query expansion at retrieval time, A/B-tested against base retrieval on the held-out target pack.
- A new step for hybrid BM25-over-OCR + dense fusion using RRF.
- Gwet's AC2 in the judge audit metric set.

What the existing R2 plan should deprioritise:

- The 200-per-category prompt floor; lower to 50-75 and treat as soft targets.
- Near-term LambdaMART promotion ambitions; either keep as a research artifact or replace with listwise LLM reranking if a serving reranker is required.
- Generating additional same-family prompts before the family-disjoint judge labelling and audit set are in place.

---

## 8. Bottom line

R1's failure was a structural mismatch between the engineering layer being optimised (pairwise reranker) and the layer where the actual headroom lives (index labels and recall). R2 carries forward the right defensive infrastructure but inherits the same target. The cited literature supports moving the same RLAIF judge investment one layer down: into AI-enriched index labels (VeCLIP-style, `+15-29%` R@1) and zero-shot query expansion (HyDE-style, multilingual evidence), where small-corpus gains are documented in the 15-30% range, not the 1-3% range that any reranker on top of a 92.5% top-1 base can realistically deliver.

The R2 ranker work should continue as a paper-contribution methodology artifact. The next operationally meaningful gain will come from labeling and retrieval, not reranking.

---

## 9. Sources

1. [LLMs-as-Judges: A Comprehensive Survey on LLM-based Evaluation Methods](https://arxiv.org/html/2412.05579v2)
2. [RankGPT: Is ChatGPT Good at Search? Investigating Large Language Models as Re-Ranking Agents](https://arxiv.org/abs/2304.09542)
3. [Promptagator: Few-Shot Dense Retrieval From 8 Examples](https://arxiv.org/abs/2209.11755)
4. [InPars: Data Augmentation for Information Retrieval using Large Language Models](https://arxiv.org/abs/2202.05144)
5. [Permutation Self-Consistency Improves Listwise Ranking in Large Language Models (NAACL 2024)](https://arxiv.org/abs/2310.07712)
6. [Judging the Judges: A Systematic Study of Position Bias in LLM-as-Judge (AACL 2025)](https://arxiv.org/abs/2406.07791)
7. [Precise Zero-Shot Dense Retrieval without Relevance Labels (HyDE)](https://arxiv.org/abs/2212.10496)
8. [VeCLIP: Improving CLIP Training via Visual-enriched Captions](https://arxiv.org/html/2310.07699v2)
9. [BGE M3-Embedding](https://arxiv.org/abs/2402.03216)
10. [Sentence-Transformers training overview](https://www.sbert.net/docs/sentence_transformer/training_overview.html)
11. [CLIP-LoRA: Low-Rank Few-Shot Adaptation of Vision-Language Models (CVPRW 2024)](https://github.com/MaxZanella/CLIP-LoRA)
12. [Beyond Consensus: Mitigating the Agreeableness Bias in LLM Judge Evaluations](https://arxiv.org/html/2510.11822v1)
13. [Hybrid Search Guide: Vectors and Full-Text](https://blog.supermemory.ai/hybrid-search-guide/)
14. [Building Hybrid Search That Actually Works (BM25 + Dense + Cross-Encoders)](https://ranjankumar.in/building-a-full-stack-hybrid-search-system-bm25-vectors-cross-encoders-with-docker)
15. [How Good are LLM-based Rerankers? An Empirical Analysis](https://aclanthology.org/2025.findings-emnlp.305.pdf)
16. [An agent-coded search reranker (development logs)](https://softwaredoug.com/blog/2025/10/19/agentic-code-generation-to-optimize-a-search-reranker)
17. [Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena](https://arxiv.org/abs/2306.05685)
18. [RLAIF vs. RLHF: Scaling RL from Human Feedback with AI Feedback](https://arxiv.org/abs/2309.00267)

---

## 10. Cross-references

- `docs/RLAIF/SELF_LEARNING_CANONICAL_PLAN.md` - the current single source of truth for implementation.
- `docs/RLAIF/archive/RLAIF_MEMERANK_RESEARCH_PLAN.md` - the earlier R2 plan this document reviewed.
- `docs/RLAIF/archive/RLAIF_MEMERANK_TASK_HANDOFF.md` - the earlier implementation task list.
- `docs/RLHF_TRUE_TRAIN_TEST_PLAN.md` - the R1 plan that produced the negative result.
- `docs/experiments/R1_FAILED_RLHF_EXPERIMENT.md` - the R1 negative-result writeup.
- `AGENTS_CONVERSATION.MD` Entry 91 - prior Claude review identifying R1 promotion blockers.
- `AGENTS_CONVERSATION.MD` Entry 95 - R1 corpus verification result confirming negative outcome.
- `AGENTS_CONVERSATION.MD` Entry 96 - prior Claude code review of R2 scaffolding.

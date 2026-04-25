# RLHF_FEEDBACK_LOOP_PLAN.md - Human feedback loop for meme retrieval

**Version:** 2026-04-25  
**Status:** Design plan; implementation-ready after Phase 0 closeout  
**Owner:** primary builder / OpenCode  
**Reviewers:** Codex, Claude  
**Scope:** Add a human-in-the-loop learning system where a user searches for a meme, selects the best result, and the system improves future ranking.

---

## 0. Executive decision

Call this feature **human preference learning for retrieval**, not generic RLHF.

The user's desired loop is:

1. User asks: "find the meme about jobs" or "orange food items on a tray".
2. System returns ranked local images from `data/meme`.
3. User chooses the image that best matches the query.
4. The system records that preference and improves future ranking.

That is a learning-to-rank / relevance-feedback problem. Classical RLHF is useful as background because it learns from human preferences, but the action here is a ranked list of existing images, not generated text. The safest architecture is therefore:

- Keep Phase 0 retrieval unchanged as the deterministic baseline.
- Log every shown result list as an immutable "impression".
- Convert explicit user selections into pairwise preferences: selected image beats shown-but-unselected images for that query.
- Train an additive reranking model offline from those preferences.
- Promote a new ranker only if it beats the baseline in held-out replay and does not regress the fixed Phase 0 eval set.
- Add controlled exploration only after logging and offline evaluation exist.

No first version should fine-tune BGE-M3, SigLIP, the VLM captioner, or OCR. Those are expensive to retrain and easy to corrupt. The first learned component should be a small, versioned ranker that rescales Phase 0 candidates.

---

## 1. Research synthesis

### 1.1 Relevance feedback is the right primitive

Rocchio relevance feedback is the classic retrieval pattern: use user-marked relevant and non-relevant documents to adjust the query representation in vector space. This maps directly to "selected meme is relevant; displayed non-selected memes are weaker negatives." It is useful for immediate session-level improvement, but not enough by itself for durable model learning.

Design consequence: implement optional session-level query expansion later, but make durable learning happen through logged preferences and a ranker.

### 1.2 Clicks and selections are relative preferences

Joachims' clickthrough work treats search logs as `(query, shown ranking, clicked result)` and derives pairwise preferences: if a user clicked a lower-ranked item after seeing higher-ranked items, the clicked item should rank above the skipped items for that query. This matches our explicit "I pick this image" UI even better than normal clicks because the user is intentionally marking the best match.

Design consequence: store the full shown ranking for each search, not just the selected image. A selection without the presented alternatives is not a training example.

### 1.3 Bias must be logged, not ignored

Learning-to-rank literature repeatedly warns that user feedback is biased by presentation position. Top-ranked images are more likely to be seen and clicked even when they are not best. Unbiased LTR methods use propensity weighting or counterfactual estimators, but these require knowing the logging policy and exposure probabilities.

Design consequence: every impression row must store rank, score, ranker version, exploration policy, and propensity. In the first version, explicit selection is strong enough to train a conservative offline ranker, but promotion gates must still protect against position bias.

### 1.4 Contextual bandits are the later online-learning form

Contextual bandits model the system as repeatedly choosing items under context and updating from observed rewards. This is appropriate once we intentionally explore alternative rankings. It is premature before we can log propensities and evaluate candidate rankers offline.

Design consequence: use a staged rollout. Start log-only, then offline ranker, then small controlled exploration. Do not randomize top results on day one.

### 1.5 True RLHF/DPO is not the first implementation

RLHF and DPO are designed around learning from human preferences over generated outputs or policy trajectories. They are relevant if we later fine-tune a query-rewriter, captioner, or answer generator. For Phase 0/Phase 1 retrieval, a pairwise ranker is simpler, safer, easier to evaluate, and enough to learn from "this image is the best match".

Design consequence: reserve "RLHF" for future generative components. The first delivered feature is preference-based reranking.

---

## 2. Product loop

### 2.1 User flow in Open WebUI

Search response should render each image with feedback links:

```markdown
![meme](http://127.0.0.1:8000/thumbnail/img_x.webp)

[Open full image](http://127.0.0.1:8000/image/img_x)
[Select as best match](http://127.0.0.1:8000/feedback/select/<signed_token>)
[Not relevant](http://127.0.0.1:8000/feedback/reject/<signed_token>)
```

Open WebUI markdown links are the most robust first UI. They avoid needing custom frontend code. A click opens a small FastAPI confirmation page:

```text
Feedback recorded.
Query: orange food items on a tray
Selected: img_...
You can close this tab and continue in Open WebUI.
```

Later, replace markdown links with native OWUI actions if Open WebUI exposes a stable button/action API for pipe outputs.

### 2.2 Signals and strength

Use explicit signals first:

| Signal | Strength | Use for training? | Notes |
|---|---:|---|---|
| `selected_best` | 1.0 | yes | Strong positive; creates pairwise preferences against shown unselected hits. |
| `rejected` | -0.5 | yes, cautiously | Negative for that image on that query; weaker than a positive selection. |
| `opened_full_image` | 0.2 | no by default | Useful analytics; not enough to infer relevance. |
| `thumbs_up` | 0.8 | yes | Positive if available without full ranked-list context. |
| `thumbs_down` | -0.5 | yes, cautiously | Negative if available. |
| `none_correct` | -1.0 | yes, aggregate only | Marks all shown hits as poor for the query. |
| `undo` | N/A | removes/invalidates | Required to fix accidental clicks. |

Do not train on passive dwell time in the first version. It is noisy and adds privacy questions.

### 2.3 Feedback granularity

Training should be per `query_text` and `image_id`, but logs must also store:

- `user_token`, if available, for personalization later.
- `conversation_id` or `surface`, if available, to distinguish OWUI from API tests.
- `intent` from the Phase 0 router.
- `ranker_version_id` and `retrieval_config_hash`.
- Full list of displayed alternatives.

This allows both global learning and later per-user preference layers without changing the log format.

---

## 3. Data model

The existing `feedback.events` table is useful for simple feedback, but it is not enough for ranker training because it does not preserve the displayed ranking. Add an additive migration, for example `infra/postgres/003_feedback_loop.sql`.

### 3.1 Search sessions

```sql
CREATE TABLE feedback.search_sessions (
    search_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    query_text TEXT NOT NULL,
    normalized_query TEXT NOT NULL,
    intent TEXT NOT NULL,
    user_token TEXT,
    surface TEXT NOT NULL DEFAULT 'api',
    limit_requested INT NOT NULL,
    ranker_version_id TEXT NOT NULL DEFAULT 'baseline',
    retrieval_config_hash TEXT,
    exploration_policy TEXT NOT NULL DEFAULT 'none',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX search_sessions_query_trgm
    ON feedback.search_sessions USING gin (normalized_query gin_trgm_ops);
CREATE INDEX search_sessions_created_idx
    ON feedback.search_sessions (created_at);
CREATE INDEX search_sessions_user_idx
    ON feedback.search_sessions (user_token);
```

### 3.2 Search impressions

One row per displayed candidate.

```sql
CREATE TABLE feedback.search_impressions (
    search_id UUID NOT NULL REFERENCES feedback.search_sessions(search_id) ON DELETE CASCADE,
    rank INT NOT NULL,
    image_id TEXT NOT NULL REFERENCES core.images(image_id) ON DELETE CASCADE,
    retrieval_score DOUBLE PRECISION NOT NULL DEFAULT 0,
    rerank_score DOUBLE PRECISION,
    feedback_ranker_score DOUBLE PRECISION,
    final_score DOUBLE PRECISION NOT NULL DEFAULT 0,
    propensity DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    feature_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    shown BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (search_id, image_id)
);

CREATE INDEX search_impressions_image_idx
    ON feedback.search_impressions (image_id);
CREATE INDEX search_impressions_search_rank_idx
    ON feedback.search_impressions (search_id, rank);
```

### 3.3 Explicit judgments

```sql
CREATE TABLE feedback.judgments (
    judgment_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    search_id UUID REFERENCES feedback.search_sessions(search_id) ON DELETE CASCADE,
    image_id TEXT REFERENCES core.images(image_id) ON DELETE CASCADE,
    signal TEXT NOT NULL CHECK (
        signal IN (
            'selected_best',
            'rejected',
            'opened_full_image',
            'thumbs_up',
            'thumbs_down',
            'none_correct',
            'undo'
        )
    ),
    strength DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    reason TEXT,
    user_token TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    invalidated_at TIMESTAMPTZ
);

CREATE INDEX judgments_search_idx ON feedback.judgments (search_id);
CREATE INDEX judgments_image_idx ON feedback.judgments (image_id);
CREATE INDEX judgments_signal_idx ON feedback.judgments (signal);
```

### 3.4 Derived preference pairs

Materialize pairwise examples so training is deterministic and auditable.

```sql
CREATE TABLE feedback.preference_pairs (
    pair_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    search_id UUID NOT NULL REFERENCES feedback.search_sessions(search_id) ON DELETE CASCADE,
    winner_image_id TEXT NOT NULL REFERENCES core.images(image_id) ON DELETE CASCADE,
    loser_image_id TEXT NOT NULL REFERENCES core.images(image_id) ON DELETE CASCADE,
    source_judgment_id UUID REFERENCES feedback.judgments(judgment_id) ON DELETE SET NULL,
    pair_weight DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (search_id, winner_image_id, loser_image_id)
);

CREATE INDEX preference_pairs_winner_idx ON feedback.preference_pairs (winner_image_id);
CREATE INDEX preference_pairs_loser_idx ON feedback.preference_pairs (loser_image_id);
```

### 3.5 Ranker registry

```sql
CREATE TABLE feedback.ranker_versions (
    ranker_version_id TEXT PRIMARY KEY,
    artifact_uri TEXT NOT NULL,
    algorithm TEXT NOT NULL,
    trained_from TIMESTAMPTZ,
    trained_to TIMESTAMPTZ,
    training_pairs INT NOT NULL DEFAULT 0,
    config JSONB NOT NULL DEFAULT '{}'::jsonb,
    metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL CHECK (status IN ('candidate', 'active', 'rejected', 'archived')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    activated_at TIMESTAMPTZ
);
```

---

## 4. API changes

### 4.1 Search response must carry `search_id`

Add fields:

```python
class SearchHit(BaseModel):
    rank: int
    image_id: str
    source_uri: str
    thumbnail_uri: str
    feedback_select_url: str | None = None
    feedback_reject_url: str | None = None
    ocr_excerpt: str = ""
    retrieval_score: float
    rerank_score: float | None = None
    feedback_ranker_score: float | None = None
    final_score: float | None = None

class SearchResponse(BaseModel):
    search_id: str
    query: str
    intent: str
    ranker_version_id: str
    total_returned: int
    hits: list[SearchHit]
```

`POST /search` should:

1. Run Phase 0 retrieval.
2. Apply active feedback ranker if enabled.
3. Create `feedback.search_sessions`.
4. Create `feedback.search_impressions` for every displayed hit.
5. Return signed feedback URLs per hit.

### 4.2 Feedback endpoints

Add both machine and browser-friendly endpoints:

```http
POST /feedback/judgment
GET  /feedback/select/{signed_token}
GET  /feedback/reject/{signed_token}
POST /feedback/undo
```

`signed_token` should encode:

- `search_id`
- `image_id`
- `signal`
- `expires_at`
- HMAC signature using `VIDSEARCH_FEEDBACK_SECRET`

This prevents users from manually changing URLs to vote for arbitrary images outside the shown result list.

### 4.3 Pair generation on write

For `selected_best`:

- Winner = selected image.
- Losers = every shown image in the same `search_id` except the winner.
- Weight = `1.0` for losers ranked above the winner, `0.7` for losers ranked below the winner.
- If selected image was rank 1, still create weaker pairs against ranks 2-N with weight `0.5`.

For `rejected`:

- If there is a selected winner in the same session, create winner > rejected with weight `0.7`.
- Without a winner, store the judgment but do not generate pairs immediately.

For `none_correct`:

- Store a session-level negative judgment.
- Do not generate arbitrary winner-loser pairs.
- Use it for analytics and future query-rewrite training.

---

## 5. Features for the learned ranker

The first ranker should use small, explainable, cheap features. Do not store or train directly on full embeddings in v1.

### 5.1 Candidate-level features

Store `feature_snapshot` per impression:

| Feature | Source |
|---|---|
| `rank_phase0` | baseline rank |
| `retrieval_score` | Qdrant hybrid score |
| `rerank_score` | Jina score |
| `intent_onehot` | query router |
| `has_ocr` | `core.image_items` |
| `has_caption` | `core.image_items` |
| `template_name_known` | `template_name != unknown` |
| `query_len_tokens` | query parser |
| `ocr_trigram_similarity` | Postgres `pg_trgm` or Python trigram |
| `retrieval_text_trigram_similarity` | Postgres `pg_trgm` or Python trigram |
| `tag_overlap_count` | query tokens vs tags |
| `template_exact_match` | template alias dictionary |
| `caption_literal_contains_query_token` | simple lexical |
| `caption_figurative_contains_query_token` | simple lexical |
| `source_path_tokens_overlap` | useful for local filenames |
| `width`, `height`, `aspect_ratio` | image metadata |
| `recency_seen_count` | optional anti-repeat feature |

The model should learn how to combine existing retrieval/rerank scores and metadata. It should not replace candidate generation.

### 5.2 Pairwise training rows

For each pair:

```text
x_pair = features(query, winner) - features(query, loser)
y = 1
weight = pair_weight
```

Train a binary pairwise logistic model:

```text
P(winner beats loser) = sigmoid(w · x_pair)
```

At serving time:

```text
final_score = baseline_score + alpha * feedback_ranker_score
```

Where:

- `baseline_score` starts as normalized Jina reranker score, falling back to retrieval score.
- `feedback_ranker_score` is `w · features(query, candidate)`.
- `alpha` starts at `0.15`, then is tuned by offline replay.

### 5.3 Why linear first

Use a NumPy-only linear ranker first:

- No new heavy dependency.
- Fast to train.
- Easy to inspect and roll back.
- Works with small feedback data.
- Lower risk of memorizing one user's accidental clicks.

After at least a few thousand judgments, consider XGBoost/LightGBM LambdaMART. That is a later upgrade, not the first loop.

---

## 6. Learning stages

### Stage A - log-only

Deliverables:

- Full impression logging.
- Feedback links in OWUI.
- Judgments stored.
- Preference pairs generated.
- No ranking changes.

Exit criteria:

- 50 real search sessions logged.
- At least 100 explicit judgments.
- No broken feedback tokens.
- No search latency regression above 5%.

### Stage B - offline ranker

Deliverables:

- `python -m vidsearch.feedback.train_ranker`
- `python -m vidsearch.feedback.evaluate_ranker`
- Ranker artifact written to `models/feedback_ranker/<version>/weights.json`.
- Candidate version inserted into `feedback.ranker_versions`.

Promotion gate:

- Pairwise holdout accuracy >= 0.60.
- nDCG@10 improves on feedback holdout by >= 3 percentage points.
- No Phase 0 fixed eval regression above 2 percentage points.
- At least 100 judgments and 300 preference pairs.

### Stage C - shadow mode

Deliverables:

- Active API computes candidate feedback ranker scores but does not apply them.
- Logs show how the learned ranker would reorder results.
- Compare baseline rank vs shadow rank for selected images.

Promotion gate:

- Shadow rank improves selected image median rank.
- No pathological collapse where the same image appears in top 3 for unrelated queries.
- Manual review of 20 changed rankings.

### Stage D - conservative online rerank

Deliverables:

- `VIDSEARCH_FEEDBACK_RANKER_ENABLED=true`.
- Apply learned ranker only to top 20 Phase 0 candidates.
- Do not introduce new candidates.
- Cap movement: an item cannot move up more than 5 slots at first.
- Baseline fallback env var: `VIDSEARCH_FEEDBACK_RANKER_ENABLED=false`.

Promotion gate:

- Live selected-best top-1 rate improves over the previous active version.
- Negative feedback rate does not increase.
- API p95 latency increase < 50 ms.

### Stage E - controlled exploration

Only after Stage D is stable:

- Randomly swap one candidate into positions 4-8 for a small fraction of sessions.
- Store propensity for every explored item.
- Never randomize rank 1 until offline and shadow metrics are strong.
- Use interleaving or counterfactual evaluation before promoting more aggressive exploration.

Initial policy:

```text
VIDSEARCH_EXPLORATION_RATE=0.05
Explore only ranks 4-8.
One swap per session max.
Never explore on exact_text queries unless the baseline confidence is low.
```

---

## 7. Code architecture

Add package:

```text
vidsearch/feedback/
  __init__.py
  tokens.py              # HMAC feedback token encode/decode
  logging.py             # create sessions, impressions, judgments
  features.py            # feature extraction for query-candidate pairs
  pairs.py               # derive preference_pairs from judgments
  ranker.py              # load/scoring for active model
  train_ranker.py        # offline training CLI
  evaluate_ranker.py     # replay/eval CLI
  artifacts.py           # save/load versioned weights
```

Modify:

```text
vidsearch/api/contracts.py
vidsearch/api/main.py
vidsearch/query/retrieve_images.py
infra/open_webui/functions/meme_search_pipe.py
infra/postgres/003_feedback_loop.sql
docs/runbook.md
docs/owui_integration.md
```

Optional later:

```text
vidsearch/feedback/rocchio.py       # session-level query refinement
vidsearch/feedback/bandit.py        # exploration policy
vidsearch/feedback/personalize.py   # per-user preference layer
```

---

## 8. Serving path

Current Phase 0 path:

```text
query -> intent -> BGE/SigLIP query vectors -> Qdrant hybrid -> Jina rerank -> top hits
```

New path:

```text
query
  -> Phase 0 candidate generation and Jina rerank
  -> feature extraction for top N
  -> optional feedback ranker score
  -> conservative merge with baseline scores
  -> impression logging
  -> OWUI markdown with feedback links
```

Important rules:

- The feedback ranker only reranks Phase 0 candidates.
- It must not decide whether an image is in the corpus.
- It must not write to Qdrant.
- It must not mutate captions, OCR, or embeddings.
- It must be bypassable by one env var.

This keeps the Phase 0 baseline usable even if feedback learning is wrong.

---

## 9. Evaluation plan

### 9.1 Offline metrics

Use feedback holdout split by time:

- Train on older 80%.
- Validate on newer 20%.
- Never random split by pair only, because pairs from the same search session leak.

Metrics:

| Metric | Purpose |
|---|---|
| Pairwise accuracy | Does the model prefer selected images over skipped images? |
| MRR on selected image | How high does the selected image rank? |
| nDCG@10 | Compatible with existing eval protocol. |
| Top-1 selected rate | Product metric: did the chosen image become first? |
| Mean selected rank | More stable on small data. |
| Phase 0 regression nDCG@10 | Guardrail against harming fixed baseline. |

### 9.2 Online metrics

Log daily:

- Searches with explicit feedback.
- Selection rate per search.
- Median selected rank.
- Top-1 selected rate.
- Rejection rate by rank.
- Undo rate.
- "None correct" rate.
- Search latency p50/p95.
- Ranker version distribution.

### 9.3 Counterfactual caveat

If rankings are deterministic, logs mostly show what the current ranker already thought was good. This makes offline evaluation biased. To support stronger counterfactual evaluation later:

- Store propensities from day one.
- Add small exploration only after logging works.
- Keep a baseline holdout where the feedback ranker is disabled.
- Use interleaving or counterfactual estimators only once there is enough randomized traffic.

For this local project, explicit best-match choices reduce the risk, but they do not eliminate position bias.

---

## 10. Personalization plan

Do not personalize in v1.

Later add a small per-user layer:

```text
final_score = baseline_score + global_feedback_score + beta * user_feedback_score
```

Where `user_feedback_score` is trained only from that user's judgments or from a small preference memory:

- User likes certain meme templates.
- User prefers exact text over semantic matches.
- User repeatedly selects a specific visual style.

Guardrails:

- Global ranker never trains directly on private per-user preferences unless opted in.
- User layer can be reset.
- Anonymous mode stores no `user_token`.

---

## 11. Safety and anti-corruption rules

### 11.1 Do not poison the baseline

- Never overwrite OCR/captions/embeddings from feedback.
- Never delete or alter `core.images` from feedback.
- Never update Qdrant vectors from feedback.
- Store every ranker version and artifact hash.
- Keep a `baseline` ranker mode forever.

### 11.2 Prevent accidental bad data

- Every feedback confirmation page should include `Undo`.
- Duplicate same-user same-search same-image judgments collapse or invalidate older rows.
- Ignore feedback tokens after expiration.
- Reject feedback if the image was not in `feedback.search_impressions` for that `search_id`.
- Rate-limit feedback endpoints lightly.

### 11.3 Avoid reward hacking

The learned ranker should not optimize only click/open rate. It should optimize explicit `selected_best` and guard against:

- Always ranking visually bright thumbnails higher.
- Over-promoting common templates.
- Memorizing one frequent query.
- Collapsing different queries to the same popular image.

Add diagnostics:

- Top images by learned-score lift.
- Top query tokens by learned-score lift.
- Per-template score shifts.
- Changed-ranking review report.

---

## 12. Implementation checklist

### Milestone 1 - feedback logging

- [ ] Add `infra/postgres/003_feedback_loop.sql`.
- [ ] Add `vidsearch/feedback/tokens.py` with HMAC token tests.
- [ ] Add `vidsearch/feedback/logging.py`.
- [ ] Extend `/search` to create `feedback.search_sessions`.
- [ ] Extend `/search` to create `feedback.search_impressions`.
- [ ] Add `search_id` and feedback URLs to API contracts.
- [ ] Add `GET /feedback/select/{token}` and `GET /feedback/reject/{token}`.
- [ ] Add `POST /feedback/judgment` for programmatic use.
- [ ] Add unit tests for invalid token, expired token, image not shown, duplicate judgment.

### Milestone 2 - OWUI feedback links

- [ ] Update `infra/open_webui/functions/meme_search_pipe.py` to render `Select as best match` links.
- [ ] Verify links from Open WebUI hit FastAPI and return a confirmation page.
- [ ] Capture one transcript in `docs/owui_integration.md`.
- [ ] Document how to test feedback in `docs/runbook.md`.

### Milestone 3 - pair generation and feature snapshots

- [ ] Implement `vidsearch/feedback/features.py`.
- [ ] Store `feature_snapshot` for every impression.
- [ ] Implement `vidsearch/feedback/pairs.py`.
- [ ] Generate pairs on `selected_best`.
- [ ] Add deterministic tests for pair weights.
- [ ] Add CLI: `python -m vidsearch.feedback.backfill_pairs`.

### Milestone 4 - offline ranker

- [ ] Implement NumPy pairwise logistic training.
- [ ] Save weights to `models/feedback_ranker/<version>/weights.json`.
- [ ] Register candidate in `feedback.ranker_versions`.
- [ ] Add offline replay evaluator.
- [ ] Add report output under `artifacts/feedback_eval/<version>.json`.
- [ ] Add tests for model load, scoring, and deterministic replay.

### Milestone 5 - shadow and promote

- [ ] Add `VIDSEARCH_FEEDBACK_RANKER_SHADOW=true`.
- [ ] Compute shadow score during `/search` without applying it.
- [ ] Add changed-ranking report.
- [ ] Add `VIDSEARCH_FEEDBACK_RANKER_ENABLED=true`.
- [ ] Apply ranker with capped movement.
- [ ] Add `VIDSEARCH_FEEDBACK_RANKER_VERSION=...`.
- [ ] Add rollback command to runbook.

### Milestone 6 - exploration

- [ ] Add `VIDSEARCH_EXPLORATION_RATE`.
- [ ] Implement one-swap exploration for ranks 4-8.
- [ ] Store `exploration_policy` and `propensity`.
- [ ] Add exploration audit report.
- [ ] Keep disabled by default.

---

## 13. Test matrix

### Unit tests

- Token signing, expiration, tamper rejection.
- Search session logging with no hits.
- Impression logging preserves rank order.
- Selected-best creates correct preference pairs.
- Reject without selected-best does not generate bad pairs.
- Feature extraction handles missing OCR/caption.
- Linear ranker scoring is deterministic.
- Ranker artifact load failure falls back to baseline.

### Integration tests

- Search returns `search_id`.
- Returned feedback URL records a judgment.
- Feedback for an unshown image is rejected.
- OWUI formatter includes feedback links.
- Shadow ranker computes scores without changing order.
- Enabled ranker changes order only within movement cap.

### Live smoke tests

1. Query `orange food items on a tray`.
2. Select the correct result.
3. Confirm `feedback.judgments` has `selected_best`.
4. Confirm `feedback.preference_pairs` has pairs.
5. Train ranker on fixture feedback.
6. Run same query in shadow mode and verify selected image improves or holds rank.

---

## 14. Configuration

Add to `.env.example`:

```env
VIDSEARCH_FEEDBACK_ENABLED=true
VIDSEARCH_FEEDBACK_SECRET=change-me-local-secret
VIDSEARCH_FEEDBACK_TOKEN_TTL_SECONDS=604800
VIDSEARCH_FEEDBACK_RANKER_SHADOW=false
VIDSEARCH_FEEDBACK_RANKER_ENABLED=false
VIDSEARCH_FEEDBACK_RANKER_VERSION=baseline
VIDSEARCH_FEEDBACK_RANKER_ALPHA=0.15
VIDSEARCH_FEEDBACK_MAX_UPWARD_MOVEMENT=5
VIDSEARCH_EXPLORATION_RATE=0.0
```

Default production/local behavior:

- Feedback logging enabled.
- Learned reranking disabled.
- Shadow disabled.
- Exploration disabled.

---

## 15. Recommended first implementation prompt

Use this when handing off implementation:

```text
Build Milestone 1 and Milestone 2 from docs/RLHF_FEEDBACK_LOOP_PLAN.md.

Goal: after a meme search in Open WebUI, each returned image has a "Select as best match" link. Clicking it records a durable, auditable feedback judgment tied to the exact search result list that was shown.

Read first:
- AGENTS.md
- AGENT_COMMUNICATION_RULES.md
- AGENTS_CONVERSATION.MD
- docs/RLHF_FEEDBACK_LOOP_PLAN.md
- vidsearch/api/main.py
- vidsearch/api/contracts.py
- infra/open_webui/functions/meme_search_pipe.py
- infra/postgres/001_schema.sql

Implement only:
- Add additive feedback-loop schema migration.
- Add HMAC signed feedback tokens.
- Extend SearchResponse/SearchHit with search_id and feedback URLs.
- Log search_sessions and search_impressions from POST /search.
- Add browser-friendly GET feedback endpoints and JSON POST endpoint.
- Update OWUI pipe markdown to show feedback links.
- Add unit/integration tests for the logging and token flow.

Do not implement learned ranking, exploration, or model training yet.
Do not mutate Qdrant, OCR, captions, or embeddings from feedback.
Keep Phase 0 search results identical except for extra metadata/links.
```

---

## 16. Sources used

- Stanford IR Book, "The Rocchio algorithm for relevance feedback": https://nlp.stanford.edu/IR-book/html/htmledition/the-rocchio-algorithm-for-relevance-feedback-1.html
- Joachims, "Optimizing Search Engines using Clickthrough Data" (KDD 2002): https://www.cs.cornell.edu/people/tj/publications/joachims_02c.pdf
- Joachims, Swaminathan, Schnabel, "Unbiased Learning-to-Rank with Biased Feedback": https://arxiv.org/abs/1608.04468
- Swaminathan and Joachims, "Batch Learning from Logged Bandit Feedback through Counterfactual Risk Minimization" (JMLR 2015): https://jmlr.org/papers/v16/swaminathan15a.html
- Li, Chu, Langford, Schapire, "A Contextual-Bandit Approach to Personalized News Article Recommendation": https://arxiv.org/abs/1003.0146
- Zhuang and Zuccon, "Counterfactual Online Learning to Rank": https://arvinzhuang.github.io/files/arvin2020counterfactual.pdf
- Christiano et al., "Deep Reinforcement Learning from Human Preferences" (NeurIPS 2017): https://papers.nips.cc/paper/7017-deep-reinforcement-learning-from-human-preferences
- Ouyang et al., "Training language models to follow instructions with human feedback": https://arxiv.org/abs/2203.02155
- Rafailov et al., "Direct Preference Optimization: Your Language Model is Secretly a Reward Model": https://arxiv.org/abs/2305.18290
- Jiang and Li, "Doubly Robust Off-policy Value Evaluation for Reinforcement Learning": https://proceedings.mlr.press/v48/jiang16.html
- XGBoost Learning to Rank documentation, for future tree-ranker option: https://xgboost.readthedocs.io/en/release_3.2.0/tutorials/learning_to_rank.html


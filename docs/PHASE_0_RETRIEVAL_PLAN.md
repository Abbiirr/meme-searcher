# PHASE_0_RETRIEVAL_PLAN.md — How we actually fetch memes from descriptions

**Version:** 2026-04-19
**Status:** Design doc (companion to `PHASE_0_PLAN.md` + `PHASE_0_TODO.md`; does **not** override them)
**Audience:** engineers, Codex, OpenCode
**Scope:** the retrieval core of the Phase 0 meme chat app. API shapes are deliberately out of scope — those live in `PHASE_0_PLAN.md` §8.

---

## 0. What this document answers

> A user opens the Open WebUI chat app, types a plain-English description of a meme they half-remember, and expects the matching image from `K:\projects\video_searcher\data\meme` to appear inline in the chat.
>
> How do we actually make that work?

This file is the concrete retrieval reality: what we compute per image at ingest, what we compute per query, how we fuse the signals, and how we rerank. It names the models, the thresholds, and the failure modes. Everything here is aligned to the four canonical Phase 0 query classes (`exact_text`, `fuzzy_text`, `semantic_description`, `mixed_visual_description`) and to the 40-query × 10-per-class eval.

---

## 1. The single most important decision: **caption-then-retrieve**

The naïve approach — encode the user's query with SigLIP-2's text tower and do cosine similarity against SigLIP-2 visual vectors — underperforms badly on open-ended description queries. The 2025 consensus across multiple sources is that **caption-first pipelines beat raw CLIP-style cross-modal retrieval by a very large margin** for tasks that look like ours:

- Instruction-tuned / captioned pipelines versus CLIP-style dual-encoder retrieval: a reported gap of roughly **60% → 95% accuracy** on cross-modal retrieval tasks. ([TheDataGuy, Dec 2025](https://thedataguy.pro/blog/2025/12/multimodal-embeddings-evolution/))
- Meme-specific prior art (CM50, 2025) uses VLMs to produce **literal captions + figurative/meme captions + literary-device labels** at ingest, and pairs that with a meme-text retrieval CLIP (mtrCLIP) trained on those outputs. ([CM50 paper](https://arxiv.org/html/2501.13851v1))
- The original MemeCap work already demonstrated that plain image captions are insufficient for memes; you need a second "what does the meme *mean*" caption alongside the literal one. ([MemeCap](https://arxiv.org/abs/2305.13703))

**Conclusion:** Phase 0's retrieval core is **not** primarily "text→image embedding." It is **"text→text embedding over VLM-generated labels, with visual embeddings as the backup leg."** That flip is the design.

This collapses three of the four Phase 0 query classes into well-understood text retrieval problems:

| Class | What the user types | Primary signal |
|---|---|---|
| `exact_text` | "the one that says i am once again asking" | OCR text (dense + sparse) |
| `fuzzy_text` | "stonks meme", "disttractedboyfriend" | OCR text (sparse forgives typos) + caption keywords |
| `semantic_description` | "someone exhausted and done with life" | VLM figurative caption (dense) |
| `mixed_visual_description` | "the drake meme about code review" | template name + VLM caption + OCR (fused) |

Raw SigLIP visual search stays in the mix only as an insurance leg — it catches the rare cases where both OCR and the VLM caption miss the meme but the visual encoder recognises it from a frame-similarity prior.

---

## 2. What we compute per image at ingest (the "label schema")

Each file in `data/meme` becomes one row in `core.images`, one row in `core.image_items`, and one point in Qdrant with named vectors. The ingest pipeline produces the following fields. Everything written here is the *source data* for the retrieval legs; §3 describes the legs themselves.

### 2.1 Deterministic identity and file facts

| Field | How | Purpose |
|---|---|---|
| `image_id` | `sha256(canonical_bytes)` after re-encoding through Pillow to a canonical JPEG Q95 | idempotent dedupe, stable across re-ingest |
| `source_uri` | absolute path under `data/meme/...` | display in chat |
| `width`, `height`, `format`, `bytes` | Pillow probe | payload filters + sanity check |
| `thumbnail_uri` | MinIO object at `thumbnails/{image_id}.webp`, 512 px long edge, Q80 | OWUI inline display |

### 2.2 OCR layer (PaddleOCR PP-OCRv5)

| Field | How | Purpose |
|---|---|---|
| `ocr_raw_boxes` | full PP-OCRv5 output with per-token confidence | debugging, later box-level filtering |
| `ocr_text_hi` | concatenation of tokens with confidence ≥ 0.60, normalised (lowercase, collapsed whitespace, smart-quote fold, unicode-NFKC) | the clean text that feeds BGE-M3 |
| `ocr_text_all` | concatenation of **all** tokens regardless of confidence, same normalisation | a fallback field that `fuzzy_text` queries fall back to when `ocr_text_hi` is empty |
| `has_ocr` | `len(ocr_text_hi) >= 3` | payload filter: routes queries away from the OCR leg when false |

PaddleOCR misreads punctuation and apostrophes often; the normalisation step folds them aggressively so the query "i am once again asking" matches OCR output `"i'm once again asking"` and `"im once again askjng"` alike. BGE-M3's sparse leg (learned token weights) handles the rest of the fuzziness — this is what takes us from strict BM25 to genuinely fuzzy OCR match. ([BGE-M3 sparse retrieval guide](https://thenewstack.io/generate-learned-sparse-embeddings-with-bge-m3/))

### 2.3 VLM caption layer — **the most important new thing**

Every image is captioned at ingest by **Qwen3-VL-8B-Instruct** (stable; Qwen3-VL-30B-A3B is a stretch-only quality experiment, not a default). The captioning pass runs once per image and writes three short fields:

| Field | Prompt template | Target length |
|---|---|---|
| `caption_literal` | "Describe exactly what is visible in this image in one short sentence. Do not interpret. Do not add anything that is not shown." | 10–25 tokens |
| `caption_figurative` | "In one short sentence, say what this meme is *about* or what feeling it expresses. Do not repeat the literal image description." | 10–25 tokens |
| `template_name` | "If this image is a well-known meme template, respond with only its common name (e.g. 'drake', 'distracted boyfriend', 'expanding brain', 'this is fine', 'two buttons'). If it is not a known template, respond with 'unknown'." | 1–4 tokens |
| `tags` | "Give 3–7 short tags describing this meme. Each tag is 1–3 words. Comma-separated. No sentences." | 20–40 tokens |

Design rules for the VLM prompt pass:

- **Keep captions short.** Long captions add noise to the dense leg and tank retrieval precision. Enforce with a `max_new_tokens=48` budget and a hard regex stripper on the output.
- **Two captions, not one.** The literal caption catches `mixed_visual_description` queries ("batman slapping robin meme about deadlines" — the visual is batman slapping robin, the "about deadlines" part is user spin). The figurative caption catches `semantic_description` queries ("someone exhausted and done with life" → skeleton meme). Meme retrieval fails when only one of the two is available. ([MemeCap](https://arxiv.org/abs/2305.13703), [CM50](https://arxiv.org/html/2501.13851v1))
- **Template name is a cheap jackpot.** When the VLM correctly names the template, retrieval becomes almost trivial — the user's natural-language phrase ("the drake meme about code review") hits the template name directly. Confidence is low for obscure images, hence the `unknown` sentinel. Do not fabricate templates.
- **Tags are bag-of-words fodder for the sparse leg.** They increase recall on short queries without drowning the dense leg.

All four fields are concatenated into a single searchable `retrieval_text` field with clear separators so the retriever sees:

```
[CAP_LIT] <caption_literal>
[CAP_FIG] <caption_figurative>
[TEMPLATE] <template_name>
[TAGS] <tags>
[OCR] <ocr_text_hi>
```

The separators are literal string tokens; BGE-M3's tokenizer treats them as distinct subwords, which lets us later do field-scoped boosts if the eval shows (for example) `[TEMPLATE]` matches should weigh higher.

### 2.4 Embedding layer

Three named vectors per Qdrant point:

| Named vector | Model | Routing | Dim | Input |
|---|---|---|---|---|
| `text-dense` | BGE-M3 dense | **direct-local** (gateway has no equivalent with sparse leg) | 1024 | the full `retrieval_text` blob above |
| `text-sparse` | BGE-M3 sparse (SPLADE-style learned weights from the same model) | **direct-local** (same reason) | vocab-size sparse | same input |
| `visual` | SigLIP-2 So400m/patch16-384 image tower | **direct-local** (gateway exposes no image-embedding endpoints) | 1152 | canonical image bytes |

Companion models used elsewhere in the pipeline (routing also recorded in `docs/MODEL_GATEWAY.md`):

| Model | Role | Routing |
|---|---|---|
| Qwen3-VL-8B-Instruct | §2.3 caption pass at ingest | **gateway** alias `meme_vlm_captioner` → upstream `qwen3.6-vlm-local` |
| PaddleOCR PP-OCRv5 | §2.2 OCR at ingest | **gateway** alias `meme_ocr` → upstream `paddle-ocr` |
| jina-reranker-v2-base-multilingual | §5 rerank | **direct-local** |

BGE-M3 is used for both dense and sparse in a single model pass (this is its whole point), which halves VRAM use relative to running a separate SPLADE. The `text-sparse` leg is what forgives OCR typos and fuzzy recall ("disttractedboyfriend" still hits `"distracted boyfriend"` because the learned token expansion knows the ambiguity). ([BGE-M3 model card](https://huggingface.co/BAAI/bge-m3))

### 2.5 Optional Lane C backfill (deferred, flag-gated)

If the VLM caption is low-confidence (literal caption < 8 tokens, or model refused, or template `unknown` and tags empty), Phase 0 may optionally re-caption via Gemini 2.5 Flash-Lite through LiteLLM. This stays **off by default** in Phase 0. It is wired for the first full corpus run but the decision to enable it is post-eval.

### 2.6 Per-image ops metadata (reproducibility)

Every ingest writes an entry into `ops.model_versions` containing the exact model identifiers — `paddleocr_version`, `paddleocr_weights_hash`, `bge_m3_rev`, `siglip2_rev`, `qwen3_vl_rev`, `qwen3_vl_quantization`, `prompt_template_hash` — and the ingest pipeline hashes all of that into a `config_hash` that is pinned onto the eval run. A change to any of these fields forces a re-embed of affected images (see §9.3 of `PHASE_0_PLAN.md`).

---

## 3. What we compute per query

At query time we have exactly one input: a plain-text user query. No user-supplied image. The query path produces four vectors and an intent tag.

### 3.1 Intent routing — cheap and hybrid, not LLM-based

The "router latency paradox" applies: an LLM router adds ~200–500 ms to every query and buys little. ([Semantic routing survey, 2025](https://arxiv.org/html/2502.00409v1)) Phase 0 uses a **three-tier cheap cascade**:

1. **Regex layer (~0.1 ms).** Quoted substrings or all-lowercase run-on strings of ≥ 6 tokens → `exact_text`. Short (≤ 3-token) queries with known meme-template keywords ("stonks", "doge", "disaster girl", "distracted boyfriend", "this is fine") → `fuzzy_text`.
2. **Keyword heuristic (~1 ms).** Queries containing strong visual nouns ("meme where", "the one with", "picture of") → bias toward `mixed_visual_description`. Queries containing emotional/abstract language ("feels like", "when you", "that moment when") → bias toward `semantic_description`.
3. **Fallback (the common case).** Run *all three retrieval legs* with default weights and let RRF sort it out. Intent is only used to tune leg weights; it never gates a leg off.

This is intentionally low-effort. A tiny classifier or an embedding-based semantic router can be added in Phase 2 if evals show the heuristics are leaking recall. Phase 0 does not depend on intent being correct — it depends on the fused retrieval being correct.

### 3.2 Query encoding

The query text is encoded three ways, reusing the same models as ingest:

| Query vector | Model | Target named vector in Qdrant |
|---|---|---|
| `q_dense` | BGE-M3 dense | `text-dense` |
| `q_sparse` | BGE-M3 sparse | `text-sparse` |
| `q_visual` | **SigLIP-2 text tower** on the raw query text | `visual` |

That last row is the subtle one. Phase 0 has no user-supplied image — so we project the **text** through SigLIP-2's text tower and compare it against the image tower's vectors in the shared space. This is a much weaker signal than BGE-M3 over captions (which is why captioning is the main leg), but it catches cases where the VLM caption missed the mark. SigLIP-2 was explicitly trained for this text-tower→image-tower retrieval use case and continues to dominate its scale tier on COCO/Flickr30K zero-shot retrieval. ([SigLIP-2 paper, Feb 2025](https://arxiv.org/abs/2502.14786))

### 3.3 Intent-conditional leg weights (passed to Qdrant RRF)

RRF with equal weights is the safe default; intent-conditional weights are a tunable we adjust during the eval runs:

| Intent | `text-dense` | `text-sparse` | `visual` |
|---|---|---|---|
| `exact_text` | 1.0 | **1.5** | 0.3 |
| `fuzzy_text` | 1.0 | **1.3** | 0.5 |
| `semantic_description` | **1.3** | 0.7 | 0.8 |
| `mixed_visual_description` | 1.2 | 0.9 | **1.1** |

Starting values only. Qdrant's RRF query supports per-prefetch weights natively. ([Qdrant hybrid queries docs](https://qdrant.tech/documentation/search/hybrid-queries/))

---

## 4. Fusion: Qdrant server-side RRF

We use Qdrant's Query API with three `prefetch` clauses — one per named vector — combined with `fusion: rrf`. The standard RRF formula is:

```
RRF_score(doc) = Σ  1 / (k + rank_i(doc))
```

- **k = 60** as the starting point (Qdrant's documented default, and the value used in most public RRF papers). ([Qdrant RRF explainer](https://medium.com/@Iraj/how-qdrant-combines-query-results-explaining-rrf-and-dbsf-cd08cd272a80))
- **Per-prefetch weights** from the table in §3.3.
- **Prefetch size = 100 per leg.** The top-100 from each leg goes into fusion; fusion returns top-50 to the reranker; reranker returns top-10 to the chat.

Known failure modes to watch for in the eval runs:

1. **One leg dominates.** If a leg consistently puts the same image at rank 1, the RRF score stays high regardless of the other legs. Mitigation: lower that leg's weight or cap its contribution with `limit` on the prefetch.
2. **Empty leg on a query.** If OCR is empty for every candidate (pure-visual meme corpus slice) the sparse leg returns noise. Mitigation: the `has_ocr=true` payload filter on the sparse prefetch when the intent is `semantic_description` or `mixed_visual_description`.
3. **Duplicate ranks at the tail.** RRF gives tied candidates the same score. Mitigation: the reranker (§5) breaks ties.

We also keep Qdrant's DBSF (distribution-based score fusion) as a one-line swap for A/B: it normalises raw scores instead of ranks, which sometimes beats RRF when the three legs' score distributions are wildly different. Not the default in Phase 0.

---

## 5. Reranker

Jina reranker v2 base multilingual is a **text-only cross-encoder**. ([Jina v2 reranker card](https://huggingface.co/jinaai/jina-reranker-v2-base-multilingual)) It takes `(query, document)` pairs. Since our "document" is an image, we reconstruct a text document per candidate at rerank time:

```
document = f"{template_name}. {caption_literal} {caption_figurative} OCR: {ocr_text_hi}"
```

- Template name first so a template match dominates.
- Figurative and literal captions next because they carry the most semantic content.
- OCR last because it is noisy and the reranker is sensitive to noise.
- Truncate to 256 tokens; the reranker's context is short and verbose OCR can poison the score.

Uplift target: **reranker nDCG@10 ≥ RRF-only nDCG@10 + 2 percentage points** on the 40-query eval (this is the existing `P0-G3` gate).

If the text-only reranker proves insufficient on `mixed_visual_description` queries, the Phase 0 fallback is to swap in **jina-reranker-m0** which is a true multimodal cross-encoder that takes `(query, image)` pairs directly. ([Jina m0 reranker](https://jina.ai/models/jina-reranker-m0/)) This is a heavier model and a Phase 0 risk we accept only if v2 fails the gate.

---

## 6. End-user surface: returning the image to the OWUI chat

The chat-app end-state (per `PHASE_0_PLAN.md` §2) requires the matching image to appear **inline in the chat response**. In Open WebUI, the reliable way to do this is to have the tool return markdown with an HTTP URL that OWUI renders as an image:

```markdown
![meme](http://api:8000/thumbnail/{image_id}.webp)
_source: data/meme/reaction/stonks.jpg_
```

Markdown image links are rendered inline by OWUI's chat UI. Base64 data URIs technically work but break when the image is large — OWUI has a documented issue with long base64 strings. ([OWUI discussion #17172](https://github.com/open-webui/open-webui/discussions/17172), [OWUI issue #16976](https://github.com/open-webui/open-webui/issues/16976))

Phase 0 serves thumbnails from the FastAPI backend at a static `/thumbnail/{image_id}.webp` path (no presigned URLs, no MinIO exposure to the browser — OWUI and the API share the Docker network). The original-resolution path `/image/{image_id}` is reserved for click-through when the thumbnail is not enough.

API shape for that endpoint is out of scope per the user's direction; the retrieval plan only cares that the tool response string contains a valid markdown image link that OWUI can render.

---

## 7. Concrete ingest pipeline (no API shapes, just the graph)

```
data/meme/*.{jpg,png,webp,gif,...}
  │
  ├─► filter supported formats (Pillow probe; reject EPS, PSD, corrupt)
  ├─► re-encode to canonical JPEG Q95 bytes → sha256 → image_id
  ├─► write original to MinIO: raw/{image_id}.{ext}
  ├─► generate 512-px webp thumbnail → thumbnails/{image_id}.webp
  │
  ├─► PaddleOCR PP-OCRv5  ─────► ocr_raw_boxes, ocr_text_hi, ocr_text_all
  │
  ├─► Qwen3-VL-8B caption pass (4 prompts in one conversation turn)
  │       ├─► caption_literal
  │       ├─► caption_figurative
  │       ├─► template_name  (or "unknown")
  │       └─► tags
  │
  ├─► build retrieval_text blob (§2.3)
  │
  ├─► BGE-M3 single forward pass ─► text-dense (1024d) + text-sparse
  ├─► SigLIP-2 image tower        ─► visual (1152d)
  │
  ├─► Postgres: upsert core.images + core.image_items (all fields above)
  └─► Qdrant: upsert point(image_id) with all three named vectors + payload
        (payload: source_uri, thumbnail_uri, template_name, has_ocr,
         format, width, height, ingested_at, config_hash)
```

Every step checkpoints to `ops.ingest_steps` so a second run on the same file becomes a full cache hit.

**Throughput plan (single consumer GPU, 24 GB):**
- PaddleOCR: ~5–15 images/sec on GPU.
- Qwen3-VL-8B captioning pass (4 short prompts, greedy, max_new_tokens=48): bottleneck, roughly 1–2 images/sec on 24 GB (much better with FP8 quantization). ([Qwen3-VL FP8 variant](https://huggingface.co/prithivMLmods/Qwen3-VL-8B-Abliterated-Caption-it-FP8))
- BGE-M3: ~50 texts/sec.
- SigLIP-2 So400m: ~15 images/sec.
- Net: **captioning dominates**. Full `data/meme` ingest (~3,107 images) lands in 30–60 minutes with Qwen3-VL-8B FP8. Budget a single unattended overnight run and we are done.

---

## 8. Concrete query pipeline

```
user query string (from OWUI chat)
  │
  ├─► intent routing (regex → keyword → fallback to all-legs)
  │
  ├─► BGE-M3 query encode ─► q_dense, q_sparse        (~20 ms)
  ├─► SigLIP-2 text tower ─► q_visual                 (~15 ms)
  │
  ├─► Qdrant Query API: fusion=rrf (k=60)
  │     prefetch text-dense  (limit=100, weight by intent)
  │     prefetch text-sparse (limit=100, weight by intent)
  │     prefetch visual      (limit=100, weight by intent)
  │     limit=50, with_payload=true
  │                                                    (~30 ms)
  │
  ├─► build (query, reconstructed_doc) pairs for top-50
  ├─► jina-reranker-v2 rerank                         (~200 ms for 50)
  │
  ├─► top-10 candidates with final scores
  │
  └─► format OWUI tool response:
        rank 1 inline image (markdown) + source path + short "why it matched"
        ranks 2..5 as secondary thumbnails (optional, behind a flag)
```

End-to-end latency budget: **< 500 ms** warm, **< 2 s** cold. The `P0-G3` wall-clock target (P95 < 20 s) in `PHASE_0_PLAN.md` is dominated by the LLM synthesis layer we deliberately kept out of scope here.

---

## 9. Known failure modes and what we do about them

| Failure | Symptom | Mitigation |
|---|---|---|
| VLM hallucinates a template name | `template_name="drake"` on a meme that is not drake → false positives on drake queries | Validate template names against a whitelist of ~40 well-known templates; anything else becomes `unknown` |
| OCR misreads a key word | `"mordor"` → `"modor"` → exact-text query fails | BGE-M3 sparse forgives; also mitigated by the `ocr_text_all` fallback and by the template/caption legs |
| Caption is too verbose | dense retrieval overfits to one caption phrase | `max_new_tokens=48` + post-regex to clip trailing commentary |
| Pure-visual meme, weak caption, OCR empty | `semantic_description` queries miss it | SigLIP visual leg still retrieves it; eval this case explicitly with at least 2 of the 10 `semantic_description` queries targeting OCR-empty memes |
| Query is multilingual (Bangla / mixed) | BGE-M3 is fine; SigLIP-2 is *also* multilingual (new in v2). No action. PP-OCRv5 already multilingual. | — |
| Reranker tanks on queries whose top-50 has no good candidate | reranker promotes the least-bad → misleadingly confident rank 1 | Reject the tool call if top-1 reranker score < threshold (tuned on eval); surface "no good match" in OWUI |
| OWUI renders broken image | markdown link unreachable | Health-check the thumbnail endpoint at API boot; alert loudly |

---

## 10. The knobs to tune during the 40-query eval

These are the parameters we adjust in evaluation sweeps, not at runtime:

1. **RRF k** (default 60; try 10, 30, 100).
2. **Per-leg weights** (table in §3.3) — per intent.
3. **Prefetch size per leg** (default 100; try 50, 200).
4. **Reranker input template** — order of fields, separators, truncation length.
5. **OCR confidence cutoff** for `ocr_text_hi` (default 0.60; try 0.50, 0.70).
6. **Caption `max_new_tokens`** (default 48; try 32, 64).
7. **VLM temperature** (default 0.0 greedy; the caption pass should be deterministic).
8. **Template whitelist coverage** — does adding 20 more templates to the whitelist lift `mixed_visual_description` recall?

Every tuning run writes to `eval.runs` with its `config_hash`. The `P0-G3` gate closes on the single best config — but we keep all runs for regression testing.

---

## 11. Prior art we are explicitly borrowing from

- **CM50 (2025)** — their 50-template annotation pipeline via large VLM is very close to our caption-pass design. We are not using their dataset; we are copying their pattern of "literal caption + figurative caption + template + tags" for our own corpus. ([CM50](https://arxiv.org/html/2501.13851v1))
- **MemeCap (2023→2025)** — the two-captions-not-one insight comes from here. ([MemeCap](https://arxiv.org/abs/2305.13703))
- **mtrCLIP** — a meme-text-retrieval CLIP trained on VLM-generated captions. We are *not* training our own model in Phase 0; we are achieving a similar effect by captioning at ingest and retrieving over the captions with BGE-M3. (Referenced in [CM50](https://arxiv.org/html/2501.13851v1).)
- **Qdrant hybrid search** — RRF + per-prefetch weights + named vectors. Canonical pattern. ([Qdrant docs](https://qdrant.tech/documentation/search/hybrid-queries/))
- **Hybrid retrieval + rerank** — production RAG pattern, exactly the shape we are building. ([Production RAG survey](https://machine-mind-ml.medium.com/production-rag-that-works-hybrid-search-re-ranking-colbert-splade-e5-bge-624e9703fa2b))

---

## 12. What is intentionally **out of scope** for Phase 0

- No fine-tuning of any model (SigLIP, BGE, reranker, VLM). Zero-shot everything.
- No user-supplied query image.
- No ColBERT / multivector leg. (`text-colbert` is reserved in the Qdrant schema for Phase 3, not used here.)
- No knowledge-graph integration (IMKG, KnowYourMeme scraping). Template names are VLM-generated against a short whitelist; graph lookup is a Phase 4 possibility.
- No captioning through hosted VLMs by default. Lane C backfill is flag-gated and disabled in the P0 baseline.
- No query-rewriting / HyDE / query expansion. If the 40-query eval shows a recall ceiling, revisit in Phase 3.
- No A/B of reranker v2 vs m0 — commit to v2; fall back to m0 only if the gate fails.

---

## 13. Open questions the eval must answer

1. Does `caption_figurative` actually lift `semantic_description` queries materially over `caption_literal` alone? If not, collapse to one caption and save half the VLM budget.
2. Is Qwen3-VL-8B FP8 quality sufficient for `template_name`, or do we need the 30B stretch variant for that one field?
3. Does SigLIP-2 visual leg contribute enough to pay for its weight? If equal-weight RRF beats intent-conditional weights on eval, simplify.
4. Does the reranker need image input (m0), or can text-only v2 close the `P0-G3` gate?
5. How often does the `unknown` template fallback fire, and does it correlate with retrieval failure?

These five are logged to `docs/decision_log.md` as Phase 0 open questions, with their resolutions becoming ADRs after the first full eval run.

---

## 14. Minimum viable path if we are time-boxed

If we need to cut for schedule, drop in this order:

1. Optional Lane C caption backfill (already off by default — just leave off).
2. Intent-conditional weights (use equal weights, accept a small recall hit).
3. `tags` field from the VLM (keep only literal + figurative + template).
4. SigLIP visual leg (accept `mixed_visual_description` recall hit).
5. Reranker (accept `P0-G3` uplift gate slipping).

We should not need to cut past step 2. The caption pass is non-negotiable — without it, we do not hit the quality bar.

---

## 15. References

- [SigLIP-2 paper (arXiv 2502.14786)](https://arxiv.org/abs/2502.14786)
- [SigLIP-2 HF card (so400m/patch16-384)](https://huggingface.co/google/siglip2-so400m-patch14-384)
- [BGE-M3 model card](https://huggingface.co/BAAI/bge-m3)
- [BGE-M3 sparse embeddings explainer](https://thenewstack.io/generate-learned-sparse-embeddings-with-bge-m3/)
- [Qdrant hybrid queries / RRF docs](https://qdrant.tech/documentation/search/hybrid-queries/)
- [Qdrant RRF explainer (Iraj, Medium)](https://medium.com/@Iraj/how-qdrant-combines-query-results-explaining-rrf-and-dbsf-cd08cd272a80)
- [Jina reranker v2 base multilingual](https://huggingface.co/jinaai/jina-reranker-v2-base-multilingual)
- [Jina reranker m0 (multimodal fallback)](https://jina.ai/models/jina-reranker-m0/)
- [Qwen3-VL-8B-Instruct](https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct)
- [Qwen3-VL-8B caption-tuned FP8 variant](https://huggingface.co/prithivMLmods/Qwen3-VL-8B-Abliterated-Caption-it-FP8)
- [MemeCap dataset](https://arxiv.org/abs/2305.13703)
- [CM50 dataset / VLM annotation pipeline](https://arxiv.org/html/2501.13851v1)
- [Meme template retrieval via image embeddings](https://pmc.ncbi.nlm.nih.gov/articles/PMC12112496/)
- [Multimodal embeddings evolution survey, Dec 2025](https://thedataguy.pro/blog/2025/12/multimodal-embeddings-evolution/)
- [Production RAG pattern (hybrid + rerank)](https://machine-mind-ml.medium.com/production-rag-that-works-hybrid-search-re-ranking-colbert-splade-e5-bge-624e9703fa2b)
- [LLM routing survey, Feb 2025](https://arxiv.org/html/2502.00409v1)
- [OWUI image rendering discussion](https://github.com/open-webui/open-webui/discussions/17172)
- [OWUI base64 display issue](https://github.com/open-webui/open-webui/issues/16976)

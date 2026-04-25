# MODEL_GATEWAY.md — Which models go through the gateway, which stay direct-local

**Version:** 2026-04-20
**Status:** Authoritative model-routing map for Phase 0
**Upstream:** `docs/PHASE_0_REMAINING_PLAN.md` §2; `docs/PHASE_0_RETRIEVAL_PLAN.md` §2

## 0. TL;DR

- One gateway at `http://127.0.0.1:4000` (LiteLLM), master key in `.env` as `LITELLM_MASTER_KEY`.
- **Four models go through the gateway:** VLM captioner, OCR, synthesis LLM, controller LLM.
- **Three models stay direct-local:** BGE-M3 (dense+sparse), SigLIP-2 (image + text towers), jina-reranker-v2.
- Every model referenced by Phase 0 ingest or query paths MUST have a row in `ops.model_versions` with a deterministic fingerprint — gateway-routed models fingerprint the upstream model ID + endpoint hash; direct-local models fingerprint the `safetensors` blob.

## 1. Live gateway verification (2026-04-20)

Probe:

```
curl -sS -H "Authorization: Bearer $LITELLM_MASTER_KEY" $LITELLM_URL/health/liveliness
# → HTTP 200 in 0.26s
curl -sS -H "Authorization: Bearer $LITELLM_MASTER_KEY" $LITELLM_URL/v1/models | jq '.data | length'
# → 46
```

Confirmed-present upstream models used by this project:

| Upstream model ID | Role |
|---|---|
| `qwen3.6-vlm-local` | Qwen3-VL multimodal chat (captioning) |
| `paddle-ocr` | PaddleOCR PP-OCRv5 OCR |
| `glm-ocr` | GLM-OCR alternate OCR (fallback only) |
| `nomic-embed-text-v2-moe` | Nomic text embedding (NOT used by Phase 0) |
| `fast` | default fast chat LLM (Groq-backed per earlier smoke test) |
| `thinking` | slower reasoning LLM |
| `vision` | alternate VLM (fallback for `qwen3.6-vlm-local`) |

## 2. Routing decision (authoritative)

| Model | Role in Phase 0 | Routing | Gateway alias or local path | Rationale |
|---|---|---|---|---|
| Qwen3-VL-8B-Instruct | VLM captioning at ingest (4 prompts per image) | **gateway** | `meme_vlm_captioner` → upstream `qwen3.6-vlm-local` | Gateway exposes it; avoids owning ~8 GB weights + an inference server per dev machine |
| PaddleOCR PP-OCRv5 | OCR at ingest | **gateway** | `meme_ocr` → upstream `paddle-ocr` | Closes blocker B without pinning local weights; gateway is the single reproducibility surface |
| Synthesis LLM | OWUI answer shaping | **gateway** | `meme_synthesis` → upstream `fast` | Latency-sensitive response text; upstream switches providers behind the gateway |
| Controller LLM | intent/reasoning helper if we add one later in P0 | **gateway** | `meme_controller` → upstream `thinking` | Reserved for P0-G4 tuning; not hot path yet |
| BGE-M3 dense + sparse | text retrieval legs (dense + sparse in one forward pass) | **direct-local** | `K:\models\video_searcher\bge-m3\` via `FlagEmbedding` / `transformers` | Gateway offers `nomic-embed-text-v2-moe`, not BGE-M3; retrieval plan §2.4 requires BGE-M3's learned sparse leg for fuzzy OCR recall — `nomic-embed` has no sparse counterpart |
| SigLIP-2 So400m/patch16-384 | visual leg + query text tower | **direct-local** | `K:\models\video_searcher\siglip2-so400m-patch16-384\` | Gateway does not expose image-embedding endpoints; needed in both ingest (image tower) and query (text tower) |
| jina-reranker-v2-base-multilingual | cross-encoder rerank of top-50 | **direct-local** | `K:\models\video_searcher\jina-reranker-v2-base-multilingual\` | Gateway does not expose reranker endpoints; <1 GB footprint, fast enough locally |

## 3. Fallbacks

- If `meme_vlm_captioner` (Qwen3-VL) fails repeatedly, swap upstream to `vision` in `infra/litellm/config.yaml` without changing calling code.
- If `meme_ocr` (PaddleOCR) is down, swap upstream to `glm-ocr`; the alias stays.
- No fallback for BGE-M3 in Phase 0 — it is load-bearing. If it goes down, ingest halts. (Phase 1+ may add a Nomic fallback behind a flag.)
- No fallback for SigLIP-2 — the visual leg is only a 10–20 % contributor at target leg weights, so the system degrades gracefully to text-only retrieval if SigLIP is missing, but `P0-G4` thresholds require it.

## 4. Fingerprints in `ops.model_versions`

### Gateway-routed fingerprint recipe

At API boot, for each gateway alias we use:

1. Call `GET $LITELLM_URL/v1/models`.
2. Find the record for the upstream model ID.
3. Compute `fingerprint = sha256(json.dumps({alias, upstream_id, api_base, gateway_build_sha}, sort_keys=True))`.
4. `UPSERT` into `ops.model_versions` with `model_key=<alias>`, `family='litellm-gateway'`, `version=<upstream_id>`, `revision=<fingerprint>`.

`gateway_build_sha` is `GET $LITELLM_URL/health/readiness` → `version` field (if exposed) or the `sha256` of the `/v1/models` response body as a fallback.

### Direct-local fingerprint recipe

For each of BGE-M3, SigLIP-2, jina-reranker-v2:

1. Walk the local model directory.
2. Compute `fingerprint = sha256(sha256(model.safetensors) + sha256(config.json) + sha256(preprocessor_config.json if present))`.
3. `UPSERT` into `ops.model_versions` with `model_key=<canonical_id>`, `family=<family>`, `version=<hf_repo_id>`, `revision=<fingerprint>`.

The canonical IDs that the rest of the code refers to:

| `model_key` | `family` | `version` |
|---|---|---|
| `meme_vlm_captioner` | `litellm-gateway` | `qwen3.6-vlm-local` |
| `meme_ocr` | `litellm-gateway` | `paddle-ocr` |
| `meme_synthesis` | `litellm-gateway` | `fast` |
| `meme_controller` | `litellm-gateway` | `thinking` |
| `text_dense` | `bge-m3` | `BAAI/bge-m3` |
| `text_sparse` | `bge-m3` | `BAAI/bge-m3` |
| `visual` | `siglip2` | `google/siglip2-so400m-patch16-384` |
| `reranker` | `jina-reranker` | `jinaai/jina-reranker-v2-base-multilingual` |

Every Qdrant point's `model_version` payload field is a dict `{ "text_dense": <rev>, "text_sparse": <rev>, "visual": <rev>, "caption": <rev>, "ocr": <rev> }` keyed by the `model_key` column, so per-image reproducibility survives re-embed cycles.

## 5. Env contract

The only gateway-related env vars that the project code reads:

- `LITELLM_URL` — base URL for the gateway, default `http://127.0.0.1:4000`.
- `LITELLM_MASTER_KEY` — bearer token for `Authorization`.
- `VIDSEARCH_MODEL_ROOT` — filesystem root for the three direct-local models; default `K:\models\video_searcher`.

No per-model API key, no per-model endpoint override. Everything under one gateway.

## 6. When to move a direct-local model onto the gateway

Move a direct-local model onto the gateway when all of:

1. The gateway exposes the equivalent model (or an acceptable substitute) and the substitute is measured against the Phase 0 eval set without regression.
2. The calling code only needs HTTP-flavoured inputs (no in-process batching / no Python object exchange).
3. The latency overhead of HTTP is < 10 % of the leg's total cost.

Until then, BGE-M3 / SigLIP-2 / jina-reranker-v2 stay direct-local.

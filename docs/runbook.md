# Runbook

Operational commands for the meme search engine. Follow top-to-bottom on a cold start.

## 1. Boot the stack

```bash
cp .env.example .env
# Edit .env with your values — see §2 for the required variables
docker compose up -d
docker compose ps  # wait for all services to report healthy
```

Expected compose services: `api`, `postgres`, `qdrant`, `minio`, `open-webui`.
Host-side dependency: LiteLLM gateway on `127.0.0.1:4000` (not a compose service in this repo).

## 2. Required environment

The following variables must be set in `.env` before the ingest pipeline will route through the gateway:

| Variable | Default | Purpose |
|---|---|---|
| `LITELLM_URL` | `http://127.0.0.1:4000` | Host-side LiteLLM gateway URL for local commands and host-run Python |
| `LITELLM_INTERNAL_URL` | `http://host.docker.internal:4000` | Gateway URL injected into Linux containers (`api`, `open-webui`) |
| `LITELLM_MASTER_KEY` | `sk-my-secret-gateway-key` | Bearer token for `/v1/*` endpoints |
| `OPEN_WEBUI_ADMIN_EMAIL` | `admin@localhost` | Bootstrap admin used only when the OWUI DB has no users yet |
| `OPEN_WEBUI_ADMIN_PASSWORD` | `admin` | Bootstrap admin password for the empty-DB case |
| `OPEN_WEBUI_RAG_EMBEDDING_ENGINE` | `openai` | Forces OWUI to use the LiteLLM gateway for its own RAG embeddings instead of downloading a local sentence-transformer at boot |
| `OPEN_WEBUI_RAG_EMBEDDING_MODEL` | `nomic-embed-text` | Embedding alias OWUI uses through LiteLLM |
| `VIDSEARCH_ENABLE_CAPTIONS` | `true` | Toggle the 4-prompt VLM caption pass; Phase 0 requires this enabled |
| `VIDSEARCH_ENABLE_VISUAL_QUERY` | `false` | Enables the SigLIP text-to-visual query leg; keep `false` on lower-memory machines |
| `VIDSEARCH_PREWARM_RETRIEVAL` | `true` | Preloads the text retrieval stack in the API container after boot |
| `VIDSEARCH_OCR_BACKEND` | `gateway` | `gateway` (default) or `local`; `local` keeps PaddleOCR direct-call as an offline fallback |
| `VIDSEARCH_MODEL_ROOT` | `K:/models/video_searcher` | Host-side direct-local weights root (mounted into containers as `/app/models`) |
| `VIDSEARCH_DATA_ROOT` | `K:/projects/video_searcher/data` | Host-side corpus root (mounted into containers as `/app/data`) |

If `LITELLM_MASTER_KEY` is unset, both `compute_gateway_fingerprint()` and every gateway-routed model call short-circuit to a no-op — ingest will still run but captions and gateway-OCR are skipped.

## 3. Local endpoint defaults

- LiteLLM gateway: `http://127.0.0.1:4000`
- FastAPI: `http://127.0.0.1:8000`
- Open WebUI: `http://127.0.0.1:${OPEN_WEBUI_HOST_PORT}`
- Qdrant: `http://127.0.0.1:6333`
- MinIO console: `http://127.0.0.1:9001`
- Postgres: `localhost:5432` (user `vidsearch`, db `vidsearch`)
- Ollama (if wired for a direct escape-hatch alias): `http://127.0.0.1:11434`
- Local Qwen3.6 llama.cpp server (if bound): `http://127.0.0.1:8080`

The LiteLLM gateway is the default Open WebUI provider surface and may expose more models than the raw local endpoints. Keep its host in `.env` via `LITELLM_URL`. Keep the Open WebUI host port in `.env` via `OPEN_WEBUI_HOST_PORT` if the default `3000` binding is unavailable.

For this machine, the stable Phase 0 runtime profile is text-first search: captions + OCR + BGE-M3 retrieval with reranking, while the optional SigLIP visual query leg stays disabled by default via `VIDSEARCH_ENABLE_VISUAL_QUERY=false`.

The OWUI container auto-provisions a `Meme Search` model at boot. Use that model for local meme retrieval. Do not expect plain chat models like `vision` to search `data/meme`.

## 4. Gateway liveness probe

Before any ingest, confirm the gateway answers from inside the API container:

```bash
docker compose exec api \
  curl -sS -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  "$LITELLM_URL/v1/models" | jq '.data | length'
```

Expect a positive integer (46 aliases at time of writing). If this returns 0, a connection error, or `null`, do not proceed — fix the URL/key first. Host-side probe is equivalent:

```bash
curl -sS -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  http://127.0.0.1:4000/v1/models | jq '.data | length'
```

## 5. Bootstrap Qdrant

```bash
python -m infra.qdrant.bootstrap
```

Creates the `memes_v1` collection with three named vectors: `text-dense` (BGE-M3 dense, 1024-dim), `text-sparse` (BGE-M3 sparse), `visual` (SigLIP-2, 1152-dim). Aliased to `memes`.

## 6. Small-ingest first (always)

Before running a full folder ingest, always prove the pipeline end-to-end on 5 images. This is the approval gate for moving to the full corpus.

```bash
python -m vidsearch.ingest.images --path data/meme/<known_ocr_heavy>.jpg
python -m vidsearch.ingest.images --path data/meme/<known_caption_heavy>.jpg
python -m vidsearch.ingest.images --path data/meme/<known_pure_visual>.jpg
python -m vidsearch.ingest.images --path data/meme/<fourth>.jpg
python -m vidsearch.ingest.images --path data/meme/<fifth>.jpg
```

Then assert:

```bash
# PG row count
docker compose exec postgres psql -U vidsearch -d vidsearch \
  -c "SELECT COUNT(*) FROM core.images;"

# Qdrant point count
curl -s http://127.0.0.1:6333/collections/memes | jq '.result.points_count'

# Caption columns populated
docker compose exec postgres psql -U vidsearch -d vidsearch \
  -c "SELECT image_id, caption_literal, caption_figurative, template_name, array_length(tags,1), length(retrieval_text) FROM core.image_items ORDER BY updated_at DESC LIMIT 5;"

# Fingerprints seeded
docker compose exec postgres psql -U vidsearch -d vidsearch \
  -c "SELECT model_key, family, revision FROM ops.model_versions;"
```

All five rows must have non-NULL `caption_literal`, `caption_figurative`, `template_name` (or explicit `unknown`), `tags` array length ≥ 1, and `retrieval_text` length > 0. Re-run the same 5 images; expect zero new rows (SHA-256 idempotency).

A helper script `test_small.py` at the repo root patches out OCR and runs a minimal 3-image ingest for very fast smoke testing.

## 7. Full ingest — data/meme

```bash
python -m vidsearch.ingest.images --folder data/meme
```

Re-running is idempotent (duplicates detected by SHA-256; early-return after `hash` step). Expected post-run invariant:

```bash
# Must be equal
pg_count=$(docker compose exec -T postgres psql -U vidsearch -d vidsearch -t -c "SELECT COUNT(*) FROM core.images;")
qd_count=$(curl -s http://127.0.0.1:6333/collections/memes | jq '.result.points_count')
echo "pg=$pg_count qdrant=$qd_count"
```

## 8. Ingest pipeline steps (reference)

`ops.ingest_steps` records one row per step per image. Canonical order:

1. `hash` — SHA-256 of the raw bytes (also serves as dedup key).
2. `decode` — Pillow decode; records `width`, `height`, `fmt`.
3. `thumbnail` — 512px webp, pushed to MinIO at `thumbnails/<image_id>.webp`.
4. `ocr` — gateway call to `meme_ocr` (with auto-retry to `meme_ocr_fallback` on HTTP error); `VIDSEARCH_OCR_BACKEND=local` falls back to direct PaddleOCR.
5. `caption` — 4-prompt gateway call to `meme_vlm_captioner` emitting literal, figurative, template, tags per `PHASE_0_RETRIEVAL_PLAN.md` §2.3.
6. `embed_text` — BGE-M3 dense + sparse over the `retrieval_text` blob (falls back to raw OCR if blob is empty).
7. `embed_visual` — SigLIP-2 forward pass (direct-local from `$MODEL_ROOT/embeddings/siglip2-so400m-patch16-384`).
8. `upsert_pg` — `core.images` + `core.image_items` write (13 columns including the 5 caption fields).
9. `upsert_qdrant` — single point with three named vectors + payload.

A step may be `done`, `skipped`, or `error`. A failure on any non-critical step (thumbnail, ocr, caption) does not abort the batch; the row is written with whatever succeeded.

## 9. Run a search

```bash
curl -X POST http://127.0.0.1:8000/search \
  -H "Content-Type: application/json" \
  -d '{"query":"drake meme about code review","limit":10}'
```

The response is a `SearchResponse` with `query`, `intent`, `total_returned`, and `hits[]`. Each hit carries `retrieval_score` and `rerank_score`. Intent detection is classifier-based over the four canonical classes (`exact_text`, `fuzzy_text`, `semantic_description`, `mixed_visual_description`); rerank top-K is intent-conditional (30/40/50/50).

## 9.1 Run the UI path

1. Open `http://127.0.0.1:${OPEN_WEBUI_HOST_PORT}`.
2. Sign in.
3. Select the `Meme Search` model in the OWUI model picker.
4. Enter a natural-language meme description such as `orange food items on a tray`.

Expected result:
- OWUI returns markdown with inline image thumbnails from `http://127.0.0.1:8000/thumbnail/...`
- the benchmark query above returns `data\meme\10933027.png` at rank 1 in the current runtime
- if you use `vision` or another normal chat model instead of `Meme Search`, you will get plain LLM text rather than local meme retrieval

## 10. Run the eval

```bash
python -m vidsearch.eval.runner --queries vidsearch/eval/queries_memes.yaml --limit 10
```

Eval YAML is exactly 10/10/10/10 balanced across the four intent classes. Each run upserts `eval.queries`, writes `eval.runs`, `eval.run_results`, and emits both aggregate and per-intent metrics (e.g. `Recall@10__exact_text`, `nDCG@10__fuzzy_text`). Per-intent breakdown is the primary surface for tuning — aggregate numbers hide class-specific regressions.

**P0-G4 thresholds (all must pass):** `Recall@10 ≥ 0.90`, `top_1_hit_rate ≥ 0.70`, `reranker_uplift_ndcg10 ≥ 0.02`, no `exact_text` miss outside top 10.

If thresholds are missed, tune per `docs/PHASE_0_RETRIEVAL_PLAN.md` §10 (leg weights, RRF k, reranker template, OCR confidence cutoff) and rerun.

## 11. Delete one image

```bash
curl -X DELETE http://127.0.0.1:8000/image/{image_id}
```

This removes the image from Postgres (`core.images` cascade), Qdrant (point delete), and MinIO (thumbnail delete). Subsequent `GET` on the same `image_id` should return 404.

## 12. Back up Postgres

```bash
docker compose exec postgres pg_dump -U vidsearch vidsearch > backup_$(date +%Y%m%d).sql
```

## 13. Snapshot Qdrant

```bash
curl -X POST http://127.0.0.1:6333/collections/memes_v1/snapshots
```

Snapshots are stored in the Qdrant data volume.

## 14. Mirror MinIO thumbnails

```bash
docker compose exec minio mc mirror local/thumbnails ./backup_thumbnails_$(date +%Y%m%d)/
```

## 15. Restore Postgres

```bash
docker compose exec -T postgres psql -U vidsearch vidsearch < backup_YYYYMMDD.sql
```

## 16. Restore Qdrant

```bash
curl -X PUT http://127.0.0.1:6333/collections/memes_v1/snapshots/upload \
  -F 'snapshot=@<snapshot_file>'
```

## 17. Verify restore

After restoring all three stores, run one known-good search:

```bash
curl -X POST http://127.0.0.1:8000/search \
  -H "Content-Type: application/json" \
  -d '{"query":"drake meme","limit":5}' | jq '.hits[0].image_id'
```

Paste the drill transcript (commands + outputs) into this runbook under §18 as evidence for P0-G5.

## 18. Backup / restore drill evidence

*Placeholder. Populate with transcript after the first drill run — required for P0-G5 sign-off.*

## 19. Structured logging

All ingest and API operations use Python structured logging via the `vidsearch` logger namespace. Set `LOG_LEVEL=DEBUG` for verbose output. Gateway calls log at INFO including the resolved alias; local-weight loads log at INFO with the resolved path.

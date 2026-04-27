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
Host-side dependency: LiteLLM gateway on `127.0.0.1:4100` (not a compose service in this repo).

## 2. Required environment

The following variables must be set in `.env` before the ingest pipeline will route through the gateway:

| Variable | Default | Purpose |
|---|---|---|
| `LITELLM_URL` | `http://127.0.0.1:4100` | Host-side LiteLLM gateway URL for local commands and host-run Python |
| `LITELLM_INTERNAL_URL` | `http://host.docker.internal:4100` | Gateway URL injected into Linux containers (`api`, `open-webui`) |
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

- LiteLLM gateway: `http://127.0.0.1:4100`
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
  http://127.0.0.1:4100/v1/models | jq '.data | length'
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
5. `caption` — 4-prompt gateway call to `qwen3.6-vlm-wrapper` emitting literal, figurative, template, tags per `PHASE_0_RETRIEVAL_PLAN.md` §2.3.
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

## 9.2 Test feedback capture

Feedback is rendered only by the `Meme Search` OWUI pipe.

1. In Open WebUI, select `Meme Search`.
2. Search for a known meme, for example `"iterator iterator for loop"`.
3. Click `Select` under the best result.
4. A FastAPI confirmation page opens at `/feedback/confirm/<signed_token>` and auto-submits `POST /feedback/judgment`.
5. The confirmation page should show `Feedback recorded`.

Verify the write:

```bash
docker compose exec postgres psql -U vidsearch -d vidsearch \
  -c "SELECT action, image_id, created_at FROM feedback.judgments ORDER BY created_at DESC LIMIT 5;"
```

Duplicate clicks on the same `Select` link are idempotent. `Undo` tombstones the active judgment and derived preference pairs.

Current local status (2026-04-25): the reusable Codex-agent bootstrap loop has trained and promoted `feedback_pairwise_v1_d1325bb7c307`, served from `artifacts/feedback_rankers/latest.json`. The API is configured with `VIDSEARCH_FEEDBACK_RANKER_ENABLED=true` and returns non-null `learned_score` values. To return to Phase 0-only ordering, use the rollback command below.

Useful feedback maintenance commands:

```bash
python -m vidsearch.feedback.backfill_pairs
python -m vidsearch.feedback.snapshots --output artifacts/feedback_snapshots/latest.jsonl
python -m vidsearch.feedback.exporters --snapshot artifacts/feedback_snapshots/latest.jsonl --output-dir artifacts/feedback_exports/latest
python -m vidsearch.feedback.train_ranker --output artifacts/feedback_rankers/latest.json --approve-promotion --p0-g4-passing
python -m vidsearch.feedback.evaluate_ranker --artifact artifacts/feedback_rankers/latest.json --output artifacts/feedback_eval/latest.json --changed-report-prefix artifacts/feedback_eval/latest_changed
```

## 9.3 Reusable LLM-agent feedback loop

Use this when an agent such as Codex, Claude Code, or OpenCode is standing in for the human selector. The loop builds an auditable review pack from an eval run, lets the agent provide decisions, applies those decisions through the same signed feedback-token path used by OWUI, then trains and evaluates the ranker with the normal hard gates.

Important: this eval-run slate loop only tests learning-to-rank for candidates already found by the retriever. The preferred RLHF benchmark loop now starts from `data/meme_rlhf`: inspect each target image, generate natural prompts, run search against the full corpus, select the target if it appears, and record `target_not_found` if it does not.

Target-image benchmark loop:

```text
data/meme_rlhf image
-> AI agent writes natural prompts
-> system searches full data/meme corpus with no target hint
-> evaluator checks whether target image appears in top K
-> found: select target and train ranking pairs
-> missing: record retrieval-failure correction case
```

Prompt-generation instructions live in `docs/AGENT_PROMPT_LABELING_INSTRUCTIONS.md`.

Build the target pack from `data/meme_rlhf`:

```powershell
python -m vidsearch.feedback.target_benchmark build-target-pack `
  --folder data/meme_rlhf `
  --output artifacts/feedback_targets/target_pack.jsonl

python -m vidsearch.feedback.target_benchmark write-target-prompt `
  --pack artifacts/feedback_targets/target_pack.jsonl `
  --output artifacts/feedback_targets/agent_prompt.md `
  --labels-output artifacts/feedback_targets/target_prompts.jsonl
```

Automated AI-agent prompt generation uses the LiteLLM gateway. This keeps prompt labels under the same model-routing and provenance surface as Phase 0 captioning.

```powershell
python -m vidsearch.feedback.target_benchmark generate-prompts-gateway `
  --pack artifacts/feedback_targets/target_pack.jsonl `
  --output artifacts/feedback_targets/target_prompts.jsonl `
  --model qwen3.6-vlm-wrapper `
  --gateway-url $env:LITELLM_URL `
  --resume
```

If the image-VLM path is unavailable or too slow, keep the run on LiteLLM and generate prompts from the target pack's reviewer metadata instead of falling back to direct Ollama:

```powershell
python -m vidsearch.feedback.target_benchmark generate-prompts-metadata-gateway `
  --pack artifacts/feedback_targets/target_pack.jsonl `
  --output artifacts/feedback_targets/target_prompts.jsonl `
  --model fast `
  --gateway-url $env:LITELLM_URL `
  --prompts-per-image 1 `
  --batch-size 8 `
  --resume
```

Do not use direct Ollama unless the user explicitly accepts fallback provenance for that run. If `localhost:4100` is down, restore the gateway first.

Then run those prompts against the full corpus:

```powershell
.\scripts\rlhf_target_benchmark.ps1 `
  -Pack artifacts/feedback_targets/target_pack.jsonl `
  -Prompts artifacts/feedback_targets/target_prompts.jsonl `
  -ClientSessionPrefix rlhf-target `
  -Operator codex-agent `
  -ReplacePrefix `
  -Train
```

This writes:

- `artifacts/feedback_targets/results.jsonl`, with every target/prompt search result.
- `artifacts/feedback_targets/target_not_found.jsonl`, with retrieval failures that the ranker cannot fix.
- Normal feedback `judgments` and `preference_pairs` for found targets, created through the same signed-token path as Open WebUI.

Use `-Limit N` on the wrapper for a small proof run:

```powershell
.\scripts\rlhf_target_benchmark.ps1 -BuildPack -WriteAgentPrompt -GeneratePrompts -Limit 5 -ReplacePrefix -Train
```

One-command controlled bootstrap:

```powershell
.\scripts\rlhf_agent_loop.ps1 `
  -EvalRunId 21b3ade7-e9b4-4803-9a52-cb17370c8a28 `
  -ClientSessionPrefix rlhf-agent `
  -Operator codex-agent `
  -ReplacePrefix
```

Manual agent-in-the-loop mode:

```bash
python -m vidsearch.feedback.agent_operator build-pack \
  --eval-run-id 21b3ade7-e9b4-4803-9a52-cb17370c8a28 \
  --output artifacts/feedback_agent/review_pack.jsonl \
  --top-k 20 \
  --repeats 5

python -m vidsearch.feedback.agent_operator write-prompt \
  --pack artifacts/feedback_agent/review_pack.jsonl \
  --output artifacts/feedback_agent/agent_prompt.md
```

Give `artifacts/feedback_agent/agent_prompt.md` and `artifacts/feedback_agent/review_pack.jsonl` to the agent reviewer. The agent writes one JSONL decision per task to `artifacts/feedback_agent/decisions.jsonl`. Apply those decisions:

```bash
python -m vidsearch.feedback.agent_operator apply-decisions \
  --pack artifacts/feedback_agent/review_pack.jsonl \
  --decisions artifacts/feedback_agent/decisions.jsonl \
  --client-session-prefix rlhf-agent \
  --operator codex-agent \
  --replace-prefix
```

Then train and evaluate:

```bash
python -m vidsearch.feedback.train_ranker --output artifacts/feedback_rankers/latest.json --approve-promotion --p0-g4-passing
python -m vidsearch.feedback.evaluate_ranker --artifact artifacts/feedback_rankers/latest.json --output artifacts/feedback_eval/latest.json --changed-report-prefix artifacts/feedback_eval/latest_changed
```

The `--replace-prefix` flag makes reruns reproducible by deleting prior feedback sessions with that client-session prefix before recreating them. Omit it if you want the script to fail rather than overwrite an existing bootstrap set.

Emergency rollback for learned feedback ranking:

```bash
VIDSEARCH_FEEDBACK_RANKER_ENABLED=false
VIDSEARCH_FEEDBACK_RANKER_SHADOW=false
docker compose up -d --no-deps --force-recreate api
```

The rollback returns serving to Phase 0 ordering. Feedback logging can remain enabled because it does not mutate Qdrant vectors, OCR, captions, thumbnails, or corpus records.

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

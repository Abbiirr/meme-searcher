# PHASE_0_REMAINING_PLAN.md — What is left to finish Phase 0

**Version:** 2026-04-20
**Status:** Residual plan for the open work
**Owner:** OpenCode (implementer)
**Reviewers:** Codex, Claude
**Upstream sources (authoritative; do not drift from them):**
- `docs/PHASE_0_PLAN.md` — the gates, corpus contract, API contract, data model, eval contract
- `docs/PHASE_0_TODO.md` — the full Phase 0 task list
- `docs/PHASE_0_RETRIEVAL_PLAN.md` — *how* retrieval works (caption-then-retrieve, per-image labels, query fusion, reranker)

This file is not a rewrite. It is a **residual plan**: only the parts of Phase 0 that are still open as of 2026-04-20, synthesised into one readable sequence. Everything already green (OWUI v0.9.1, Postgres 18, gateway env wiring, doc alignment) is not repeated here.

Companion: `docs/PHASE_0_REMAINING_TODO.md` — the actionable checklist for the same scope.

---

## 0. Residual scope at a glance

Phase 0 ships a working OWUI chat app over `data/meme`. What is left to turn the current skeleton into that product:

1. **Wire the LiteLLM gateway as the single entry path for captioning and OCR** (live at `http://127.0.0.1:4000`).
2. **Add the caption pass to ingest** per `PHASE_0_RETRIEVAL_PLAN.md` §2.3 — Qwen3-VL labels + OCR → one `retrieval_text` blob.
3. **Extend the schema** for the new caption columns and the `retrieval_text` field.
4. **Unblock SigLIP-2 visual leg** (direct-local, the gateway does not expose image-embedding models).
5. **Rebuild the eval YAML** to the 10/10/10/10 split with graded qrels.
6. **Re-prove small ingest** (PG count == Qdrant count, visual non-zero, caption columns populated).
7. **Full `data/meme` ingest** + record ADR baseline.
8. **E2E search proof** + OWUI chat-app transcripts.
9. **Baseline eval run** against the P0-G4 thresholds.
10. **Delete + backup/restore drill.**
11. **Sign-off on the Phase 1 transition doc** and close P0-G6.

The ordering in §8 below is the shortest critical path. Each section states what changes, why, and the close condition (the same line that appears in `PHASE_0_REMAINING_TODO.md`).

---

## 1. Gateway verification and env fix — precondition

**Status:** Gateway is live at `http://127.0.0.1:4000`. Verified 2026-04-20:
- `GET /health/liveliness` → HTTP 200
- `GET /v1/models` with `Authorization: Bearer sk-my-secret-gateway-key` returns 46 models including `qwen3.6-vlm-local`, `paddle-ocr`, `glm-ocr`, `nomic-embed-text-v2-moe`, `fast`, `thinking`
- `POST /v1/chat/completions` with `model=fast` returns a valid completion

**Drift to fix:** `.env` currently has `LITELLM_URL=http://192.168.0.251:4000`, which times out from this workspace. Flip to `http://127.0.0.1:4000`. Keep the `.env.example` default aligned.

**Why this is the first move:** the caption pass (step 2) and any gateway-routed OCR (step 3) both fail fast if the URL is wrong. Fixing env first avoids diagnosing bad URLs under load.

**Close condition:** `curl -sS -H "Authorization: Bearer $LITELLM_MASTER_KEY" $LITELLM_URL/v1/models | jq '.data | length'` returns ≥ 1 from the API container and from a host shell, both using the same `LITELLM_URL`.

---

## 2. Gateway-vs-direct decision (blocker **N**)

Before touching code, decide per model whether it runs through the gateway or stays direct-local. This is the one design call that unblocks caption wiring.

| Model | Role | Gateway available? | Decision for Phase 0 |
|---|---|---|---|
| Qwen3-VL-8B-Instruct | VLM captioning at ingest | ✅ `qwen3.6-vlm-local` at 127.0.0.1:4000 | **gateway** (alias: `meme_vlm_captioner`) |
| PaddleOCR PP-OCRv5 | OCR at ingest | ✅ `paddle-ocr` on gateway | **gateway** (alias: `meme_ocr`); record gateway model ID + endpoint hash as the fingerprint in `ops.model_versions` |
| BGE-M3 dense + sparse | text retrieval legs | ❌ gateway offers `nomic-embed-text-v2-moe`, not BGE-M3 | **direct-local** (retrieval plan §2.4 depends on BGE-M3's sparse leg for fuzzy OCR recall — `nomic-embed` has no sparse counterpart) |
| SigLIP-2 So400m/patch16-384 | visual leg + query text tower | ❌ gateway does not expose image-embedding models | **direct-local** |
| jina-reranker-v2-base-multilingual | rerank | ❌ not on gateway | **direct-local** |
| Synthesis / tool LLM | answer shaping in OWUI | ✅ `fast`, `thinking` on gateway | **gateway** (aliases: `meme_synthesis`, `meme_controller`) |

**Record this table** as the authoritative mapping in a new doc `docs/MODEL_GATEWAY.md`. Mark each model `gateway` or `direct-local` in `PHASE_0_RETRIEVAL_PLAN.md` §2.4 table.

**Close condition:** `docs/MODEL_GATEWAY.md` exists; `PHASE_0_RETRIEVAL_PLAN.md` §2.4 rows are annotated.

---

## 3. Caption pass + OCR through gateway + schema migration (blockers **B + L + M** together)

One PR, three blockers closed.

### 3.1 Schema migration (blocker **M**)

Add to `core.image_items` (per `PHASE_0_RETRIEVAL_PLAN.md` §2.3):

| Column | Type | Notes |
|---|---|---|
| `caption_literal` | `TEXT` | from Qwen3-VL prompt 1 |
| `caption_figurative` | `TEXT` | from Qwen3-VL prompt 2 |
| `template_name` | `TEXT` | from Qwen3-VL prompt 3, `'unknown'` sentinel allowed, validated against whitelist |
| `tags` | `TEXT[]` | from Qwen3-VL prompt 4, split on `,` |
| `retrieval_text` | `TEXT` | the concatenated blob from §2.3 — the exact string fed to BGE-M3 |

- Fresh installs: update `infra/postgres/001_schema.sql`.
- Existing volumes: add `infra/postgres/002_captions.sql` migration that `ALTER TABLE`s.
- `has_caption BOOL` is kept as `true` when `caption_literal` is non-empty.

Also add a template whitelist file `infra/data/template_whitelist.txt` (start with the ~40 well-known templates from `PHASE_0_RETRIEVAL_PLAN.md` §9: drake, distracted boyfriend, expanding brain, this is fine, two buttons, stonks, doge, disaster girl, change my mind, batman slapping robin, hide the pain harold, roll safe, woman yelling at cat, etc.). Validate VLM output against this; fallback to `unknown`.

**Close condition:** fresh PG18 boot creates the new columns; existing dev volumes can apply `002_captions.sql` cleanly.

### 3.2 LiteLLM gateway aliases (blocker **N** applied)

Extend `infra/litellm/config.yaml` with semantic aliases — do not keep the loopback placeholder shape (`openai/fast` → `api_base=LITELLM_URL`). Replace with routing to real upstream IDs:

```yaml
model_list:
  - model_name: meme_vlm_captioner
    litellm_params:
      model: openai/qwen3.6-vlm-local   # upstream ID on the live gateway
      api_base: os.environ/LITELLM_URL
      api_key: os.environ/LITELLM_MASTER_KEY
  - model_name: meme_ocr
    litellm_params:
      model: openai/paddle-ocr
      api_base: os.environ/LITELLM_URL
      api_key: os.environ/LITELLM_MASTER_KEY
  - model_name: meme_synthesis
    litellm_params:
      model: openai/fast
      api_base: os.environ/LITELLM_URL
      api_key: os.environ/LITELLM_MASTER_KEY
  - model_name: meme_controller
    litellm_params:
      model: openai/thinking
      api_base: os.environ/LITELLM_URL
      api_key: os.environ/LITELLM_MASTER_KEY
```

Keep `local_qwen36_vlm`, `local_ollama_glm_ocr`, `local_ollama_nomic_embed` from Entry 16 for compatibility; they document the direct endpoints. The new aliases are what the code imports.

**Close condition:** `litellm --config infra/litellm/config.yaml --test` passes; the API container can reach `meme_vlm_captioner`, `meme_ocr`, `meme_synthesis`.

### 3.3 Caption module (blocker **L**)

Create `vidsearch/ingest/caption.py` with the exact prompt shapes from `PHASE_0_RETRIEVAL_PLAN.md` §2.3:

- Four prompts per image in a single multi-turn call to `meme_vlm_captioner`.
- `max_new_tokens=48` per response, `temperature=0.0`.
- Post-regex clipping on each response.
- Return a dataclass `Captions(literal, figurative, template, tags)`.
- Template name validated against the whitelist from §3.1.
- If the gateway call fails, write empty captions + `has_caption=false` and let the pipeline continue (caption is load-bearing but must not halt the whole batch on a transient gateway hiccup; ingest_steps records `caption: error`).

**`retrieval_text` builder** (utility function, same file):

```
[CAP_LIT] {caption_literal}
[CAP_FIG] {caption_figurative}
[TEMPLATE] {template_name}
[TAGS] {tags_joined}
[OCR] {ocr_text_hi}
```

**Close condition:** on 5 known memes, all four caption columns populate and `retrieval_text` is non-empty. VLM is reached only through the gateway (no direct `8080` calls from `caption.py`).

### 3.4 OCR through gateway (blocker **B**)

Replace the current direct-PaddleOCR call in `vidsearch/ingest/ocr.py` with a gateway call to `meme_ocr`. Record the fingerprint:

```sql
INSERT INTO ops.model_versions (model_id, purpose, endpoint, fingerprint, recorded_at)
VALUES ('meme_ocr', 'ocr', 'litellm:paddle-ocr', '<sha256 of model_id + endpoint + gateway build>', now());
```

The `<sha256>` is computed once at API boot by hitting `GET /v1/models` and hashing the returned JSON record for `paddle-ocr`. Pin it into `ops.model_versions` before any baseline eval run.

**Close condition:** `ops.model_versions` has a row with `model_id='meme_ocr'`, `fingerprint` populated, and every `ops.ingest_steps` row with `step_name='ocr'` after this change references the same fingerprint via `config_hash`.

### 3.5 Ingest step list update

`ops.ingest_steps.step_name` expands to:

```
hash → decode → thumbnail → ocr → caption → embed_text → embed_visual → upsert_pg → upsert_qdrant
```

(Inserts `caption` between `ocr` and `embed_text`. Update `PHASE_0_PLAN.md` §9.3 parity is already aligned via the retrieval plan; the schema allows any string in `step_name` so this is purely an ingest-runner change.)

---

## 4. SigLIP-2 visual leg (blocker **A**) — in parallel

The gateway does not help here. Need actual local weights because encoder loads the model for every image.

### 4.1 Options (pick one)

1. **HF mirror via `HF_ENDPOINT`.** Set `HF_ENDPOINT=https://hf-mirror.com` on the ingest box and re-run `huggingface-cli download google/siglip2-so400m-patch16-384 --local-dir K:\models\video_searcher\siglip2-so400m-patch16-384 --resume-download`. Mirrors are typically faster than direct HF on constrained networks.
2. **Manual download + verify.** Grab the weight files (`model.safetensors` ~2 GB, plus `config.json`, `preprocessor_config.json`, `tokenizer*`) from any machine with bandwidth, copy into `K:\models\video_searcher\siglip2-so400m-patch16-384`, verify hash.
3. **One-time prep container.** Write a `docker compose run --rm model-prep` service that downloads into a named volume that the ingest container mounts read-only afterward.

Whichever option: confirm `vidsearch/query/encoders.py` loads from the local path with `local_files_only=True` and `HF_HUB_OFFLINE=1`. No fall-through to remote at query time.

### 4.2 Fingerprint

Seed `ops.model_versions` with `model_id='siglip2_so400m_patch16_384'`, `purpose='visual_embed'`, `fingerprint=<sha256 of model.safetensors + config.json>`.

**Close condition:** on 5 known memes, `visual` vector dimension is 1152 and norm > 0 (not zero-padded fallback); the encoder runs offline with no network.

---

## 5. Retrieval module finalisation

The code skeleton exists in `vidsearch/query/{retrieve_images,encoders,intent,rerank_images}.py`. What is still needed:

### 5.1 Retrieval legs against `retrieval_text`

Update Qdrant ingest so the `text-dense` and `text-sparse` named vectors are computed from **`retrieval_text`**, not raw `ocr_text_hi`. This is the whole point of the caption-then-retrieve design.

### 5.2 RRF fusion

Use Qdrant's Query API with three `prefetch` clauses per `PHASE_0_RETRIEVAL_PLAN.md` §4:

- `text-dense` prefetch (limit=100, weight by intent)
- `text-sparse` prefetch (limit=100, weight by intent)
- `visual` prefetch (limit=100, weight by intent)
- `fusion: rrf`, `k=60`, final `limit=50`, `with_payload=true`

Intent-conditional weights come from the table in §3.3 of the retrieval plan. If the eval later shows equal weights match or beat intent-conditional, simplify.

### 5.3 Reranker input reconstruction

Per §5 of the retrieval plan:

```
document = f"{template_name}. {caption_literal} {caption_figurative} OCR: {ocr_text_hi}"
```

Truncate to 256 tokens; feed `(query, document)` pairs to jina-reranker-v2-base-multilingual. Return top-10 with `retrieval_score` (pre-rerank fused) and `rerank_score`.

**Close condition:** an `exact_text`, a `semantic_description`, and a `mixed_visual_description` query each return the expected meme in top-10 on a 50-image fixture.

---

## 6. Eval rebuild (blockers **E + F**)

### 6.1 Class rebalance (E)

`vidsearch/eval/queries_memes.yaml` currently has 6 exact / 4 fuzzy / 20 semantic / 10 mixed. Rebalance to:

- 10 `exact_text`
- 10 `fuzzy_text`
- 10 `semantic_description`
- 10 `mixed_visual_description`

Every query text must be authentic — OpenCode eyeballs `data/meme` and writes queries that a user would plausibly type for images that exist there.

### 6.2 Graded qrels (F)

Every query row gets:

```yaml
- text: "..."
  intent: "..."
  targets:
    - image_id: "img_sha256_<...>"         # primary target
      grade: 3
    - image_id: "img_sha256_<...>"         # near-duplicate or same-template
      grade: 2
    - image_id: "img_sha256_<...>"
      grade: 1
```

At minimum, every query has ≥ 1 target with grade 3. Label the top-10 candidates per query after a first retrieval pass — this is `pool-at-10` pooling, standard IR practice.

### 6.3 Runner fix

`vidsearch/eval/runner.py` currently passes `grades: []` into `compute_all_metrics`. It must load qrels from YAML and pass the real grade list. Write the qrels into `eval.qrels` on each run so the Postgres side of `P0-G4` is honest.

**Close condition:** running `python -m vidsearch.eval.runner` produces non-zero metrics on a populated Qdrant; metrics match a hand-worked 3-query fixture.

---

## 7. Ingest → baseline loop (blocker **H**, **I**, **J**)

### 7.1 Small-ingest re-proof (5 images)

After A + B + L + M + N + gateway env fix all land, run the 5-image smoke batch. Assert:

- `pg.count(core.images) == qdrant.count(memes)`
- Every visual vector `norm > 0`
- Every `retrieval_text` non-empty
- `ops.ingest_steps` has one row per canonical step per image, all `done`
- `ops.model_versions` has the four fingerprints (OCR, VLM, BGE-M3, SigLIP-2)

### 7.2 Full `data/meme` ingest (H)

`python -m vidsearch.ingest.images --folder data/meme` end-to-end. Budget ~30–60 min per §7 of the retrieval plan (VLM captioning dominates).

Record end-of-run summary into `docs/decision_log.md` as ADR-N:

```
total_seen, supported, ingested, duplicate, skipped, failed
```

This is the corpus-count baseline. Future runs diff against it.

### 7.3 E2E search proof (I)

Pick 5 known memes from `data/meme`, compose natural-language queries (one per canonical class + one general probe), call `POST /search`, paste the ranked hits into `docs/owui_integration.md` "Verified queries".

### 7.4 OWUI chat-app evidence (P0-G3 hard gate)

Real OWUI chat transcripts showing:

- The operator types a plain-English meme description.
- The tool call fires to `POST /search`.
- The response renders the matching image from `data/meme` **inline** in chat via the markdown image link (`![meme](http://api:8000/thumbnail/{image_id}.webp)`), plus the source path.
- 5 transcripts total — one per class + one probe. Paste into `docs/owui_integration.md` under `## P0-G3 chat-app evidence log`.

Without this, P0-G3 is not closed — the end-user deliverable is literally the chat app, not the backend.

### 7.5 Baseline eval run (P0-G4 hard gate)

Run `python -m vidsearch.eval.runner`. Record to `eval.runs`, `eval.run_results`, `eval.metrics`. Gate thresholds:

- `Recall@10 ≥ 0.90`
- `top_1_hit_rate ≥ 0.70`
- `reranker_uplift_ndcg10 ≥ 0.02`
- No `exact_text` query misses outside top 10

If any threshold misses, tune per retrieval-plan §10 knobs (leg weights, RRF k, reranker template, OCR conf cutoff) and rerun. Do not declare G4 closed on a single under-threshold run.

### 7.6 Delete + backup/restore drill (J, G5)

- `DELETE /image/{image_id}` integration test: PG rows + Qdrant point + MinIO thumbnail all removed for one image.
- Backup: `pg_dump` + Qdrant snapshot + MinIO `mc mirror` to a scratch compose project. Boot the scratch stack. Rerun one known-good search. Confirm the same image comes back.

### 7.7 Integration test (Entry 6 observation)

Add one boot-to-search integration test that spins Postgres + Qdrant + MinIO ephemerally (testcontainers or compose-based), ingests a fixture folder, searches, deletes. This is the one test surface Phase 0 currently lacks.

---

## 8. Critical path (shortest route to a closed Phase 0)

1. **Env fix** (§1) — flip `LITELLM_URL` to `127.0.0.1:4000`. ~5 min.
2. **Gateway-vs-direct decision doc** (§2) — write `docs/MODEL_GATEWAY.md`, annotate retrieval plan §2.4. ~30 min.
3. **Schema migration + gateway aliases + caption.py + OCR gateway swap** (§3) — one PR, blockers B + L + M + N closed. ~1 day.
4. **Eval rebuild** (§6) — can start in parallel with step 3; purely data work. ~half-day.
5. **SigLIP-2 weights** (§4) — in parallel; network-dependent. ~half-day once mirror decided.
6. **Small-ingest re-proof** (§7.1) — ~30 min once 3+4+5 land.
7. **Full `data/meme` ingest** (§7.2) — overnight unattended. Record ADR baseline.
8. **E2E search proof + OWUI transcripts** (§7.3, §7.4) — ~2 hours.
9. **Baseline eval run** (§7.5) — ~30 min; iterate on tuning if thresholds miss.
10. **Delete + backup drill + integration test** (§7.6, §7.7) — ~half-day.
11. **Sign-off on `docs/phase1_short_clips_transition.md`** (P0-G6) — Codex + Claude ADR; ~15 min.

---

## 9. Approval contract for closing Phase 0

Phase 0 is closed when every gate in `PHASE_0_PLAN.md` §11 is green, specifically:

| Gate | Close evidence |
|---|---|
| P0-G1 | Stack boots clean on PG18 + OWUI v0.9.1, env drives `LITELLM_URL`, `docs/MODEL_GATEWAY.md` exists |
| P0-G2 | Full `data/meme` ingest summary in `docs/decision_log.md`; `pg_count == qdrant_count`; all four model fingerprints in `ops.model_versions` |
| P0-G3 | 5 OWUI chat-app transcripts in `docs/owui_integration.md`; every inline image renders; `POST /search` validated in contract tests |
| P0-G4 | Baseline eval metrics in `eval.metrics`; all four threshold rows in §7.5 pass |
| P0-G5 | Delete integration test green; backup/restore drill transcript in `docs/runbook.md` |
| P0-G6 | Codex + Claude ADR on `docs/phase1_short_clips_transition.md` |

When every row has green evidence, OpenCode posts a Phase 0 completion entry to `AGENTS_CONVERSATION.MD` and Phase 1 unlocks.

---

## 10. What is **not** in this residual plan (explicit out-of-scope)

- Phase 1 work (`retrieve_video.py`, dispatcher, video ingest).
- Fine-tuning any model. Zero-shot end-to-end.
- Query-by-image. Phase 0 is text-only query.
- Graph booster, VSS benchmark, CCTV profile — Phase 4 scope.
- Multi-GPU, Helm chart, weekly reranker LoRA loop — Phase 5 scope.

If any of this creeps into Phase 0 work, stop and flag in `AGENTS_CONVERSATION.MD`.

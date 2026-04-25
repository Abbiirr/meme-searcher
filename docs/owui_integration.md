# Open WebUI Integration

## Overview

Open WebUI is the Phase 0 chat UI for local meme search. The repo now provisions a dedicated OWUI `pipe` model named `Meme Search` at container start. That model calls the FastAPI backend's canonical `POST /search` endpoint and returns markdown with inline thumbnails.

This keeps one search contract everywhere:
- FastAPI: `POST /search`
- OWUI `Meme Search` pipe: calls `POST /search`
- No second UI-specific search schema exists

## Setup

1. Start the stack: `docker compose up -d` (see `docs/runbook.md` section 1).
2. Open WebUI is available at `http://127.0.0.1:${OPEN_WEBUI_HOST_PORT}`. In this workspace it is `http://127.0.0.1:3180`.
3. LiteLLM handles model routing at `http://127.0.0.1:4000`.
4. FastAPI meme-search is at `http://127.0.0.1:8000` on the host and `http://api:8000` on the compose network.
5. In the OWUI model picker, select `Meme Search`.

## Repo-managed integration

Provisioning path:
- `docker-compose.yml` mounts `infra/open_webui/` into the `open-webui` container
- `infra/open_webui/start-with-bootstrap.sh` wraps OWUI startup
- `infra/open_webui/provision.py` upserts the `meme_search` pipe into OWUI
- `infra/open_webui/functions/meme_search_pipe.py` is the model code that calls `POST http://api:8000/search`

The pipe extracts the latest user message, sends the canonical request body:

```json
{
  "query": "user natural-language meme description",
  "limit": 5
}
```

and formats the canonical `SearchResponse` into markdown that OWUI renders inline.

Important:
- Use `Meme Search` when you want local meme retrieval.
- Plain chat models like `vision` do not automatically search `data/meme`; they will just answer as LLMs unless separately instructed and wired.

## Search contract

```json
{
  "query": "...",
  "intent": "semantic_description",
  "total_returned": 10,
  "hits": [
    {
      "rank": 1,
      "image_id": "...",
      "source_uri": "data/meme/....jpg",
      "thumbnail_uri": "http://127.0.0.1:8000/thumbnail/<image_id>.webp",
      "ocr_excerpt": "...",
      "retrieval_score": 0.87,
      "rerank_score": 0.93
    }
  ]
}
```

## Inline rendering

OWUI renders hits inline via markdown image links pointing at the host-reachable thumbnail URL, not base64 and not the internal compose hostname. Pattern:

```markdown
![meme](http://127.0.0.1:8000/thumbnail/{image_id}.webp)

[Open full image](http://127.0.0.1:8000/image/{image_id})

Source: `{source_uri}`

Rank `{rank}` · Retrieval `{retrieval_score}` · Rerank `{rerank_score}`
```

Keeping images as URLs preserves the thumbnail cache on MinIO and avoids the 4 MB message-size limits OWUI imposes on base64 payloads.

## Security notes

- Direct Connections must remain disabled per CVE-2025-64496.
- The `POST /search` endpoint is the single canonical contract.
- If OWUI is exposed beyond localhost, put a reverse proxy in front with auth.

## Verified queries

### Query 1 - semantic description

**Query:** `orange food items on a tray`

**Response summary:** top hit returned inline from the local corpus.

- `intent`: `semantic_description`
- `image_id`: `img_6b6a6c8742d0267f7ed54122502239a5aa9c9bf9ef23024e54af5416d64cde87`
- `source_uri`: `data\meme\10933027.png`
- `rank`: `1`
- `retrieval_score`: `0.0328`
- `rerank_score`: `-1.1875`

Rendered markdown from the OWUI pipe:

```markdown
![meme](http://127.0.0.1:8000/thumbnail/img_6b6a6c8742d0267f7ed54122502239a5aa9c9bf9ef23024e54af5416d64cde87.webp)

[Open full image](http://127.0.0.1:8000/image/img_6b6a6c8742d0267f7ed54122502239a5aa9c9bf9ef23024e54af5416d64cde87)

Source: `data\meme\10933027.png`
```

Additional 4 benchmark queries remain to be logged before P0-G3 can fully close.

## P0-G3 chat-app evidence log

Hard gate for Phase 0. This section still needs 5 real OWUI chat transcripts before P0-G3 can close:

1. `exact_text` transcript - pending
2. `fuzzy_text` transcript - pending
3. `semantic_description` transcript - captured below
4. `mixed_visual_description` transcript - pending
5. General probe - pending

### Transcript 1 - semantic_description

**Query:** `orange food items on a tray`

**Intent classified as:** `semantic_description`

**Top hit:**

![meme](http://127.0.0.1:8000/thumbnail/img_6b6a6c8742d0267f7ed54122502239a5aa9c9bf9ef23024e54af5416d64cde87.webp)

- `image_id`: `img_6b6a6c8742d0267f7ed54122502239a5aa9c9bf9ef23024e54af5416d64cde87`
- `source_uri`: `data\meme\10933027.png`
- `rank`: `1`
- `retrieval_score`: `0.0328`
- `rerank_score`: `-1.1875`

**Notes:** This was captured through OWUI's own `POST /api/chat/completions` path against the auto-provisioned `Meme Search` pipe model. The prior failure mode was using the plain `vision` chat model, which does not search `data/meme`.

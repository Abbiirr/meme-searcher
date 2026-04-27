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

The current pipe also renders feedback controls returned by the API:

```markdown
Feedback: [Select](http://127.0.0.1:8000/feedback/confirm/<signed_token>)
| [Reject](http://127.0.0.1:8000/feedback/confirm/<signed_token>)
| [Undo](http://127.0.0.1:8000/feedback/confirm/<signed_token>)

[None of these are correct](http://127.0.0.1:8000/feedback/confirm/<signed_token>)
```

These are confirmation links only. The GET request displays a confirmation page; the write happens through `POST /feedback/judgment` with a CSRF cookie.

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
      "rerank_score": 0.93,
      "impression_id": "...",
      "feedback_select_url": "http://127.0.0.1:8000/feedback/confirm/...",
      "feedback_reject_url": "http://127.0.0.1:8000/feedback/confirm/...",
      "feedback_undo_url": "http://127.0.0.1:8000/feedback/confirm/..."
    }
  ],
  "search_id": "...",
  "feedback_enabled": true,
  "feedback_none_correct_url": "http://127.0.0.1:8000/feedback/confirm/..."
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

Hard gate evidence captured on 2026-04-25 through Open WebUI v0.9.1's own `POST /api/chat/completions` path using model `meme_search`. Signed feedback tokens are redacted below; the live responses rendered `Select`, `Reject`, `Undo`, and `None of these are correct` links.

### Transcript 1 - exact_text

**Query:** `"iterator iterator for loop"`

**Intent classified as:** `exact_text`

**Top hit:**

![meme](http://127.0.0.1:8000/thumbnail/img_b697c4e861980f40c75acb1afaef9dc236ce59f65da420119bc0ded287bd95f5.webp)

- `image_id`: `img_b697c4e861980f40c75acb1afaef9dc236ce59f65da420119bc0ded287bd95f5`
- `source_uri`: `data\meme\Old Memes\OOP Memes\p1upji01q2921.png`
- `rank`: `1`
- `retrieval_score`: `0.0410`
- `rerank_score`: `0.8828`
- `ocr_excerpt`: `Iterator Iterator FOR loop`

Rendered excerpt:

```markdown
Intent: `exact_text`

![meme](http://127.0.0.1:8000/thumbnail/img_b697c4e861980f40c75acb1afaef9dc236ce59f65da420119bc0ded287bd95f5.webp)

[Open full image](http://127.0.0.1:8000/image/img_b697c4e861980f40c75acb1afaef9dc236ce59f65da420119bc0ded287bd95f5)

Source: `data\meme\Old Memes\OOP Memes\p1upji01q2921.png`

Rank `1` | Retrieval `0.0410` | Rerank `0.8828`

OCR: `Iterator Iterator FOR loop`

Feedback: [Select](http://127.0.0.1:8000/feedback/confirm/<signed_token_redacted>) | [Reject](http://127.0.0.1:8000/feedback/confirm/<signed_token_redacted>) | [Undo](http://127.0.0.1:8000/feedback/confirm/<signed_token_redacted>)
```

### Transcript 2 - fuzzy_text

**Query:** `text says something like developers, implementin a complex pattern in their project`

**Intent classified as:** `fuzzy_text`

**Top hit:**

![meme](http://127.0.0.1:8000/thumbnail/img_6455b6f5079ccc97f1c1c1e66e1697663747f77e263bca73806f91313996acfc.webp)

- `image_id`: `img_6455b6f5079ccc97f1c1c1e66e1697663747f77e263bca73806f91313996acfc`
- `source_uri`: `data\meme\Old Memes\OOP Memes\trojan-horse.jpg`
- `rank`: `1`
- `retrieval_score`: `0.0377`
- `rerank_score`: `0.4570`
- `ocr_excerpt`: `DEVELOPERS, IMPLEMENTING A COMPLEX PATTERN IN THEIR PROJECT`

Rendered excerpt:

```markdown
Intent: `fuzzy_text`

![meme](http://127.0.0.1:8000/thumbnail/img_6455b6f5079ccc97f1c1c1e66e1697663747f77e263bca73806f91313996acfc.webp)

[Open full image](http://127.0.0.1:8000/image/img_6455b6f5079ccc97f1c1c1e66e1697663747f77e263bca73806f91313996acfc)

Source: `data\meme\Old Memes\OOP Memes\trojan-horse.jpg`

Rank `1` | Retrieval `0.0377` | Rerank `0.4570`

OCR: `DEVELOPERS, IMPLEMENTING A COMPLEX PATTERN IN THEIR PROJECT`

Feedback: [Select](http://127.0.0.1:8000/feedback/confirm/<signed_token_redacted>) | [Reject](http://127.0.0.1:8000/feedback/confirm/<signed_token_redacted>) | [Undo](http://127.0.0.1:8000/feedback/confirm/<signed_token_redacted>)
```

### Transcript 3 - semantic_description

**Query:** `meme about trying to fit gaming into a busy schedule`

**Intent classified as:** `semantic_description`

**Top hit:**

![meme](http://127.0.0.1:8000/thumbnail/img_6fe40a766062547243f2a6bbdc1eb022d986ca4ea5b06fbedf37c6f3dc842359.webp)

- `image_id`: `img_6fe40a766062547243f2a6bbdc1eb022d986ca4ea5b06fbedf37c6f3dc842359`
- `source_uri`: `data\meme\Old Memes\356427680_579718341005498_8178807524708712011_n.jpg`
- `rank`: `1`
- `retrieval_score`: `0.0328`
- `rerank_score`: `0.5508`
- `ocr_excerpt`: `AW, I CAN'T FIT GAMES INTO MY SCHEDULE...`

Rendered excerpt:

```markdown
Intent: `semantic_description`

![meme](http://127.0.0.1:8000/thumbnail/img_6fe40a766062547243f2a6bbdc1eb022d986ca4ea5b06fbedf37c6f3dc842359.webp)

[Open full image](http://127.0.0.1:8000/image/img_6fe40a766062547243f2a6bbdc1eb022d986ca4ea5b06fbedf37c6f3dc842359)

Source: `data\meme\Old Memes\356427680_579718341005498_8178807524708712011_n.jpg`

Rank `1` | Retrieval `0.0328` | Rerank `0.5508`

Feedback: [Select](http://127.0.0.1:8000/feedback/confirm/<signed_token_redacted>) | [Reject](http://127.0.0.1:8000/feedback/confirm/<signed_token_redacted>) | [Undo](http://127.0.0.1:8000/feedback/confirm/<signed_token_redacted>)
```

### Transcript 4 - mixed_visual_description

**Query:** `image of a screenshot meme about ignoring a bug because fixing it is too costly or complex`

**Intent classified as:** `mixed_visual_description`

**Top hit:**

![meme](http://127.0.0.1:8000/thumbnail/img_ddc1b8be5482d2a1c311670b6f7ba4a145280ba25ac6a96c6d024798791e1733.webp)

- `image_id`: `img_ddc1b8be5482d2a1c311670b6f7ba4a145280ba25ac6a96c6d024798791e1733`
- `source_uri`: `data\meme\457473425_941767084631914_7665414484297322371_n.jpg`
- `rank`: `1`
- `retrieval_score`: `0.0344`
- `rerank_score`: `1.1875`
- `ocr_excerpt`: `Coworker: so how did you solve the bug? Me: Ostrich algorithm...`

Rendered excerpt:

```markdown
Intent: `mixed_visual_description`

![meme](http://127.0.0.1:8000/thumbnail/img_ddc1b8be5482d2a1c311670b6f7ba4a145280ba25ac6a96c6d024798791e1733.webp)

[Open full image](http://127.0.0.1:8000/image/img_ddc1b8be5482d2a1c311670b6f7ba4a145280ba25ac6a96c6d024798791e1733)

Source: `data\meme\457473425_941767084631914_7665414484297322371_n.jpg`

Rank `1` | Retrieval `0.0344` | Rerank `1.1875`

Feedback: [Select](http://127.0.0.1:8000/feedback/confirm/<signed_token_redacted>) | [Reject](http://127.0.0.1:8000/feedback/confirm/<signed_token_redacted>) | [Undo](http://127.0.0.1:8000/feedback/confirm/<signed_token_redacted>)
```

### Transcript 5 - general probe

**Query:** `orange food items on a tray`

**Intent classified as:** `semantic_description`

**Top hit:**

![meme](http://127.0.0.1:8000/thumbnail/img_673c8453419792af3c09d09b93fb385b85cba687454a6cc8f6124e153509042a.webp)

- `image_id`: `img_673c8453419792af3c09d09b93fb385b85cba687454a6cc8f6124e153509042a`
- `source_uri`: `data\meme\Old Memes\Old Memes\95603182_3256961271028960_3507566925829898240_o.jpg`
- `rank`: `1`
- `retrieval_score`: `0.0324`
- `rerank_score`: `-0.1816`
- `ocr_excerpt`: `Doner ORANGEN-MILCH`

Rendered excerpt:

```markdown
Intent: `semantic_description`

![meme](http://127.0.0.1:8000/thumbnail/img_673c8453419792af3c09d09b93fb385b85cba687454a6cc8f6124e153509042a.webp)

[Open full image](http://127.0.0.1:8000/image/img_673c8453419792af3c09d09b93fb385b85cba687454a6cc8f6124e153509042a)

Source: `data\meme\Old Memes\Old Memes\95603182_3256961271028960_3507566925829898240_o.jpg`

Rank `1` | Retrieval `0.0324` | Rerank `-0.1816`

Feedback: [Select](http://127.0.0.1:8000/feedback/confirm/<signed_token_redacted>) | [Reject](http://127.0.0.1:8000/feedback/confirm/<signed_token_redacted>) | [Undo](http://127.0.0.1:8000/feedback/confirm/<signed_token_redacted>)

[None of these are correct](http://127.0.0.1:8000/feedback/confirm/<signed_token_redacted>)
```

**Notes:** The prior failure mode was using a plain chat model such as `vision`, which does not search `data/meme`. These transcripts use the dedicated `Meme Search` pipe and prove the UI path returns local images, source paths, and feedback controls.

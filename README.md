# video_searcher

Local-first multimodal search engine. Phase 0 builds a meme search engine over `data/meme`.

## Authoritative Phase 0 docs

- [`docs/PHASE_0_PLAN.md`](docs/PHASE_0_PLAN.md) - architecture and scope
- [`docs/PHASE_0_TODO.md`](docs/PHASE_0_TODO.md) - checklist and gates

## Quick start

1. Copy `.env.example` to `.env` and fill in values
2. Set `LITELLM_URL` in `.env` to your active LLM API gateway host
3. `docker compose up -d`
4. Wait for all services healthy: `docker compose ps`
5. Bootstrap Qdrant: `python -m infra.qdrant.bootstrap`
6. Ingest the meme corpus: `python -m vidsearch.ingest.images --folder data/meme`
7. Search the API: `curl -X POST http://localhost:8000/search -H "Content-Type: application/json" -d '{"query":"drake meme about code review"}'`
8. Search the UI: open `http://127.0.0.1:3180`, select the `Meme Search` model, and describe the meme you want

## Requirements

- Docker Desktop
- Python 3.12+
- Local models at `K:\models\video_searcher` (run `download-models.ps1` to fetch)

## Local model endpoints

- Ollama is expected at `http://127.0.0.1:11434`
- The local Qwen3.6 llama.cpp server is expected at `http://127.0.0.1:8080`
- The local LiteLLM gateway is expected at `http://127.0.0.1:4000`
- The local LiteLLM gateway may expose additional models beyond the raw Ollama and llama.cpp endpoints
- `.env.example` exposes `LITELLM_URL`, `LITELLM_MASTER_KEY`, `OLLAMA_API_BASE`, `QWEN36_VLM_API_BASE`, `QWEN36_VLM_API_KEY`, and `QWEN36_VLM_MODEL`
- `docker-compose.yml` now reads the LiteLLM gateway host from `LITELLM_URL` in `.env` without a hardcoded fallback
- Open WebUI auto-provisions a `Meme Search` pipe model that calls the local FastAPI `POST /search` endpoint
- `infra/litellm/config.yaml` includes:
  - `local_qwen36_vlm`
  - `local_ollama_glm_ocr`
  - `local_ollama_nomic_embed`

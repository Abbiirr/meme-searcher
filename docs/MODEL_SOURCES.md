# Model Sources

Model root: `K:\models\video_searcher`

Core models:

- `nvidia/parakeet-tdt-0.6b-v3` -> `K:\models\video_searcher\asr\parakeet-tdt-0.6b-v3`
- `Systran/faster-whisper-large-v3` -> `K:\models\video_searcher\asr\faster-whisper-large-v3`
- `BAAI/bge-m3` -> `K:\models\video_searcher\embeddings\bge-m3`
- `google/siglip2-so400m-patch16-384` -> `K:\models\video_searcher\embeddings\siglip2-so400m-patch16-384`
- `jinaai/jina-reranker-v2-base-multilingual` -> `K:\models\video_searcher\embeddings\jina-reranker-v2-base-multilingual`
- `PaddlePaddle/PP-OCRv5_server_det` -> `K:\models\video_searcher\ocr\PP-OCRv5_server_det`
- `PaddlePaddle/PP-OCRv5_server_rec` -> `K:\models\video_searcher\ocr\PP-OCRv5_server_rec`
- `Qwen/Qwen3-VL-8B-Instruct-GGUF` -> `K:\models\video_searcher\vlm\Qwen3-VL-8B-Instruct-GGUF`

Qwen files to keep:

- `Qwen3VL-8B-Instruct-Q4_K_M.gguf`
- `mmproj-Qwen3VL-8B-Instruct-F16.gguf`

Installed local tools:

- `ffmpeg`
- `git-lfs`
- `hf`
- `yt-dlp`
- `aria2c`
- `llama.cpp` Windows binaries

Repeatable pull command:

```powershell
powershell -ExecutionPolicy Bypass -File K:\projects\video_searcher\download-models.ps1
```

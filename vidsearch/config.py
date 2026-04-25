import logging
import os
from pathlib import Path


def _load_dotenv_defaults(dotenv_path: Path | None = None) -> dict[str, str]:
    """Load repo-local `.env` values without overriding explicit env vars."""
    path = dotenv_path or Path(__file__).resolve().parents[1] / ".env"
    loaded: dict[str, str] = {}
    if not path.exists():
        return loaded

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key.startswith("#"):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if key not in os.environ:
            os.environ[key] = value
            loaded[key] = value
    return loaded

from vidsearch.logging_utils import setup_logging

_LOADED_DOTENV_DEFAULTS = _load_dotenv_defaults()
setup_logging()
logger = logging.getLogger("vidsearch")


def _bool_env(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default).lower()).lower() == "true"


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        logger.warning("invalid integer for %s=%r, using default=%s", name, os.environ.get(name), default)
        return default

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://vidsearch:vidsearch@localhost:5432/vidsearch")
QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")
MINIO_ROOT_USER = os.environ.get("MINIO_ROOT_USER", "minioadmin")
MINIO_ROOT_PASSWORD = os.environ.get("MINIO_ROOT_PASSWORD", "minioadmin")
MINIO_BUCKET_THUMBNAILS = os.environ.get("MINIO_BUCKET_THUMBNAILS", "thumbnails")
MINIO_ENDPOINT = os.environ.get("MINIO_ENDPOINT", "localhost:9000")
MINIO_SECURE = os.environ.get("MINIO_SECURE", "false").lower() == "true"
MODEL_ROOT = os.environ.get("VIDSEARCH_MODEL_ROOT", r"K:\models\video_searcher")
DATA_ROOT = os.environ.get("VIDSEARCH_DATA_ROOT", r"K:\projects\video_searcher\data")
ENABLE_CAPTIONS = _bool_env("VIDSEARCH_ENABLE_CAPTIONS", True)
ENABLE_VISUAL_QUERY = _bool_env("VIDSEARCH_ENABLE_VISUAL_QUERY", False)
PREWARM_RETRIEVAL = _bool_env("VIDSEARCH_PREWARM_RETRIEVAL", False)
PUBLIC_BASE_URL = os.environ.get("VIDSEARCH_PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
RERANK_TOP_K_EXACT = _int_env("VIDSEARCH_RERANK_TOP_K_EXACT", 10)
RERANK_TOP_K_FUZZY = _int_env("VIDSEARCH_RERANK_TOP_K_FUZZY", 10)
RERANK_TOP_K_SEMANTIC = _int_env("VIDSEARCH_RERANK_TOP_K_SEMANTIC", 10)
RERANK_TOP_K_MIXED = _int_env("VIDSEARCH_RERANK_TOP_K_MIXED", 12)

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".jfif"}
SKIPPED_EXTENSIONS = {".avif", ".heic", ".svg", ".pdf", ".html", ".js", ".mp3", ".mp4", ".mkv", ".zip"}

MEME_COLLECTION = "memes"
MEME_COLLECTION_V1 = "memes_v1"

TEXT_DENSE_DIM = 1024
VISUAL_DIM = 1152
THUMBNAIL_MAX_SIZE = 512
OCR_CONFIDENCE_THRESHOLD = 0.6

from __future__ import annotations

import base64
import hashlib
import logging
import os
from pathlib import Path

from PIL import Image

logger = logging.getLogger(__name__)

_DATA_URL_MIME_BY_SUFFIX = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".jfif": "image/jpeg",
}
_FILE_MODE_NATIVE_SUFFIXES = {".jpg", ".jpeg", ".png"}
_FILE_MODE_CACHE_DIR = ".vidsearch_media_cache"


def image_to_data_url(path: str | Path) -> str:
    path = Path(path)
    mime = _DATA_URL_MIME_BY_SUFFIX.get(path.suffix.lower(), "application/octet-stream")
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _cached_png_path(path: Path, data_root: Path) -> Path:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return data_root / _FILE_MODE_CACHE_DIR / f"{digest}.png"


def _ensure_file_mode_png(path: Path, data_root: Path, component: str) -> Path:
    cache_path = _cached_png_path(path, data_root)
    if cache_path.exists():
        return cache_path

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(path) as image:
        if image.mode not in ("RGB", "RGBA", "L", "LA"):
            image = image.convert("RGBA")
        image.save(cache_path, format="PNG")
    logger.info("%s file media mode transcoded %s -> %s", component, path, cache_path)
    return cache_path


def image_request_url(path: str | Path, *, component: str) -> str:
    path = Path(path)
    mode = os.environ.get("VIDSEARCH_MEDIA_URL_MODE", "data").strip().lower()
    if mode == "file":
        data_root = os.environ.get("VIDSEARCH_DATA_ROOT", "").strip()
        if data_root:
            data_root_path = Path(data_root).resolve()
            try:
                resolved = path.resolve()
                if resolved.suffix.lower() not in _FILE_MODE_NATIVE_SUFFIXES:
                    resolved = _ensure_file_mode_png(resolved, data_root_path, component)
                relative = resolved.relative_to(data_root_path)
                return f"file://{relative.as_posix()}"
            except ValueError:
                logger.warning(
                    "%s file media mode could not relativize %s under %s; falling back to data URL",
                    component,
                    path,
                    data_root,
                )
        else:
            logger.warning(
                "%s file media mode enabled without VIDSEARCH_DATA_ROOT; falling back to data URL",
                component,
            )
    return image_to_data_url(path)

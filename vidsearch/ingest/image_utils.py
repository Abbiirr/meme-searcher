import io
import logging
from pathlib import Path

from PIL import Image

from vidsearch.config import THUMBNAIL_MAX_SIZE

logger = logging.getLogger(__name__)


def decode_image(path: str | Path) -> tuple[Image.Image, int, int, str]:
    path = Path(path)
    ext = path.suffix.lower()

    if ext == ".gif":
        img = Image.open(path)
        img.seek(0)
        img = img.convert("RGB")
    elif ext in (".jfif", ".jpg", ".jpeg"):
        img = Image.open(path).convert("RGB")
    else:
        img = Image.open(path).convert("RGB")

    width, height = img.size

    if ext in (".jpg", ".jpeg", ".jfif"):
        fmt = "jpeg"
    elif ext == ".png":
        fmt = "png"
    elif ext == ".webp":
        fmt = "webp"
    elif ext == ".gif":
        fmt = "gif"
    else:
        fmt = "unknown"

    return img, width, height, fmt


def generate_thumbnail(img: Image.Image, max_size: int = THUMBNAIL_MAX_SIZE) -> bytes:
    width, height = img.size
    if width > height:
        new_width = max_size
        new_height = int(height * max_size / width)
    else:
        new_height = max_size
        new_width = int(width * max_size / height)

    thumb = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    thumb.save(buf, format="WEBP", quality=80, method=6)
    return buf.getvalue()

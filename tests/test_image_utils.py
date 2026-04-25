import io
from PIL import Image

from vidsearch.ingest.image_utils import decode_image, generate_thumbnail


def test_generate_thumbnail_jpeg():
    img = Image.new("RGB", (1000, 500), color="red")
    data = generate_thumbnail(img, max_size=512)
    thumb = Image.open(io.BytesIO(data))
    assert max(thumb.size) == 512
    assert thumb.format == "WEBP"


def test_generate_thumbnail_tall():
    img = Image.new("RGB", (300, 1200), color="blue")
    data = generate_thumbnail(img, max_size=512)
    thumb = Image.open(io.BytesIO(data))
    assert max(thumb.size) == 512


def test_decode_image_jpg(tmp_path):
    img = Image.new("RGB", (100, 100), color="green")
    path = tmp_path / "test.jpg"
    img.save(path, format="JPEG")
    decoded, w, h, fmt = decode_image(path)
    assert w == 100
    assert h == 100
    assert fmt == "jpeg"


def test_decode_image_png(tmp_path):
    img = Image.new("RGB", (50, 75), color="blue")
    path = tmp_path / "test.png"
    img.save(path, format="PNG")
    decoded, w, h, fmt = decode_image(path)
    assert w == 50
    assert h == 75
    assert fmt == "png"

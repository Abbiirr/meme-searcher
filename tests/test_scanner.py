import tempfile
from pathlib import Path

from vidsearch.ingest.scanner import scan_corpus


def test_scan_classifies_supported():
    with tempfile.TemporaryDirectory() as tmpdir:
        (Path(tmpdir) / "cat.jpg").write_bytes(b"\xff\xd8\xff")
        (Path(tmpdir) / "dog.png").write_bytes(b"\x89PNG")
        (Path(tmpdir) / "photo.webp").write_bytes(b"RIFF")
        result = scan_corpus(tmpdir)
        assert len(result.supported) == 3
        assert len(result.skipped_unsupported) == 0
        assert len(result.skipped_no_extension) == 0


def test_scan_skips_unsupported():
    with tempfile.TemporaryDirectory() as tmpdir:
        (Path(tmpdir) / "video.mp4").write_bytes(b"\x00")
        (Path(tmpdir) / "audio.mp3").write_bytes(b"\x00")
        (Path(tmpdir) / "doc.pdf").write_bytes(b"\x00")
        result = scan_corpus(tmpdir)
        assert len(result.supported) == 0
        assert len(result.skipped_unsupported) == 3


def test_scan_no_extension():
    with tempfile.TemporaryDirectory() as tmpdir:
        (Path(tmpdir) / "Makefile").write_bytes(b"\x00")
        result = scan_corpus(tmpdir)
        assert len(result.skipped_no_extension) == 1


def test_scan_recursive():
    with tempfile.TemporaryDirectory() as tmpdir:
        sub = Path(tmpdir) / "subdir"
        sub.mkdir()
        (sub / "nested.jpg").write_bytes(b"\xff\xd8\xff")
        result = scan_corpus(tmpdir)
        assert len(result.supported) == 1


def test_scan_jfif_supported():
    with tempfile.TemporaryDirectory() as tmpdir:
        (Path(tmpdir) / "image.jfif").write_bytes(b"\xff\xd8\xff")
        result = scan_corpus(tmpdir)
        assert len(result.supported) == 1


def test_scan_gif_supported():
    with tempfile.TemporaryDirectory() as tmpdir:
        (Path(tmpdir) / "anim.gif").write_bytes(b"GIF89a")
        result = scan_corpus(tmpdir)
        assert len(result.supported) == 1

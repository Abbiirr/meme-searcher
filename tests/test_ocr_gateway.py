from vidsearch.ingest import ocr


def test_sanitize_gateway_line_drops_user_prompt_echo():
    assert ocr._sanitize_gateway_line("**User:** extract every piece of text visible") is None


def test_sanitize_gateway_line_keeps_assistant_payload():
    assert ocr._sanitize_gateway_line("Assistant: hello world") == "hello world"


def test_sanitize_gateway_line_drops_no_text_markers():
    assert ocr._sanitize_gateway_line("The image contains no text.") is None
    assert ocr._sanitize_gateway_line("No visible text") is None


def test_parse_gateway_lines_filters_wrapper_noise():
    boxes = ocr._parse_gateway_lines(
        "\n".join(
            [
                "**User:** extract every piece of text visible",
                "Assistant: first line",
                "The image contains no text.",
                "second line",
            ]
        )
    )

    assert [box["text"] for box in boxes] == ["first line", "second line"]
    assert all(box["conf"] == 1.0 for box in boxes)


def test_image_request_url_uses_file_mode_when_path_is_under_data_root(monkeypatch, tmp_path):
    data_root = tmp_path / "data"
    image_path = data_root / "meme" / "ocr.png"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"png")

    monkeypatch.setenv("VIDSEARCH_MEDIA_URL_MODE", "file")
    monkeypatch.setenv("VIDSEARCH_DATA_ROOT", str(data_root))

    assert ocr._image_request_url(image_path) == "file://meme/ocr.png"


def test_image_request_url_transcodes_webp_to_cached_png_in_file_mode(monkeypatch, tmp_path):
    from PIL import Image

    data_root = tmp_path / "data"
    image_path = data_root / "meme" / "ocr.webp"
    image_path.parent.mkdir(parents=True)
    Image.new("RGB", (8, 8), "blue").save(image_path, format="WEBP")

    monkeypatch.setenv("VIDSEARCH_MEDIA_URL_MODE", "file")
    monkeypatch.setenv("VIDSEARCH_DATA_ROOT", str(data_root))

    url = ocr._image_request_url(image_path)

    assert url.startswith("file://.vidsearch_media_cache/")
    cache_path = data_root / url.replace("file://", "").replace("/", "\\")
    assert cache_path.exists()
    assert cache_path.suffix.lower() == ".png"

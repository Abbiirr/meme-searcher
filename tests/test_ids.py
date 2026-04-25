import hashlib

from vidsearch.ids import image_id


def test_identical_bytes_same_id():
    data = b"hello world"
    assert image_id(data) == image_id(data)


def test_different_bytes_different_id():
    assert image_id(b"hello") != image_id(b"world")


def test_image_id_starts_with_prefix():
    assert image_id(b"test").startswith("img_")


def test_image_id_is_sha256():
    data = b"test data"
    expected = "img_" + hashlib.sha256(data).hexdigest()
    assert image_id(data) == expected

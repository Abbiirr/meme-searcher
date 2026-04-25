import hashlib


def image_id(raw_bytes: bytes) -> str:
    return "img_" + hashlib.sha256(raw_bytes).hexdigest()

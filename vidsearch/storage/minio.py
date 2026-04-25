import io
import logging

from minio import Minio
from minio.error import S3Error

from vidsearch.config import (
    MINIO_ROOT_USER, MINIO_ROOT_PASSWORD, MINIO_BUCKET_THUMBNAILS,
    MINIO_ENDPOINT, MINIO_SECURE,
)

logger = logging.getLogger(__name__)

_client = None


def get_client() -> Minio:
    global _client
    if _client is None:
        _client = Minio(
            MINIO_ENDPOINT,
            access_key=MINIO_ROOT_USER,
            secret_key=MINIO_ROOT_PASSWORD,
            secure=MINIO_SECURE,
        )
    return _client


def ensure_bucket(bucket: str | None = None) -> None:
    bucket = bucket or MINIO_BUCKET_THUMBNAILS
    client = get_client()
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)
        logger.info("created bucket: %s", bucket)


def upload_thumbnail(image_id: str, data: bytes, content_type: str = "image/webp") -> str:
    bucket = MINIO_BUCKET_THUMBNAILS
    ensure_bucket(bucket)
    prefix = image_id[:4]
    key = f"{prefix}/{image_id}.webp"
    client = get_client()
    client.put_object(
        bucket, key, io.BytesIO(data), len(data),
        content_type=content_type,
    )
    return f"minio://{bucket}/{key}"


def download_thumbnail(thumbnail_uri: str) -> bytes:
    parts = thumbnail_uri.replace("minio://", "").split("/", 1)
    bucket, key = parts[0], parts[1]
    client = get_client()
    resp = client.get_object(bucket, key)
    data = resp.read()
    resp.close()
    resp.release_conn()
    return data


def delete_object(uri: str) -> None:
    parts = uri.replace("minio://", "").split("/", 1)
    bucket, key = parts[0], parts[1]
    client = get_client()
    client.remove_object(bucket, key)

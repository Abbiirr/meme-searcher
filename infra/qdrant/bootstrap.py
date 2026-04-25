import sys
import logging

from qdrant_client import QdrantClient, models

from vidsearch.config import QDRANT_URL, MEME_COLLECTION, MEME_COLLECTION_V1, TEXT_DENSE_DIM, VISUAL_DIM

logger = logging.getLogger(__name__)


def bootstrap_qdrant(url: str | None = None) -> None:
    client = QdrantClient(url=url or QDRANT_URL)

    existing = [c.name for c in client.get_collections().collections]
    if MEME_COLLECTION_V1 in existing:
        logger.info("collection %s already exists, skipping creation", MEME_COLLECTION_V1)
    else:
        logger.info("creating collection %s", MEME_COLLECTION_V1)
        client.create_collection(
            MEME_COLLECTION_V1,
            vectors_config={
                "text-dense": models.VectorParams(
                    size=TEXT_DENSE_DIM,
                    distance=models.Distance.COSINE,
                    on_disk=True,
                ),
                "visual": models.VectorParams(
                    size=VISUAL_DIM,
                    distance=models.Distance.COSINE,
                    on_disk=True,
                ),
            },
            sparse_vectors_config={
                "text-sparse": models.SparseVectorParams(
                    modifier=models.Modifier.IDF,
                    index=models.SparseIndexParams(on_disk=True),
                ),
            },
            on_disk_payload=True,
            optimizers_config=models.OptimizersConfigDiff(
                default_segment_number=2,
                indexing_threshold=20000,
            ),
        )
        logger.info("collection %s created", MEME_COLLECTION_V1)

    payload_indexes = [
        ("image_id", models.PayloadSchemaType.KEYWORD),
        ("source_uri", models.PayloadSchemaType.KEYWORD),
        ("format", models.PayloadSchemaType.KEYWORD),
        ("width", models.PayloadSchemaType.INTEGER),
        ("height", models.PayloadSchemaType.INTEGER),
        ("has_ocr", models.PayloadSchemaType.BOOL),
        ("has_caption", models.PayloadSchemaType.BOOL),
        ("ingested_at", models.PayloadSchemaType.INTEGER),
        ("model_version", models.PayloadSchemaType.KEYWORD),
    ]
    for field_name, field_type in payload_indexes:
        try:
            client.create_payload_index(MEME_COLLECTION_V1, field_name, field_type)
            logger.info("created payload index: %s", field_name)
        except Exception:
            pass

    aliases = client.get_collection_aliases(MEME_COLLECTION_V1).aliases
    alias_names = [a.alias_name for a in aliases]
    if MEME_COLLECTION not in alias_names:
        logger.info("creating alias %s -> %s", MEME_COLLECTION, MEME_COLLECTION_V1)
        client.update_collection_aliases(
            change_aliases_operations=[
                models.CreateAliasOperation(
                    create_alias=models.CreateAlias(
                        collection_name=MEME_COLLECTION_V1,
                        alias_name=MEME_COLLECTION,
                    )
                )
            ]
        )
    else:
        logger.info("alias %s already exists", MEME_COLLECTION)

    logger.info("qdrant bootstrap complete")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    bootstrap_qdrant()

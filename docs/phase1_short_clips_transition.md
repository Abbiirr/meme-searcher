# Phase 1 Transition: Short Clips

## Phase 0 modules that carry over unchanged

These modules are designed to be reusable for Phase 1 without modification:

- `vidsearch/ids.py` - SHA-256 identity function (Phase 1 extends with segment IDs)
- `vidsearch/query/encoders.py` - BGE-M3 and SigLIP-2 encoding (same models)
- `vidsearch/query/retrieve_images.py` - hybrid retrieval for images; **stays untouched in Phase 1** (video retrieval lands as a separate `retrieve_video.py` module, not by extending this file)
- `vidsearch/query/rerank_images.py` - cross-encoder reranking (same model; video rerank will be a sibling `rerank_video.py` or a generic `rerank.py` wrapper — decision deferred to the Phase 1 PR)
- `vidsearch/api/contracts.py` - search response schema (extended with segment fields)
- `vidsearch/storage/pg.py` - Postgres operations (new tables added)
- `vidsearch/storage/qdrant.py` - Qdrant operations (new vectors added)
- `vidsearch/storage/minio.py` - MinIO operations (same pattern)

## Phase 1 deltas

Phase 1 adds video-specific code on top of the Phase 0 foundation:

1. **Segmentation** - `vidsearch/ingest/segmentation/` with shot detection and window slicing
2. **ASR** - `vidsearch/ingest/asr/` for audio transcription
3. **Video keyframes** - frame extraction from video segments
4. **Segment ID scheme** - content-addressed UUIDs based on video hash + time bounds
5. **New tables** - `core.videos`, `core.segments` alongside existing `core.images`
6. **Temporal queries** - time-range filters and sequence-aware retrieval
7. **Group-by video** - Qdrant `group_by=video_id` to prevent single-video dominance
8. **Retrieval dispatcher** - new `vidsearch/query/retrieve_video.py` mirrors the shape of `retrieve_images.py`; a thin `vidsearch/query/retrieve.py` dispatches on the `media` query param (`image` | `video` | unset → both). Phase 0's `retrieve_images.py` is imported by the dispatcher, not modified.

## No video code in Phase 0

Phase 0 does not contain any video-specific code. The meme searcher works standalone without any video imports.

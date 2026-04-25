"""Model-version fingerprint seeding.

Implements the recipes from `docs/MODEL_GATEWAY.md` §4:

    - Gateway-routed models      → fingerprint = sha256(alias
                                                + upstream_id
                                                + api_base
                                                + gateway_build
                                                + model_record)[:16]
    - Direct-local models        → fingerprint = sha256(safetensors
                                                + config.json
                                                + preprocessor_config.json)[:16]

Extracted out of `vidsearch.ingest.images` so both the ingest batch and the
FastAPI startup hook can call it without introducing a circular import
(api → ingest → api).

Every function in this module is non-fatal: a missing gateway, missing
local weights, or an unwritable Postgres all result in a logged warning
and `None` return. The retrieval stack can still operate on an
already-ingested corpus even if fingerprinting fails — this is
bookkeeping, not a hot path.
"""

from __future__ import annotations

import json
import hashlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path

import requests

from vidsearch.config import MODEL_ROOT

try:
    from vidsearch.storage import pg as pg_store
except ModuleNotFoundError:  # pragma: no cover - exercised in lean test envs
    pg_store = None

logger = logging.getLogger(__name__)
_seed_cache_signature: tuple[str, tuple[tuple[str, str, str, str, str | None], ...], str] | None = None
_seed_cache_results: dict[str, str | None] | None = None


# ---------------------------------------------------------------------------
# Canonical model rows (mirrors docs/MODEL_GATEWAY.md §4 table)
# ---------------------------------------------------------------------------

# (model_key, family, version, rev_source, local_subpath)
#   rev_source ∈ {"gateway", "local"}
#   local_subpath is relative to MODEL_ROOT; only used when rev_source='local'
def _model_rows() -> list[tuple[str, str, str, str, str | None]]:
    return [
        (
            "meme_vlm_captioner",
            "litellm-gateway",
            os.environ.get("VIDSEARCH_CAPTION_MODEL", "vision"),
            "gateway",
            None,
        ),
        (
            "meme_ocr",
            "litellm-gateway",
            os.environ.get("VIDSEARCH_OCR_MODEL", "glm-ocr-wrapper"),
            "gateway",
            None,
        ),
        ("meme_synthesis", "litellm-gateway", "fast", "gateway", None),
        ("meme_controller", "litellm-gateway", "thinking", "gateway", None),
        ("text_dense", "bge-m3", "BAAI/bge-m3", "local", "embeddings/bge-m3"),
        ("text_sparse", "bge-m3", "BAAI/bge-m3", "local", "embeddings/bge-m3"),
        ("visual", "siglip2", "google/siglip2-so400m-patch16-384", "local", "embeddings/siglip2-so400m-patch16-384"),
        (
            "reranker",
            "jina-reranker",
            "jinaai/jina-reranker-v2-base-multilingual",
            "local",
            "embeddings/jina-reranker-v2-base-multilingual",
        ),
    ]

POINT_MODEL_KEYS: tuple[str, ...] = (
    "text_dense",
    "text_sparse",
    "visual",
    "meme_vlm_captioner",
    "meme_ocr",
)


# ---------------------------------------------------------------------------
# Gateway fingerprint
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GatewayState:
    api_base: str
    build_revision: str
    model_records: dict[str, dict]


def _json_sha256(payload: object) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def _fetch_json(url: str, headers: dict[str, str], timeout: int = 5) -> tuple[requests.Response, object] | None:
    try:
        resp = requests.get(url, headers=headers, timeout=timeout)
    except requests.RequestException as e:
        logger.warning("gateway probe failed for %s: %s", url, e)
        return None
    if not resp.ok:
        logger.warning("gateway probe %s returned HTTP %s", url, resp.status_code)
        return None
    try:
        return resp, resp.json()
    except ValueError as e:
        logger.warning("gateway probe %s returned invalid JSON: %s", url, e)
        return None


def _record_identifier(record: dict) -> str | None:
    for key in ("id", "model_name", "name"):
        value = record.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def fetch_gateway_state() -> GatewayState | None:
    """Fetch model inventory and build metadata from the LiteLLM gateway.

    Returns None on any failure — missing key, network error, invalid JSON,
    or a malformed `/v1/models` payload.
    """
    gw_url = os.environ.get("LITELLM_URL", "http://127.0.0.1:4000").rstrip("/")
    gw_key = os.environ.get("LITELLM_MASTER_KEY", "")
    if not gw_key:
        logger.info("LITELLM_MASTER_KEY not set; skipping gateway state probe")
        return None
    headers = {"Authorization": f"Bearer {gw_key}"}

    models_probe = _fetch_json(f"{gw_url}/v1/models", headers=headers)
    if models_probe is None:
        return None
    models_resp, models_payload = models_probe
    if not isinstance(models_payload, dict):
        logger.warning("gateway /v1/models payload is not a JSON object")
        return None
    raw_records = models_payload.get("data")
    if not isinstance(raw_records, list):
        logger.warning("gateway /v1/models payload missing data[]")
        return None

    model_records: dict[str, dict] = {}
    for raw in raw_records:
        if not isinstance(raw, dict):
            continue
        record_id = _record_identifier(raw)
        if record_id:
            model_records[record_id] = raw

    readiness_probe = _fetch_json(f"{gw_url}/health/readiness", headers=headers)
    if readiness_probe is not None:
        _, readiness_payload = readiness_probe
        if isinstance(readiness_payload, dict):
            build_revision = next(
                (
                    str(readiness_payload[key])
                    for key in ("version", "litellm_version")
                    if readiness_payload.get(key)
                ),
                "",
            )
            if build_revision:
                return GatewayState(
                    api_base=gw_url,
                    build_revision=build_revision,
                    model_records=model_records,
                )

    return GatewayState(
        api_base=gw_url,
        build_revision=hashlib.sha256(models_resp.content).hexdigest()[:16],
        model_records=model_records,
    )


def compute_gateway_fingerprint(
    model_key: str,
    upstream_id: str,
    gateway_state: GatewayState | None = None,
) -> str | None:
    """Return a deterministic revision for one gateway-routed model alias."""
    state = gateway_state or fetch_gateway_state()
    if state is None:
        return None
    model_record = state.model_records.get(upstream_id)
    if model_record is None:
        logger.warning("gateway model inventory missing upstream id %s", upstream_id)
        return None
    payload = {
        "alias": model_key,
        "upstream_id": upstream_id,
        "api_base": state.api_base,
        "gateway_build_sha": state.build_revision,
        "model_record": model_record,
    }
    return _json_sha256(payload)[:16]


# ---------------------------------------------------------------------------
# Direct-local fingerprint
# ---------------------------------------------------------------------------

# The files we hash for a direct-local model. Order matters — the hash
# commutes only when the same files are hashed in the same order. We sort
# then hash so the result is stable across file-system ordering quirks.
_LOCAL_FP_FILES: tuple[str, ...] = (
    "model.safetensors",
    "config.json",
    "preprocessor_config.json",
)


def _hash_file(path: Path, chunk_size: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def compute_local_fingerprint(model_dir: Path | str) -> str | None:
    """Fingerprint a local model directory.

    Hashes the concatenation of per-file sha256s for whichever of
    `model.safetensors`, `config.json`, `preprocessor_config.json` are
    present. Missing files are skipped (not all models ship a preprocessor
    config) but `config.json` is required — without it we cannot claim a
    valid model revision and return None.

    Returns the first 16 hex chars so the column can stay short and the
    Qdrant payload's `model_version` dict doesn't balloon.
    """
    model_dir = Path(model_dir)
    if not (model_dir / "config.json").exists():
        logger.info("local fingerprint: %s missing config.json; skipping", model_dir)
        return None

    rolling = hashlib.sha256()
    present: list[str] = []
    for name in _LOCAL_FP_FILES:
        p = model_dir / name
        if not p.exists():
            continue
        digest = _hash_file(p)
        rolling.update(digest.encode("ascii"))
        rolling.update(b"\x00")  # domain separator
        present.append(f"{name}={digest[:8]}")
    logger.debug("local fingerprint for %s: %s", model_dir, present)
    return rolling.hexdigest()[:16]


def build_point_model_versions(
    revisions: dict[str, str | None],
    *,
    has_text_dense: bool,
    has_text_sparse: bool,
    has_visual: bool,
    has_caption: bool,
    has_ocr: bool,
) -> dict[str, str]:
    """Build the Qdrant payload's model_version dict for one image point."""
    out: dict[str, str] = {}
    if has_text_dense and revisions.get("text_dense"):
        out["text_dense"] = revisions["text_dense"]
    if has_text_sparse and revisions.get("text_sparse"):
        out["text_sparse"] = revisions["text_sparse"]
    if has_visual and revisions.get("visual"):
        out["visual"] = revisions["visual"]
    if has_caption and revisions.get("meme_vlm_captioner"):
        out["meme_vlm_captioner"] = revisions["meme_vlm_captioner"]
    if has_ocr and revisions.get("meme_ocr"):
        out["meme_ocr"] = revisions["meme_ocr"]
    return out


# ---------------------------------------------------------------------------
# Public seed entry point
# ---------------------------------------------------------------------------


def seed_model_versions(model_root: str | Path | None = None) -> dict[str, str | None]:
    """UPSERT rows in `ops.model_versions` for every Phase-0 model.

    Returns a dict of `{model_key: revision_or_None}` for caller
    introspection (tests + logging). Safe to call repeatedly — Postgres
    `UPSERT` semantics on `model_key` mean re-running cannot duplicate.
    """
    model_rows = _model_rows()
    root = Path(model_root) if model_root else Path(MODEL_ROOT)
    cache_signature = (
        str(root.resolve()),
        tuple(model_rows),
        os.environ.get("LITELLM_URL", "http://127.0.0.1:4000").rstrip("/"),
    )
    global _seed_cache_signature, _seed_cache_results
    if _seed_cache_signature == cache_signature and _seed_cache_results is not None:
        logger.debug("reusing cached ops.model_versions seed for %s", root)
        return dict(_seed_cache_results)

    gateway_state = fetch_gateway_state()
    if pg_store is None:
        logger.warning("psycopg unavailable; skipping ops.model_versions seed")
        return {}

    # Cache per-directory fingerprints so bge-m3 isn't hashed twice for
    # text_dense + text_sparse (same directory, same hash).
    local_fp_cache: dict[Path, str | None] = {}

    results: dict[str, str | None] = {}
    try:
        with pg_store.get_cursor() as cur:
            for key, family, version, rev_source, local_subpath in model_rows:
                if rev_source == "gateway":
                    revision = compute_gateway_fingerprint(key, version, gateway_state)
                else:
                    assert local_subpath is not None
                    model_dir = root / local_subpath
                    if model_dir not in local_fp_cache:
                        local_fp_cache[model_dir] = compute_local_fingerprint(model_dir)
                    revision = local_fp_cache[model_dir]
                config = {
                    "fingerprint_source": rev_source,
                    "local_subpath": local_subpath,
                }
                pg_store.upsert_model_version(cur, key, family, version, revision, config)
                results[key] = revision
        logger.info(
            "seeded ops.model_versions: gateway_build=%s local_rows=%d",
            gateway_state.build_revision if gateway_state else None,
            sum(1 for row in model_rows if row[3] == "local"),
        )
        _seed_cache_signature = cache_signature
        _seed_cache_results = dict(results)
    except Exception as e:
        logger.warning("model-version seed failed (non-fatal): %s", e)
    return results


__all__ = [
    "POINT_MODEL_KEYS",
    "build_point_model_versions",
    "fetch_gateway_state",
    "compute_gateway_fingerprint",
    "compute_local_fingerprint",
    "seed_model_versions",
]

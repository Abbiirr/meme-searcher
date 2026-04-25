import logging
import os
import threading

import numpy as np

from vidsearch.config import MODEL_ROOT

logger = logging.getLogger(__name__)

_bge_model = None
_siglip_processor = None
_siglip_model = None
_bge_lock = threading.Lock()
_siglip_lock = threading.Lock()


def _get_bge(model_root: str | None = None):
    global _bge_model
    if _bge_model is not None:
        return _bge_model

    with _bge_lock:
        if _bge_model is not None:
            return _bge_model

        root = model_root or MODEL_ROOT
        model_path = os.path.join(root, "embeddings", "bge-m3")

        from FlagEmbedding import BGEM3FlagModel

        if os.path.exists(model_path):
            _bge_model = BGEM3FlagModel(model_path, use_fp16=True)
        else:
            _bge_model = BGEM3FlagModel("BAAI/bge-m3", use_fp16=True)
    return _bge_model


def encode_text(text: str) -> tuple[list[float], dict[int, float]]:
    if not text.strip():
        return [], {}

    model = _get_bge()
    result = model.encode([text], return_dense=True, return_sparse=True)

    dense = result["dense_vecs"][0].tolist()
    sparse_vec = result["lexical_weights"][0]
    sparse_dict = {int(k): float(v) for k, v in sparse_vec.items()}

    return dense, sparse_dict


def _get_siglip(model_root: str | None = None):
    """Load the visual encoder using the checkpoint's declared model type."""
    global _siglip_processor, _siglip_model
    if _siglip_model is not None:
        return _siglip_processor, _siglip_model

    with _siglip_lock:
        if _siglip_model is not None:
            return _siglip_processor, _siglip_model

        import torch
        from transformers import AutoModel, AutoProcessor

        root = model_root or MODEL_ROOT
        local_path = os.path.join(root, "embeddings", "siglip2-so400m-patch16-384")
        if os.path.isfile(os.path.join(local_path, "config.json")):
            logger.info("Loading visual encoder from local: %s", local_path)
            _siglip_processor = AutoProcessor.from_pretrained(local_path)
            _siglip_model = AutoModel.from_pretrained(local_path)
        else:
            logger.warning(
                "Local visual weights not found at %s; falling back to HF download "
                "(google/siglip2-so400m-patch16-384)", local_path,
            )
            _siglip_processor = AutoProcessor.from_pretrained("google/siglip2-so400m-patch16-384")
            _siglip_model = AutoModel.from_pretrained("google/siglip2-so400m-patch16-384")

        device = "cuda" if torch.cuda.is_available() else "cpu"
        _siglip_model = _siglip_model.to(device)
        _siglip_model.eval()
    return _siglip_processor, _siglip_model


def _extract_feature_vector(features, attr_name: str):
    if hasattr(features, attr_name):
        features = getattr(features, attr_name)
    elif isinstance(features, (tuple, list)):
        features = features[0]

    if getattr(features, "ndim", 0) > 1:
        features = features[0]
    return features


def encode_visual(image) -> list[float]:
    processor, model = _get_siglip()
    import torch
    from PIL import Image

    if isinstance(image, (str, os.PathLike)):
        image = Image.open(image).convert("RGB")

    inputs = processor(images=image, return_tensors="pt").to(model.device)
    with torch.no_grad():
        outputs = model.get_image_features(**inputs)

    vec = _extract_feature_vector(outputs, "image_embeds").cpu().tolist()
    return vec


def encode_text_visual(text: str) -> list[float]:
    processor, model = _get_siglip()
    import torch

    inputs = processor(text=[text], return_tensors="pt", padding=True).to(model.device)
    with torch.no_grad():
        outputs = model.get_text_features(**inputs)

    vec = _extract_feature_vector(outputs, "text_embeds").cpu().tolist()
    return vec

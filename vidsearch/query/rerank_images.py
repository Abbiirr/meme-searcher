import logging
import os
import threading

from vidsearch.config import MODEL_ROOT

logger = logging.getLogger(__name__)

_reranker = None
_reranker_lock = threading.Lock()


def _get_reranker(model_root: str | None = None):
    global _reranker
    if _reranker is not None:
        return _reranker

    with _reranker_lock:
        if _reranker is not None:
            return _reranker

        root = model_root or MODEL_ROOT
        model_path = os.path.join(root, "embeddings", "jina-reranker-v2-base-multilingual")

        from transformers import AutoModelForSequenceClassification

        if os.path.exists(model_path):
            _reranker = AutoModelForSequenceClassification.from_pretrained(
                model_path,
                trust_remote_code=True,
            )
        else:
            _reranker = AutoModelForSequenceClassification.from_pretrained(
                "jinaai/jina-reranker-v2-base-multilingual",
                trust_remote_code=True,
            )
    return _reranker


def rerank(query: str, documents: list[str], top_k: int = 10) -> list[tuple[int, float]]:
    if not documents:
        return []

    model = _get_reranker()
    tokenizer = _get_tokenizer()

    pairs = [[query, doc] for doc in documents]
    import torch

    features = tokenizer(pairs, padding=True, truncation=True, max_length=512, return_tensors="pt")
    device = next(model.parameters()).device
    features = {k: v.to(device) for k, v in features.items()}

    with torch.no_grad():
        scores = model(**features).logits.squeeze(-1)

    scores = scores.cpu().tolist()
    if isinstance(scores, float):
        scores = [scores]

    indexed = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
    return indexed[:top_k]


_tokenizer = None
_tokenizer_lock = threading.Lock()


def _get_tokenizer():
    global _tokenizer
    if _tokenizer is not None:
        return _tokenizer

    with _tokenizer_lock:
        if _tokenizer is not None:
            return _tokenizer

        root = MODEL_ROOT
        model_path = os.path.join(root, "embeddings", "jina-reranker-v2-base-multilingual")

        from transformers import AutoTokenizer

        if os.path.exists(model_path):
            _tokenizer = AutoTokenizer.from_pretrained(
                model_path,
                trust_remote_code=True,
            )
        else:
            _tokenizer = AutoTokenizer.from_pretrained(
                "jinaai/jina-reranker-v2-base-multilingual",
                trust_remote_code=True,
            )
    return _tokenizer

"""Shared embedding model instance.

Both StaticRouter and ContextManager need an embedding model.
This module ensures only one copy is loaded into memory.
"""

from functools import lru_cache
from app_config import config
from logger import get_logger

logger = get_logger(__name__)

_model = None
_loaded = False


def get_embedding_model():
    """Lazy-load and return the shared SentenceTransformer model."""
    global _model, _loaded
    if not _loaded:
        model_name = config.routing.embedding_model
        logger.info(f"Loading embedding model ({model_name})...")
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(model_name)
        _loaded = True
    return _model

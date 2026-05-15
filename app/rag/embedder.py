"""Multilingual embedding model — lazy-loaded, runs in a thread pool."""
import asyncio
from functools import lru_cache
from typing import List

from loguru import logger

# paraphrase-multilingual-mpnet-base-v2: 50+ languages, 768-dim, ~1 GB
EMBEDDING_MODEL = "paraphrase-multilingual-mpnet-base-v2"
VECTOR_DIM = 768


@lru_cache(maxsize=1)
def _load_model():
    from sentence_transformers import SentenceTransformer
    logger.info(f"Loading embedding model '{EMBEDDING_MODEL}' (first call only)")
    return SentenceTransformer(EMBEDDING_MODEL)


async def embed_texts(texts: List[str]) -> List[List[float]]:
    """Embed a list of texts without blocking the event loop."""
    loop = asyncio.get_event_loop()
    model = _load_model()
    vectors: List[List[float]] = await loop.run_in_executor(
        None,
        lambda: model.encode(texts, show_progress_bar=False).tolist(),
    )
    return vectors


async def embed_query(text: str) -> List[float]:
    results = await embed_texts([text])
    return results[0]

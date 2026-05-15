"""Qdrant wrapper — collection lifecycle, upsert, delete, and search."""
import uuid
from typing import List, Optional

from loguru import logger
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    VectorParams,
)

from app.core.config import settings
from app.rag.chunk import Chunk
from app.rag.embedder import VECTOR_DIM, embed_texts

COLLECTION = "knowledge_base"


def _point_id(source_type: str, source_id: str, chunk_index: int, document_title: str = "") -> str:
    """Deterministic UUID so re-ingesting the same content is idempotent."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{source_type}:{source_id}:{document_title}:{chunk_index}"))


class QdrantStore:
    def __init__(self):
        self._client: Optional[AsyncQdrantClient] = None

    def _client_(self) -> AsyncQdrantClient:
        if self._client is None:
            self._client = AsyncQdrantClient(
                url=settings.qdrant_url,
                api_key=settings.qdrant_api_key or None,
            )
        return self._client

    async def ensure_collection(self):
        client = self._client_()
        existing = {c.name for c in (await client.get_collections()).collections}
        if COLLECTION not in existing:
            await client.create_collection(
                collection_name=COLLECTION,
                vectors_config=VectorParams(size=VECTOR_DIM, distance=Distance.COSINE),
            )
            logger.info(f"Created Qdrant collection '{COLLECTION}'")

    async def upsert_chunks(self, chunks: List[Chunk]) -> int:
        if not chunks:
            return 0
        vectors = await embed_texts([c.text for c in chunks])
        points = [
            PointStruct(
                id=_point_id(c.source_type, c.source_id, c.chunk_index, c.document_title),
                vector=vectors[i],
                payload={
                    "text": c.text,
                    "source_type": c.source_type,
                    "source_id": c.source_id,
                    "document_title": c.document_title,
                    "chunk_index": c.chunk_index,
                    **c.metadata,
                },
            )
            for i, c in enumerate(chunks)
        ]
        await self._client_().upsert(collection_name=COLLECTION, points=points)
        logger.debug(f"Upserted {len(points)} chunks into Qdrant")
        return len(points)

    async def delete_by_source(self, source_type: str, source_id: str):
        await self._client_().delete(
            collection_name=COLLECTION,
            points_selector=Filter(
                must=[
                    FieldCondition(key="source_type", match=MatchValue(value=source_type)),
                    FieldCondition(key="source_id", match=MatchValue(value=source_id)),
                ]
            ),
        )
        logger.info(f"Deleted chunks for {source_type}:{source_id}")

    async def search(
        self,
        query_vector: List[float],
        top_k: int = 5,
        filter_by: Optional[dict] = None,
    ) -> list:
        qdrant_filter = None
        if filter_by:
            qdrant_filter = Filter(
                must=[
                    FieldCondition(key=k, match=MatchValue(value=v))
                    for k, v in filter_by.items()
                ]
            )
        return await self._client_().search(
            collection_name=COLLECTION,
            query_vector=query_vector,
            limit=top_k,
            query_filter=qdrant_filter,
            with_payload=True,
        )

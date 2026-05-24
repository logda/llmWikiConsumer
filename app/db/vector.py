"""Qdrant vector database connection management."""

import logging
from typing import Any

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue

from app.config import get_settings

logger = logging.getLogger(__name__)

_qdrant: AsyncQdrantClient | None = None


async def get_qdrant() -> AsyncQdrantClient:
    """Get or create the Qdrant client."""
    global _qdrant
    if _qdrant is None:
        settings = get_settings()
        _qdrant = AsyncQdrantClient(
            host=settings.qdrant_host,
            port=settings.qdrant_port,
            api_key=settings.qdrant_api_key or None,
        )
        logger.info("Qdrant connection established to %s:%s", settings.qdrant_host, settings.qdrant_port)
    return _qdrant


async def close_qdrant() -> None:
    """Close the Qdrant client."""
    global _qdrant
    if _qdrant is not None:
        await _qdrant.close()
        _qdrant = None
        logger.info("Qdrant connection closed")


class VectorStore:
    """Qdrant-based vector store for wiki chunks."""

    def __init__(self, client: AsyncQdrantClient, collection: str = "wiki_chunks") -> None:
        self._client = client
        self._collection = collection

    async def get_chunks_by_path(
        self,
        namespace: str,
        version: str,
        path: str,
    ) -> list[dict[str, Any]]:
        """Retrieve all chunks for a specific page, sorted by chunk_index."""
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        filter_condition = Filter(
            must=[
                FieldCondition(key="namespace", match=MatchValue(value=namespace)),
                FieldCondition(key="version", match=MatchValue(value=version)),
                FieldCondition(key="page_path", match=MatchValue(value=path)),
            ]
        )

        results, _ = await self._client.scroll(
            collection_name=self._collection,
            scroll_filter=filter_condition,
            limit=1000,
            with_payload=True,
            with_vectors=False,
        )

        chunks = []
        for point in results:
            payload = point.payload or {}
            chunks.append({
                "id": str(point.id),
                "text": payload.get("text", ""),
                "chunk_index": payload.get("chunk_index", 0),
                "page_path": payload.get("page_path", ""),
                "namespace": payload.get("namespace", ""),
                "version": payload.get("version", ""),
            })

        chunks.sort(key=lambda c: c["chunk_index"])
        return chunks

    async def search_by_metadata(
        self,
        namespace: str,
        version: str,
        path_prefix: str | None = None,
        contains: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Search chunks using metadata filters (coarse filtering for grep)."""
        must_conditions = [
            FieldCondition(key="namespace", match=MatchValue(value=namespace)),
            FieldCondition(key="version", match=MatchValue(value=version)),
        ]

        if path_prefix:
            must_conditions.append(
                FieldCondition(key="page_path", match=MatchValue(value=path_prefix))
            )

        if contains:
            # Note: Qdrant doesn't have native $contains for payload;
            # this is a placeholder for the concept. Actual implementation
            # would need full-text search or post-filtering.
            pass

        filter_condition = Filter(must=must_conditions)

        results, _ = await self._client.scroll(
            collection_name=self._collection,
            scroll_filter=filter_condition,
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )

        chunks = []
        for point in results:
            payload = point.payload or {}
            chunks.append({
                "id": str(point.id),
                "text": payload.get("text", ""),
                "chunk_index": payload.get("chunk_index", 0),
                "page_path": payload.get("page_path", ""),
                "namespace": payload.get("namespace", ""),
                "version": payload.get("version", ""),
            })

        return chunks
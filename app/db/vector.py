"""Qdrant vector database connection management."""

import logging
import uuid
from typing import Any

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchValue, PointStruct, VectorParams

from app.config import get_settings

logger = logging.getLogger(__name__)

_qdrant: AsyncQdrantClient | None = None

# Default vector size for sparse/no-embedding mode (we use metadata-only for now)
DEFAULT_VECTOR_SIZE = 4


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
        logger.info(
            "Qdrant connection established to %s:%s",
            settings.qdrant_host, settings.qdrant_port,
        )
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

    async def ensure_collection(self) -> None:
        """Ensure the Qdrant collection exists, create if not."""
        collections = await self._client.get_collections()
        collection_names = [c.name for c in collections.collections]

        if self._collection not in collection_names:
            await self._client.create_collection(
                collection_name=self._collection,
                vectors_config=VectorParams(
                    size=DEFAULT_VECTOR_SIZE,
                    distance="Cosine",
                ),
            )
            logger.info("Created Qdrant collection: %s", self._collection)
        else:
            logger.debug("Qdrant collection already exists: %s", self._collection)

    async def upsert_chunks(
        self,
        namespace: str,
        version: str,
        chunks: list[dict[str, Any]],
    ) -> int:
        """Batch upsert chunks into Qdrant.

        Args:
            namespace: Wiki namespace.
            version: Wiki version string.
            chunks: List of chunk dicts with keys: text, chunk_index, page_path.

        Returns:
            Number of chunks upserted.
        """
        await self.ensure_collection()

        points = []
        for chunk in chunks:
            point_id = str(uuid.uuid4())
            # Use a dummy vector since we focus on metadata search for now
            vector = [0.0, 0.0, 0.0, 0.0]
            payload = {
                "text": chunk["text"],
                "chunk_index": chunk["chunk_index"],
                "page_path": chunk["page_path"],
                "namespace": namespace,
                "version": version,
                "start_line": chunk.get("start_line", 0),
                "end_line": chunk.get("end_line", 0),
            }
            points.append(
                PointStruct(id=point_id, vector=vector, payload=payload)
            )

        # Batch upsert (max 100 per request)
        batch_size = 100
        for i in range(0, len(points), batch_size):
            batch = points[i : i + batch_size]
            await self._client.upsert(
                collection_name=self._collection,
                points=batch,
            )

        logger.info("Upserted %d chunks for %s:%s", len(points), namespace, version)
        return len(points)

    async def delete_chunks_by_namespace_version(
        self, namespace: str, version: str
    ) -> int:
        """Delete all chunks for a specific namespace and version.

        Returns the number of points deleted.
        """
        from qdrant_client.models import PointIdsList

        # First, find all matching points
        filter_condition = Filter(
            must=[
                FieldCondition(key="namespace", match=MatchValue(value=namespace)),
                FieldCondition(key="version", match=MatchValue(value=version)),
            ]
        )

        results, _ = await self._client.scroll(
            collection_name=self._collection,
            scroll_filter=filter_condition,
            limit=10000,
            with_payload=False,
            with_vectors=False,
        )

        if not results:
            return 0

        point_ids = [point.id for point in results]
        await self._client.delete(
            collection_name=self._collection,
            points_selector=PointIdsList(points=point_ids),
        )
        logger.info(
            "Deleted %d chunks for %s:%s", len(point_ids), namespace, version
        )
        return len(point_ids)

    async def get_chunks_by_path(
        self,
        namespace: str,
        version: str,
        path: str,
    ) -> list[dict[str, Any]]:
        """Retrieve all chunks for a specific page, sorted by chunk_index."""
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

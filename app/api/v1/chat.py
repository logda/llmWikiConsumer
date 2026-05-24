"""Chat API endpoints - user Q&A with wiki knowledge base via SSE streaming."""

import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.config import get_settings
from app.core.wikifs import WikiFs
from app.db.postgres import get_db
from app.db.redis import RedisCache, get_redis
from app.db.vector import VectorStore, get_qdrant
from app.models.db_models import WikiNamespace, WikiVersion
from app.models.schemas import ActiveVersionInfo, ChatRequest
from app.services.agent import WikiAgent
from app.services.llm_client import LLMClient

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------- Dependency Helpers ----------


async def _get_active_version(
    namespace_id: str,
    db: AsyncSession,
) -> tuple[WikiNamespace, WikiVersion]:
    """Get the active version for a namespace.

    Args:
        namespace_id: Namespace ID.
        db: Database session.

    Returns:
        Tuple of (namespace, active_version).

    Raises:
        HTTPException: If namespace or active version not found.
    """
    # Get namespace
    result = await db.execute(
        select(WikiNamespace).where(WikiNamespace.id == namespace_id)
    )
    namespace = result.scalar_one_or_none()
    if namespace is None:
        raise HTTPException(status_code=404, detail=f"Namespace {namespace_id} not found")

    # Get active version
    result = await db.execute(
        select(WikiVersion).where(
            WikiVersion.namespace_id == namespace_id,
            WikiVersion.status == "active",
        )
    )
    version = result.scalar_one_or_none()
    if version is None:
        raise HTTPException(
            status_code=404,
            detail=f"No active version found for namespace {namespace_id}",
        )

    return namespace, version


async def _build_wikifs(namespace_name: str, version_str: str) -> WikiFs:
    """Build a WikiFs instance for the given namespace/version.

    Args:
        namespace_name: Namespace name.
        version_str: Version string.

    Returns:
        Configured WikiFs instance.
    """
    redis_client = await get_redis()
    redis_cache = RedisCache(redis_client)
    qdrant_client = await get_qdrant()
    vector_store = VectorStore(qdrant_client)
    settings = get_settings()

    # Load path tree from Redis
    path_tree = await redis_cache.get_path_tree(namespace_name, version_str)
    if path_tree is None:
        path_tree = {"files": [], "directories": {}}

    return WikiFs(
        namespace=namespace_name,
        version=version_str,
        path_tree=path_tree,
        vector_store=vector_store,
        cache=redis_cache,
        cache_ttl=settings.wikifs_cache_ttl,
    )


def _build_llm_client() -> LLMClient:
    """Build an LLM client from application settings."""
    settings = get_settings()
    return LLMClient(
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        base_url=settings.llm_base_url or None,
    )


# ---------- SSE Event Generator ----------


async def _chat_event_generator(
    agent: WikiAgent,
    question: str,
    history: list[dict[str, Any]],
) -> AsyncGenerator[dict[str, Any], None]:
    """Generate SSE events from the agent's chat response.

    Args:
        agent: WikiAgent instance.
        question: User question.
        history: Conversation history.

    Yields:
        SSE event dicts compatible with EventSourceResponse.
    """
    try:
        async for event in agent.chat(question, history):
            event_data = json.dumps(event, ensure_ascii=False)
            yield {"data": event_data}
    except Exception as e:
        logger.error("Error in chat event generator: %s", e, exc_info=True)
        error_event = json.dumps(
            {"type": "error", "message": f"内部错误: {e}"},
            ensure_ascii=False,
        )
        yield {"data": error_event}
        done_event = json.dumps({"type": "done"}, ensure_ascii=False)
        yield {"data": done_event}


# ---------- Endpoints ----------


@router.post("/chat")
async def chat(
    request: ChatRequest,
    db: AsyncSession = Depends(get_db),
) -> EventSourceResponse:
    """Chat with the wiki knowledge base - SSE streaming response.

    Flow:
    1. Resolve namespace_id to active version
    2. Build WikiFs for that version
    3. Create WikiAgent
    4. Stream agent response as SSE events
    """
    # Get active version
    namespace, version = await _get_active_version(request.namespace_id, db)

    # Build WikiFs
    wikifs = await _build_wikifs(namespace.name, version.version)

    # Build LLM client
    llm_client = _build_llm_client()

    # Create agent
    agent = WikiAgent(wikifs=wikifs, llm_client=llm_client)

    # Stream response
    return EventSourceResponse(
        _chat_event_generator(agent, request.question, request.history),
        media_type="text/event-stream",
    )


@router.get("/namespaces/{namespace_id}/active-version", response_model=ActiveVersionInfo)
async def get_active_version(
    namespace_id: str,
    db: AsyncSession = Depends(get_db),
) -> Any:
    """Get the currently active version for a namespace."""
    namespace, version = await _get_active_version(namespace_id, db)
    return ActiveVersionInfo(
        namespace_id=namespace.id,
        namespace_name=namespace.name,
        version_id=version.id,
        version=version.version,
        page_count=version.page_count,
    )

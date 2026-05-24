"""Admin API endpoints - namespace and version management."""

import logging
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.postgres import get_db
from app.db.redis import RedisCache, get_redis
from app.db.storage import StorageBackend
from app.db.vector import VectorStore, get_qdrant
from app.models.schemas import (
    NamespaceCreate,
    NamespaceInfo,
    VersionActivateResponse,
    VersionInfo,
)
from app.services.version import VersionService

logger = logging.getLogger(__name__)

router = APIRouter()


async def get_version_service(
    db: AsyncSession = Depends(get_db),
) -> VersionService:
    """Create a VersionService instance with dependencies (async)."""
    redis_client = await get_redis()
    redis_cache = RedisCache(redis_client)
    qdrant_client = await get_qdrant()
    vector_store = VectorStore(qdrant_client)
    storage = StorageBackend()
    return VersionService(
        db=db,
        storage=storage,
        redis_cache=redis_cache,
        vector_store=vector_store,
    )


# ---------- Namespace Endpoints ----------


@router.post("/namespaces", response_model=NamespaceInfo, status_code=201)
async def create_namespace(
    body: NamespaceCreate,
    svc: VersionService = Depends(get_version_service),
) -> Any:
    """创建知识库命名空间."""
    try:
        namespace = await svc.create_namespace(body.name, body.display_name)
        return namespace
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.get("/namespaces", response_model=list[NamespaceInfo])
async def list_namespaces(
    svc: VersionService = Depends(get_version_service),
) -> Any:
    """列出所有知识库."""
    return await svc.list_namespaces()


# ---------- Version Endpoints ----------


@router.post(
    "/namespaces/{namespace_id}/versions",
    response_model=VersionInfo,
    status_code=201,
)
async def upload_version(
    namespace_id: str,
    file: UploadFile = File(...),
    svc: VersionService = Depends(get_version_service),
) -> Any:
    """上传 .wiki.tar.gz 版本包.

    - 接收文件上传
    - 校验 manifest.json
    - 解压并处理
    - 返回版本信息
    """
    # Validate file extension
    if not file.filename or not file.filename.endswith(".wiki.tar.gz"):
        raise HTTPException(
            status_code=422,
            detail="Invalid file format. Expected .wiki.tar.gz file.",
        )

    # Read file content
    content = await file.read()
    if len(content) == 0:
        raise HTTPException(status_code=422, detail="Empty file uploaded.")

    try:
        version = await svc.process_upload(namespace_id, content, file.filename)
        return version
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.put("/versions/{version_id}/activate", response_model=VersionActivateResponse)
async def activate_version(
    version_id: str,
    svc: VersionService = Depends(get_version_service),
) -> Any:
    """激活某个版本（设为当前活跃版本）."""
    try:
        version = await svc.activate_version(version_id)
        return VersionActivateResponse(
            version_id=version.id,
            status=version.status,
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/namespaces/{namespace_id}/versions", response_model=list[VersionInfo])
async def list_versions(
    namespace_id: str,
    svc: VersionService = Depends(get_version_service),
) -> Any:
    """列出某知识库的所有版本."""
    return await svc.list_versions(namespace_id)

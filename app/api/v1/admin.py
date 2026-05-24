"""Admin API endpoints - version upload and management."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/versions")
async def list_versions() -> dict:
    """List all available wiki versions."""
    # TODO: implement version listing
    return {"versions": []}


@router.post("/versions")
async def upload_version() -> dict:
    """Upload a new wiki version package."""
    # TODO: implement version upload
    return {"status": "ok"}
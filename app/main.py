"""FastAPI application entry point."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import api_router
from app.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    """Application lifespan: startup and shutdown."""
    # Startup: initialize connections
    from app.db.postgres import init_db
    await init_db()

    yield

    # Shutdown: close connections
    from app.db.postgres import close_postgres
    from app.db.redis import close_redis
    from app.db.vector import close_qdrant

    await close_qdrant()
    await close_redis()
    await close_postgres()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    application = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        lifespan=lifespan,
    )

    application.include_router(api_router, prefix="/api/v1")

    # Mount frontend static files (must be AFTER API routes)
    frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
    if frontend_dir.is_dir():
        application.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")

    return application


app = create_app()

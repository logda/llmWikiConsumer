"""Version management service (placeholder)."""

import logging

logger = logging.getLogger(__name__)


class VersionService:
    """Service for managing wiki versions."""

    async def list_versions(self, namespace: str) -> list[dict]:
        """List all versions for a namespace."""
        # TODO: implement with PostgreSQL
        logger.info("Listing versions for namespace=%s", namespace)
        return []

    async def get_version(self, namespace: str, version: str) -> dict | None:
        """Get a specific version."""
        # TODO: implement with PostgreSQL
        logger.info("Getting version %s/%s", namespace, version)
        return None

    async def create_version(self, namespace: str, version: str, package_path: str) -> dict:
        """Create a new version from an uploaded package."""
        # TODO: implement with PostgreSQL + object storage
        logger.info("Creating version %s/%s from %s", namespace, version, package_path)
        return {"namespace": namespace, "version": version, "status": "pending"}
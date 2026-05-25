"""File storage management - local filesystem with MinIO interface预留."""

import logging
import shutil
from pathlib import Path

from app.config import get_settings

logger = logging.getLogger(__name__)


class StorageBackend:
    """Local filesystem storage backend."""

    def __init__(self, base_path: str | None = None) -> None:
        if base_path is None:
            settings = get_settings()
            base_path = settings.storage_path
        self._base_path = Path(base_path)
        self._base_path.mkdir(parents=True, exist_ok=True)
        logger.info("Storage backend initialized at %s", self._base_path)

    @property
    def base_path(self) -> Path:
        """Return the base storage path."""
        return self._base_path

    def namespace_dir(self, namespace_id: str) -> Path:
        """Get directory for a namespace's files."""
        path = self._base_path / namespace_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def version_dir(self, namespace_id: str, version_id: str) -> Path:
        """Get directory for a version's extracted files."""
        path = self._base_path / namespace_id / version_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    async def save_upload(self, namespace_id: str, filename: str, content: bytes) -> Path:
        """Save an uploaded file to storage.

        Args:
            namespace_id: Namespace identifier.
            filename: Original filename.
            content: File content bytes.

        Returns:
            Path to the saved file.
        """
        ns_dir = self.namespace_dir(namespace_id)
        file_path = ns_dir / filename
        file_path.write_bytes(content)
        logger.info("Saved upload: %s (%d bytes)", file_path, len(content))
        return file_path

    async def extract_tarball(self, tarball_path: Path, version_id: str, namespace_id: str) -> Path:
        """Extract a .wiki.tar.gz to the version directory.

        Args:
            tarball_path: Path to the tar.gz file.
            version_id: Version identifier.
            namespace_id: Namespace identifier.

        Returns:
            Path to the extracted wiki directory.
        """
        import tarfile

        extract_dir = self.version_dir(namespace_id, version_id)

        with tarfile.open(tarball_path, "r:gz") as tar:
            tar.extractall(path=extract_dir, filter="data")

        logger.info("Extracted tarball %s to %s", tarball_path, extract_dir)

        # Find the wiki root (directory containing wiki/ or the root itself)
        # The tarball typically has a top-level directory like "test-wiki/"
        wiki_root = self._find_wiki_root(extract_dir)
        return wiki_root

    async def extract_zip(self, zip_path: Path, version_id: str, namespace_id: str) -> Path:
        """Extract a .zip to the version directory.

        Includes zip slip protection: verifies that extracted paths
        do not escape the target directory.

        Args:
            zip_path: Path to the .zip file.
            version_id: Version identifier.
            namespace_id: Namespace identifier.

        Returns:
            Path to the extracted wiki directory.

        Raises:
            ValueError: If a zip entry would escape the extract directory (zip slip).
        """
        import zipfile

        extract_dir = self.version_dir(namespace_id, version_id)
        resolved_extract = extract_dir.resolve()

        with zipfile.ZipFile(zip_path, "r") as zf:
            for member in zf.infolist():
                # Skip directories – they are created implicitly by extracting files
                if member.is_dir():
                    continue

                # Zip slip check: ensure the resolved target path stays within extract_dir
                target_path = (extract_dir / member.filename).resolve()
                if not str(target_path).startswith(str(resolved_extract)):
                    raise ValueError(
                        f"Zip slip detected: '{member.filename}' would escape "
                        f"the extract directory"
                    )

                # Ensure parent directory exists
                target_path.parent.mkdir(parents=True, exist_ok=True)

                # Extract the file
                with zf.open(member) as src, target_path.open("wb") as dst:
                    dst.write(src.read())

        logger.info("Extracted zip %s to %s", zip_path, extract_dir)

        # Find the wiki root (same logic as tarball)
        wiki_root = self._find_wiki_root(extract_dir)
        return wiki_root

    def _find_wiki_root(self, extract_dir: Path) -> Path:
        """Find the wiki root directory after extraction.

        Looks for a directory containing a 'wiki/' subdirectory or 'manifest.json'.
        If the extract_dir directly contains these, return it.
        Otherwise, look in immediate subdirectories.
        """
        # Check if extract_dir itself is the wiki root
        if (extract_dir / "wiki").is_dir() or (extract_dir / "manifest.json").exists():
            return extract_dir

        # Look in immediate subdirectories
        for child in extract_dir.iterdir():
            if child.is_dir() and (
                (child / "wiki").is_dir() or (child / "manifest.json").exists()
            ):
                return child

        # Fallback: return the extract_dir itself
        return extract_dir

    async def delete_version(self, namespace_id: str, version_id: str) -> None:
        """Delete a version's extracted files."""
        version_path = self.version_dir(namespace_id, version_id)
        if version_path.exists():
            shutil.rmtree(version_path)
            logger.info("Deleted version files: %s", version_path)

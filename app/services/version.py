"""Version management service - handles upload, path tree, chunk indexing."""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.redis import RedisCache
from app.db.storage import StorageBackend
from app.db.vector import VectorStore
from app.models.db_models import WikiNamespace, WikiVersion

logger = logging.getLogger(__name__)


def split_into_chunks(
    content: str,
    chunk_size: int = 500,
    overlap: int = 50,
) -> list[dict]:
    """Split markdown content into chunks.

    Strategy: split by ## headings first, then by fixed size with overlap.

    Args:
        content: Markdown content to split.
        chunk_size: Maximum chunk size in characters.
        overlap: Number of overlapping lines between chunks.

    Returns:
        List of chunk dicts with text, chunk_index, start_line, end_line.
    """
    lines = content.split("\n")

    # Step 1: Split by ## headings
    sections: list[tuple[int, int]] = []  # (start_line_idx, end_line_idx)
    section_start = 0

    for i, line in enumerate(lines):
        if line.startswith("## ") and i > 0:
            sections.append((section_start, i))
            section_start = i

    # Add the last section
    sections.append((section_start, len(lines)))

    # Step 2: For each section, check if it fits in chunk_size
    chunks: list[dict] = []
    chunk_index = 0

    for start_idx, end_idx in sections:
        section_lines = lines[start_idx:end_idx]
        section_text = "\n".join(section_lines)

        if len(section_text) <= chunk_size:
            chunks.append({
                "text": section_text,
                "chunk_index": chunk_index,
                "start_line": start_idx + 1,  # 1-based
                "end_line": end_idx,
            })
            chunk_index += 1
        else:
            # Step 3: Split by fixed size with overlap
            line_cursor = start_idx
            while line_cursor < end_idx:
                # Find end of current chunk
                chunk_end = line_cursor
                current_size = 0
                while chunk_end < end_idx and current_size < chunk_size:
                    current_size += len(lines[chunk_end]) + 1  # +1 for newline
                    chunk_end += 1

                # Include overlap lines
                overlap_end = min(chunk_end + overlap, end_idx)
                chunk_lines = lines[line_cursor:overlap_end]
                chunk_text = "\n".join(chunk_lines)

                chunks.append({
                    "text": chunk_text,
                    "chunk_index": chunk_index,
                    "start_line": line_cursor + 1,  # 1-based
                    "end_line": overlap_end,
                })
                chunk_index += 1

                # Move cursor forward (skip the overlap part for next chunk start)
                line_cursor = chunk_end

    return chunks


class VersionService:
    """Service for managing wiki versions - upload, process, activate."""

    def __init__(
        self,
        db: AsyncSession,
        storage: StorageBackend,
        redis_cache: RedisCache,
        vector_store: VectorStore,
    ) -> None:
        self._db = db
        self._storage = storage
        self._redis_cache = redis_cache
        self._vector_store = vector_store

    async def create_namespace(self, name: str, display_name: str) -> WikiNamespace:
        """Create a new wiki namespace.

        Args:
            name: Namespace identifier.
            display_name: Human-readable name.

        Returns:
            Created WikiNamespace object.

        Raises:
            ValueError: If namespace name already exists.
        """
        # Check if namespace already exists
        existing = await self._db.execute(
            select(WikiNamespace).where(WikiNamespace.name == name)
        )
        if existing.scalar_one_or_none() is not None:
            raise ValueError(f"Namespace '{name}' already exists")

        namespace = WikiNamespace(name=name, display_name=display_name)
        self._db.add(namespace)
        await self._db.flush()
        logger.info("Created namespace: %s (%s)", name, namespace.id)
        return namespace

    async def list_namespaces(self) -> list[WikiNamespace]:
        """List all namespaces."""
        result = await self._db.execute(select(WikiNamespace).order_by(WikiNamespace.created_at))
        return list(result.scalars().all())

    async def get_namespace(self, namespace_id: str) -> WikiNamespace | None:
        """Get a namespace by ID."""
        result = await self._db.execute(
            select(WikiNamespace).where(WikiNamespace.id == namespace_id)
        )
        return result.scalar_one_or_none()

    async def get_namespace_by_name(self, name: str) -> WikiNamespace | None:
        """Get a namespace by name."""
        result = await self._db.execute(
            select(WikiNamespace).where(WikiNamespace.name == name)
        )
        return result.scalar_one_or_none()

    async def process_upload(
        self,
        namespace_id: str,
        file_content: bytes,
        filename: str,
    ) -> WikiVersion:
        """Process a version package upload.

        Complete flow:
        1. Save raw file to storage
        2. Extract tar.gz
        3. Read and validate manifest.json
        4. Build path tree → compress to gzip JSON → store in Redis
        5. Split wiki pages into chunks → store in Qdrant
        6. Create version record in PostgreSQL (status=draft)

        Args:
            namespace_id: Namespace ID to upload to.
            file_content: Raw file bytes.
            filename: Original filename.

        Returns:
            Created WikiVersion object.

        Raises:
            FileNotFoundError: If namespace not found.
            ValueError: If package format is invalid.
        """
        # Verify namespace exists
        namespace = await self.get_namespace(namespace_id)
        if namespace is None:
            raise FileNotFoundError(f"Namespace {namespace_id} not found")

        # Step 1: Save raw file
        file_path = await self._storage.save_upload(namespace_id, filename, file_content)
        logger.info("Saved upload: %s", file_path)

        # Step 2: Create version record (pre-alloc ID for directory)
        version = WikiVersion(
            namespace_id=namespace_id,
            version="pending",  # Will be updated after reading manifest
            status="draft",
            file_path=str(file_path),
            page_count=0,
        )
        self._db.add(version)
        await self._db.flush()  # Get the ID

        # Step 3: Extract archive (tar.gz or zip)
        try:
            if filename.endswith(".zip"):
                wiki_root = await self._storage.extract_zip(
                    file_path, version.id, namespace_id
                )
            elif filename.endswith(".tar.gz") or filename.endswith(".wiki.tar.gz"):
                wiki_root = await self._storage.extract_tarball(
                    file_path, version.id, namespace_id
                )
            else:
                raise ValueError(
                    f"Unsupported archive format: '{filename}'. "
                    f"Supported formats: .zip, .tar.gz, .wiki.tar.gz"
                )
        except ValueError:
            # Re-raise ValueError (unsupported format / zip slip) without wrapping
            await self._db.delete(version)
            await self._db.flush()
            raise
        except Exception as e:
            # Clean up the version record
            await self._db.delete(version)
            await self._db.flush()
            raise ValueError(f"Failed to extract archive: {e}") from e

        # Step 4: Read and validate manifest.json
        manifest_path = wiki_root / "manifest.json"
        manifest = None
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                logger.warning("Failed to read manifest.json: %s", e)

        # Determine version string from manifest or filename
        version_str = "1.0.0"
        if manifest and "version" in manifest:
            version_str = str(manifest["version"])
        elif filename.endswith(".wiki.tar.gz"):
            # Try to extract version from filename: name-1.2.0.wiki.tar.gz
            parts = filename.replace(".wiki.tar.gz", "").rsplit("-", 1)
            if len(parts) == 2:
                version_str = parts[1]
        elif filename.endswith(".tar.gz"):
            # Try to extract version from filename: name-1.2.0.tar.gz
            parts = filename.replace(".tar.gz", "").rsplit("-", 1)
            if len(parts) == 2:
                version_str = parts[1]
        elif filename.endswith(".zip"):
            # Try to extract version from filename: name-1.2.0.zip
            parts = filename.replace(".zip", "").rsplit("-", 1)
            if len(parts) == 2:
                version_str = parts[1]

        # Step 5: Build path tree
        # Use wiki_root as base so paths include wiki/ prefix
        path_tree = await self.build_path_tree(wiki_root)

        # Step 6: Store path tree in Redis
        await self._redis_cache.set_path_tree(namespace.name, version_str, path_tree)

        # Step 7: Chunk and index
        settings = get_settings()
        chunk_count = await self.chunk_and_index(
            namespace.name, version_str, wiki_root,
            chunk_size=settings.chunk_size,
            overlap=settings.chunk_overlap,
        )

        # Step 8: Update version record
        version.version = version_str
        version.manifest = manifest
        version.page_count = len(path_tree.get("files", []))
        await self._db.flush()

        logger.info(
            "Version %s/%s created: %d pages, %d chunks",
            namespace.name, version_str, version.page_count, chunk_count,
        )
        return version

    async def activate_version(self, version_id: str) -> WikiVersion:
        """Activate a version - set current active to archived, target to active.

        Args:
            version_id: Version ID to activate.

        Returns:
            The activated WikiVersion.

        Raises:
            FileNotFoundError: If version not found.
        """
        # Get the target version
        result = await self._db.execute(
            select(WikiVersion).where(WikiVersion.id == version_id)
        )
        target_version = result.scalar_one_or_none()
        if target_version is None:
            raise FileNotFoundError(f"Version {version_id} not found")

        # Archive all currently active versions in the same namespace
        await self._db.execute(
            update(WikiVersion)
            .where(
                WikiVersion.namespace_id == target_version.namespace_id,
                WikiVersion.status == "active",
            )
            .values(status="archived")
        )

        # Activate the target version
        target_version.status = "active"
        target_version.activated_at = datetime.now(UTC)
        await self._db.flush()

        logger.info(
            "Activated version %s (%s)", target_version.version, version_id
        )
        return target_version

    async def list_versions(self, namespace_id: str) -> list[WikiVersion]:
        """List all versions for a namespace."""
        result = await self._db.execute(
            select(WikiVersion)
            .where(WikiVersion.namespace_id == namespace_id)
            .order_by(WikiVersion.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_version(self, version_id: str) -> WikiVersion | None:
        """Get a version by ID."""
        result = await self._db.execute(
            select(WikiVersion).where(WikiVersion.id == version_id)
        )
        return result.scalar_one_or_none()

    async def build_path_tree(self, wiki_dir: Path) -> dict:
        """Build path tree from wiki directory structure.

        Args:
            wiki_dir: Path to the wiki directory.

        Returns:
            Path tree dict with 'files' and 'directories' keys.
        """
        files: list[str] = []
        directories: dict[str, list[str]] = {}

        if not wiki_dir.exists():
            return {"files": [], "directories": {}}

        for md_file in sorted(wiki_dir.rglob("*.md")):
            rel_path = md_file.relative_to(wiki_dir)
            path_str = str(rel_path)
            files.append(path_str)

            # Build directory entries
            parts = path_str.split("/")
            for i in range(len(parts) - 1):
                dir_key = "/".join(parts[: i + 1]) + "/"
                if dir_key not in directories:
                    dir_path = wiki_dir / "/".join(parts[: i + 1])
                    if dir_path.is_dir():
                        entries = sorted(
                            str(p.relative_to(dir_path)) + ("/" if p.is_dir() else "")
                            for p in dir_path.iterdir()
                        )
                        directories[dir_key] = entries

        return {"files": files, "directories": directories}

    async def chunk_and_index(
        self,
        namespace: str,
        version: str,
        wiki_dir: Path,
        chunk_size: int = 500,
        overlap: int = 50,
    ) -> int:
        """Split wiki pages into chunks and store in vector database.

        Args:
            namespace: Namespace name.
            version: Version string.
            wiki_dir: Path to the wiki directory.
            chunk_size: Maximum chunk size in characters.
            overlap: Number of overlapping lines between chunks.

        Returns:
            Total number of chunks indexed.
        """
        if not wiki_dir.exists():
            logger.warning("Wiki directory does not exist: %s", wiki_dir)
            return 0

        all_chunks: list[dict] = []

        for md_file in sorted(wiki_dir.rglob("*.md")):
            rel_path = md_file.relative_to(wiki_dir)
            path_str = str(rel_path)

            try:
                content = md_file.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                logger.warning("Skipping file with encoding error: %s", path_str)
                continue

            if not content.strip():
                continue

            file_chunks = split_into_chunks(content, chunk_size=chunk_size, overlap=overlap)
            for chunk in file_chunks:
                chunk["page_path"] = path_str
            all_chunks.extend(file_chunks)

        if all_chunks:
            await self._vector_store.upsert_chunks(namespace, version, all_chunks)

        logger.info(
            "Indexed %d chunks from %d files for %s:%s",
            len(all_chunks), len(list(wiki_dir.rglob("*.md"))),
            namespace, version,
        )
        return len(all_chunks)

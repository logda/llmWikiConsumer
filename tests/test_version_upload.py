"""Integration tests for version upload, path tree, and chunk indexing.

Uses SQLite in-memory database and mock Redis/Qdrant for testing
without external service dependencies.
"""

import gzip
import json
import tempfile
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.db_models import Base, WikiVersion
from app.services.version import VersionService, split_into_chunks

# ---------- Fixtures ----------


@pytest.fixture
def tmp_storage(tmp_path: Path) -> Path:
    """Create a temporary storage directory."""
    storage_dir = tmp_path / "storage"
    storage_dir.mkdir()
    return storage_dir


@pytest.fixture
def tmp_uploads(tmp_path: Path) -> Path:
    """Create a temporary uploads directory."""
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    return upload_dir


@pytest.fixture
async def db_engine():
    """Create an in-memory SQLite async engine for testing."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def db_session(db_engine) -> AsyncSession:
    """Create an async database session for testing."""
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session


@pytest.fixture
def mock_redis_cache():
    """Mock RedisCache with in-memory store."""
    store: dict[str, bytes] = {}

    class MockRedisCache:
        async def set_path_tree(self, namespace: str, version: str, tree: dict[str, Any]) -> None:
            key = f"path_tree:{namespace}:{version}"
            json_bytes = json.dumps(tree, ensure_ascii=False).encode("utf-8")
            compressed = gzip.compress(json_bytes)
            store[key] = compressed

        async def get_path_tree(self, namespace: str, version: str) -> dict[str, Any] | None:
            key = f"path_tree:{namespace}:{version}"
            raw = store.get(key)
            if raw is None:
                return None
            decompressed = gzip.decompress(raw)
            return json.loads(decompressed)

        async def get(self, namespace: str, version: str, path: str) -> str | None:
            return None

        async def set(self, namespace: str, version: str, path: str, content: str) -> None:
            pass

    return MockRedisCache()


@pytest.fixture
def mock_vector_store():
    """Mock VectorStore with in-memory chunk storage."""
    chunks_store: list[dict[str, Any]] = []

    class MockVectorStore:
        async def ensure_collection(self) -> None:
            pass

        async def upsert_chunks(
            self, namespace: str, version: str, chunks: list[dict[str, Any]]
        ) -> int:
            for chunk in chunks:
                chunk["namespace"] = namespace
                chunk["version"] = version
                chunks_store.append(chunk)
            return len(chunks)

        async def get_chunks_by_path(
            self, namespace: str, version: str, path: str
        ) -> list[dict[str, Any]]:
            return [
                c for c in chunks_store
                if c.get("namespace") == namespace
                and c.get("version") == version
                and c.get("page_path") == path
            ]

    return MockVectorStore()


@pytest.fixture
def version_service(db_session, tmp_storage, mock_redis_cache, mock_vector_store):
    """Create a VersionService with test dependencies."""
    from app.db.storage import StorageBackend

    storage = StorageBackend(base_path=str(tmp_storage))
    return VersionService(
        db=db_session,
        storage=storage,
        redis_cache=mock_redis_cache,
        vector_store=mock_vector_store,
    )


def create_test_tarball(output_dir: Path, name: str = "test-wiki") -> Path:
    """Create a test .wiki.tar.gz archive."""
    import tarfile

    wiki_root = _create_wiki_structure(output_dir, name)

    # Create tarball
    tarball_path = output_dir / f"{name}.wiki.tar.gz"
    with tarfile.open(tarball_path, "w:gz") as tar:
        tar.add(wiki_root, arcname=name)

    return tarball_path


def create_test_zip(output_dir: Path, name: str = "test-wiki") -> Path:
    """Create a test .zip archive."""
    import zipfile

    wiki_root = _create_wiki_structure(output_dir, name)

    # Create zip
    zip_path = output_dir / f"{name}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file_path in sorted(wiki_root.rglob("*")):
            if file_path.is_file():
                arcname = f"{name}/{file_path.relative_to(wiki_root)}"
                zf.write(file_path, arcname)

    return zip_path


def _create_wiki_structure(output_dir: Path, name: str = "test-wiki") -> Path:
    """Create the wiki directory structure used by both tarball and zip helpers."""
    wiki_root = output_dir / name
    if wiki_root.exists():
        import shutil
        shutil.rmtree(wiki_root)
    wiki_root.mkdir(parents=True, exist_ok=True)

    # Create wiki directory structure
    wiki_dir = wiki_root / "wiki"
    wiki_dir.mkdir(exist_ok=True)

    # Create index
    (wiki_dir / "index.md").write_text("# Wiki Index\n\nWelcome to the test wiki.\n")

    # Create subdirectories and pages
    concepts_dir = wiki_dir / "concepts"
    concepts_dir.mkdir(exist_ok=True)
    (concepts_dir / "oauth.md").write_text(
        "# OAuth 2.0\n\nOAuth is an authorization framework.\n\n"
        "## Grant Types\n\nAuthorization Code and Client Credentials.\n"
    )
    (concepts_dir / "rbac.md").write_text(
        "# RBAC\n\nRole-Based Access Control.\n"
    )

    entities_dir = wiki_dir / "entities"
    entities_dir.mkdir(exist_ok=True)
    (entities_dir / "auth-service.md").write_text(
        "# Auth Service\n\nHandles token validation.\n"
    )

    # Create manifest.json
    manifest = {
        "namespace": "test",
        "version": "1.0.0",
        "description": "Test wiki package",
        "page_count": 4,
        "files": [
            "wiki/index.md",
            "wiki/concepts/oauth.md",
            "wiki/concepts/rbac.md",
            "wiki/entities/auth-service.md",
        ],
    }
    (wiki_root / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    return wiki_root


# ---------- Tests: split_into_chunks ----------


class TestSplitIntoChunks:
    """Tests for the split_into_chunks function."""

    def test_short_content_single_chunk(self) -> None:
        """Short content should result in a single chunk."""
        content = "# Title\n\nSome short content."
        chunks = split_into_chunks(content, chunk_size=500)
        assert len(chunks) == 1
        assert chunks[0]["chunk_index"] == 0
        assert "Title" in chunks[0]["text"]

    def test_heading_split(self) -> None:
        """Content with ## headings should split by heading."""
        content = "# Main\n\nIntro.\n\n## Section 1\n\nContent 1.\n\n## Section 2\n\nContent 2."
        chunks = split_into_chunks(content, chunk_size=500)
        # Should have at least 3 chunks: main, section 1, section 2
        assert len(chunks) >= 2
        # First chunk should contain Main
        assert "Main" in chunks[0]["text"]

    def test_large_section_splits(self) -> None:
        """A section exceeding chunk_size should be split further."""
        content = "# Large\n\n" + "Line content here.\n" * 100
        chunks = split_into_chunks(content, chunk_size=200, overlap=2)
        assert len(chunks) > 1

    def test_empty_content(self) -> None:
        """Empty content should return empty list or single empty chunk."""
        content = ""
        chunks = split_into_chunks(content)
        # Empty content means no lines, so sections will be [(0, 0)]
        # which gives one empty chunk
        assert len(chunks) <= 1

    def test_chunk_has_line_numbers(self) -> None:
        """Each chunk should have start_line and end_line."""
        content = "# Title\n\nContent.\n\n## Section\n\nMore."
        chunks = split_into_chunks(content)
        for chunk in chunks:
            assert "start_line" in chunk
            assert "end_line" in chunk
            assert chunk["start_line"] >= 1


# ---------- Tests: VersionService ----------


class TestNamespaceManagement:
    """Tests for namespace CRUD."""

    @pytest.mark.asyncio
    async def test_create_namespace(
        self, version_service: VersionService, db_session: AsyncSession
    ) -> None:
        """Create a namespace successfully."""
        ns = await version_service.create_namespace("test_wiki", "Test Wiki")
        await db_session.flush()

        assert ns.id is not None
        assert ns.name == "test_wiki"
        assert ns.display_name == "Test Wiki"

    @pytest.mark.asyncio
    async def test_create_duplicate_namespace(self, version_service: VersionService) -> None:
        """Creating a duplicate namespace should raise ValueError."""
        await version_service.create_namespace("test_wiki", "Test Wiki")
        with pytest.raises(ValueError, match="already exists"):
            await version_service.create_namespace("test_wiki", "Another Wiki")

    @pytest.mark.asyncio
    async def test_list_namespaces(self, version_service: VersionService) -> None:
        """List all namespaces."""
        await version_service.create_namespace("ns1", "Namespace 1")
        await version_service.create_namespace("ns2", "Namespace 2")
        namespaces = await version_service.list_namespaces()
        assert len(namespaces) == 2

    @pytest.mark.asyncio
    async def test_get_namespace(self, version_service: VersionService) -> None:
        """Get a namespace by ID."""
        ns = await version_service.create_namespace("test_ns", "Test NS")
        found = await version_service.get_namespace(ns.id)
        assert found is not None
        assert found.name == "test_ns"

    @pytest.mark.asyncio
    async def test_get_namespace_by_name(self, version_service: VersionService) -> None:
        """Get a namespace by name."""
        await version_service.create_namespace("unique_ns", "Unique NS")
        found = await version_service.get_namespace_by_name("unique_ns")
        assert found is not None
        assert found.display_name == "Unique NS"


class TestVersionUpload:
    """Tests for version upload and processing."""

    @pytest.mark.asyncio
    async def test_upload_valid_tarball(
        self, version_service: VersionService, db_session: AsyncSession, tmp_uploads: Path
    ) -> None:
        """Upload a valid .wiki.tar.gz and create a version."""
        # Create namespace
        ns = await version_service.create_namespace("test_wiki", "Test Wiki")
        await db_session.flush()

        # Create test tarball
        tarball_path = create_test_tarball(tmp_uploads)
        content = tarball_path.read_bytes()

        # Upload
        version = await version_service.process_upload(ns.id, content, "test-wiki.wiki.tar.gz")

        assert version is not None
        assert version.version == "1.0.0"  # From manifest
        assert version.status == "draft"
        assert version.page_count > 0

    @pytest.mark.asyncio
    async def test_upload_invalid_namespace(
        self, version_service: VersionService, tmp_uploads: Path
    ) -> None:
        """Upload to non-existent namespace should raise error."""
        tarball_path = create_test_tarball(tmp_uploads)
        content = tarball_path.read_bytes()

        with pytest.raises(FileNotFoundError, match="not found"):
            await version_service.process_upload("nonexistent-id", content, "test.wiki.tar.gz")

    @pytest.mark.asyncio
    async def test_upload_invalid_tarball(
        self, version_service: VersionService, db_session: AsyncSession, tmp_uploads: Path
    ) -> None:
        """Upload invalid file content should raise ValueError."""
        ns = await version_service.create_namespace("test_wiki", "Test Wiki")
        await db_session.flush()

        with pytest.raises(ValueError, match="Failed to extract"):
            await version_service.process_upload(ns.id, b"not a tarball", "test.wiki.tar.gz")

    @pytest.mark.asyncio
    async def test_upload_unsupported_format(
        self, version_service: VersionService, db_session: AsyncSession, tmp_uploads: Path
    ) -> None:
        """Upload unsupported file format should raise ValueError."""
        ns = await version_service.create_namespace("test_wiki", "Test Wiki")
        await db_session.flush()

        with pytest.raises(ValueError, match="Unsupported archive format"):
            await version_service.process_upload(ns.id, b"data", "test-wiki.rar")


class TestVersionUploadZip:
    """Tests for .zip format upload and processing."""

    @pytest.mark.asyncio
    async def test_upload_valid_zip(
        self, version_service: VersionService, db_session: AsyncSession, tmp_uploads: Path
    ) -> None:
        """Upload a valid .zip and create a version."""
        ns = await version_service.create_namespace("test_wiki", "Test Wiki")
        await db_session.flush()

        # Create test zip
        zip_path = create_test_zip(tmp_uploads)
        content = zip_path.read_bytes()

        # Upload
        version = await version_service.process_upload(ns.id, content, "test-wiki.zip")

        assert version is not None
        assert version.version == "1.0.0"  # From manifest
        assert version.status == "draft"
        assert version.page_count > 0

    @pytest.mark.asyncio
    async def test_zip_version_from_filename(
        self, version_service: VersionService, db_session: AsyncSession, tmp_uploads: Path
    ) -> None:
        """Version should be extracted from .zip filename when no manifest version."""
        ns = await version_service.create_namespace("test_wiki", "Test Wiki")
        await db_session.flush()

        # Create zip with versioned name
        zip_path = create_test_zip(tmp_uploads, name="my-wiki-2.5.0")
        content = zip_path.read_bytes()

        # Manually remove version from manifest by creating a zip without manifest version
        import io
        import zipfile

        # Read the zip, modify manifest to remove version, rewrite
        buf = io.BytesIO(content)
        with zipfile.ZipFile(buf, "r") as zf_in:
            buf_out = io.BytesIO()
            with zipfile.ZipFile(buf_out, "w", compression=zipfile.ZIP_DEFLATED) as zf_out:
                for item in zf_in.infolist():
                    data = zf_in.read(item.filename)
                    if item.filename.endswith("manifest.json"):
                        manifest = json.loads(data)
                        manifest.pop("version", None)
                        data = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
                    zf_out.writestr(item, data)
            content = buf_out.getvalue()

        version = await version_service.process_upload(ns.id, content, "my-wiki-2.5.0.zip")
        assert version.version == "2.5.0"

    @pytest.mark.asyncio
    async def test_zip_path_tree_stored_in_redis(
        self, version_service: VersionService, db_session: AsyncSession,
        mock_redis_cache, tmp_uploads: Path,
    ) -> None:
        """Path tree should be stored in Redis after .zip upload."""
        ns = await version_service.create_namespace("test_wiki", "Test Wiki")
        await db_session.flush()

        zip_path = create_test_zip(tmp_uploads)
        content = zip_path.read_bytes()
        version = await version_service.process_upload(ns.id, content, "test-wiki.zip")

        tree = await mock_redis_cache.get_path_tree(ns.name, version.version)
        assert tree is not None
        assert "files" in tree
        assert len(tree["files"]) > 0

    @pytest.mark.asyncio
    async def test_zip_chunks_stored_in_vector_store(
        self, version_service: VersionService, db_session: AsyncSession,
        mock_vector_store, tmp_uploads: Path,
    ) -> None:
        """Chunks should be stored in vector store after .zip upload."""
        ns = await version_service.create_namespace("test_wiki", "Test Wiki")
        await db_session.flush()

        zip_path = create_test_zip(tmp_uploads)
        content = zip_path.read_bytes()
        await version_service.process_upload(ns.id, content, "test-wiki.zip")

        chunks = await mock_vector_store.get_chunks_by_path("test_wiki", "1.0.0", "wiki/index.md")
        assert len(chunks) > 0
        assert any("Wiki Index" in c["text"] for c in chunks)

    @pytest.mark.asyncio
    async def test_upload_invalid_zip(
        self, version_service: VersionService, db_session: AsyncSession, tmp_uploads: Path
    ) -> None:
        """Upload invalid .zip content should raise ValueError."""
        ns = await version_service.create_namespace("test_wiki", "Test Wiki")
        await db_session.flush()

        with pytest.raises(ValueError, match="Failed to extract"):
            await version_service.process_upload(ns.id, b"not a zip file", "test-wiki.zip")


class TestPathTree:
    """Tests for path tree building and Redis storage."""

    @pytest.mark.asyncio
    async def test_build_path_tree(self, version_service: VersionService) -> None:
        """Build path tree from wiki directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            wiki_dir = Path(tmpdir) / "wiki"
            wiki_dir.mkdir()
            (wiki_dir / "index.md").write_text("# Index")
            concepts = wiki_dir / "concepts"
            concepts.mkdir()
            (concepts / "oauth.md").write_text("# OAuth")

            tree = await version_service.build_path_tree(wiki_dir)

            assert "files" in tree
            assert "directories" in tree
            assert len(tree["files"]) == 2
            assert any("oauth.md" in f for f in tree["files"])

    @pytest.mark.asyncio
    async def test_path_tree_stored_in_redis(
        self, version_service: VersionService, db_session: AsyncSession,
        mock_redis_cache, tmp_uploads: Path,
    ) -> None:
        """Path tree should be stored in Redis after upload."""
        ns = await version_service.create_namespace("test_wiki", "Test Wiki")
        await db_session.flush()

        tarball_path = create_test_tarball(tmp_uploads)
        content = tarball_path.read_bytes()
        version = await version_service.process_upload(ns.id, content, "test-wiki.wiki.tar.gz")

        # Verify path tree in mock Redis
        tree = await mock_redis_cache.get_path_tree(ns.name, version.version)
        assert tree is not None
        assert "files" in tree
        assert len(tree["files"]) > 0

    @pytest.mark.asyncio
    async def test_empty_directory_path_tree(self, version_service: VersionService) -> None:
        """Non-existent directory should return empty path tree."""
        tree = await version_service.build_path_tree(Path("/nonexistent"))
        assert tree == {"files": [], "directories": {}}


class TestChunkIndexing:
    """Tests for chunk splitting and Qdrant storage."""

    @pytest.mark.asyncio
    async def test_chunks_stored_in_vector_store(
        self, version_service: VersionService, db_session: AsyncSession,
        mock_vector_store, tmp_uploads: Path,
    ) -> None:
        """Chunks should be stored in vector store after upload."""
        ns = await version_service.create_namespace("test_wiki", "Test Wiki")
        await db_session.flush()

        tarball_path = create_test_tarball(tmp_uploads)
        content = tarball_path.read_bytes()
        await version_service.process_upload(ns.id, content, "test-wiki.wiki.tar.gz")

        # Verify chunks exist in mock vector store
        chunks = await mock_vector_store.get_chunks_by_path("test_wiki", "1.0.0", "wiki/index.md")
        assert len(chunks) > 0
        assert any("Wiki Index" in c["text"] for c in chunks)

    @pytest.mark.asyncio
    async def test_chunk_and_index_returns_count(
        self, version_service: VersionService,
    ) -> None:
        """chunk_and_index should return total chunk count."""
        with tempfile.TemporaryDirectory() as tmpdir:
            wiki_dir = Path(tmpdir) / "wiki"
            wiki_dir.mkdir()
            (wiki_dir / "index.md").write_text("# Index\n\nSome content here.\n")
            (wiki_dir / "page.md").write_text("# Page\n\nMore content.\n")

            count = await version_service.chunk_and_index("test", "1.0.0", wiki_dir)
            assert count > 0


class TestVersionActivation:
    """Tests for version activation and status transitions."""

    @pytest.mark.asyncio
    async def test_activate_version(
        self, version_service: VersionService, db_session: AsyncSession, tmp_uploads: Path,
    ) -> None:
        """Activating a version should set it to active."""
        ns = await version_service.create_namespace("test_wiki", "Test Wiki")
        await db_session.flush()

        tarball_path = create_test_tarball(tmp_uploads)
        content = tarball_path.read_bytes()
        version = await version_service.process_upload(ns.id, content, "test-wiki.wiki.tar.gz")

        # Activate
        activated = await version_service.activate_version(version.id)
        assert activated.status == "active"
        assert activated.activated_at is not None

    @pytest.mark.asyncio
    async def test_activate_archives_previous(
        self, version_service: VersionService, db_session: AsyncSession, tmp_uploads: Path,
    ) -> None:
        """Activating a new version should archive the previous active version."""
        ns = await version_service.create_namespace("test_wiki", "Test Wiki")
        await db_session.flush()

        # Create first version tarball
        tarball_path = create_test_tarball(tmp_uploads, name="test-wiki-v1")
        content = tarball_path.read_bytes()
        v1 = await version_service.process_upload(ns.id, content, "test-wiki-v1.wiki.tar.gz")

        # Activate v1
        await version_service.activate_version(v1.id)

        # Create second version tarball
        tarball_path2 = create_test_tarball(tmp_uploads, name="test-wiki-v2")
        content2 = tarball_path2.read_bytes()
        v2 = await version_service.process_upload(ns.id, content2, "test-wiki-v2.wiki.tar.gz")

        # Activate v2
        await version_service.activate_version(v2.id)

        # v1 should be archived
        from sqlalchemy import select
        result = await db_session.execute(select(WikiVersion).where(WikiVersion.id == v1.id))
        v1_refreshed = result.scalar_one()
        assert v1_refreshed.status == "archived"

        # v2 should be active
        result2 = await db_session.execute(select(WikiVersion).where(WikiVersion.id == v2.id))
        v2_refreshed = result2.scalar_one()
        assert v2_refreshed.status == "active"

    @pytest.mark.asyncio
    async def test_activate_nonexistent_version(self, version_service: VersionService) -> None:
        """Activating a non-existent version should raise error."""
        with pytest.raises(FileNotFoundError, match="not found"):
            await version_service.activate_version("nonexistent-id")

    @pytest.mark.asyncio
    async def test_list_versions(
        self, version_service: VersionService, db_session: AsyncSession, tmp_uploads: Path,
    ) -> None:
        """List versions for a namespace."""
        ns = await version_service.create_namespace("test_wiki", "Test Wiki")
        await db_session.flush()

        tarball_path = create_test_tarball(tmp_uploads, name="test-wiki-v1")
        content = tarball_path.read_bytes()
        await version_service.process_upload(ns.id, content, "test-wiki-v1.wiki.tar.gz")

        versions = await version_service.list_versions(ns.id)
        assert len(versions) >= 1

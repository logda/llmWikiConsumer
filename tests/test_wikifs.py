"""Comprehensive unit tests for WikiFs virtual filesystem."""

from typing import Any

import pytest

from app.core.wikifs import (
    FileNotFoundError,
    NotADirectoryError,
    ReadOnlyFilesystemError,
    WikiFs,
    WikiFsError,
    _normalize_path,
)
from tests.conftest import MockCache, MockVectorStore


# ============================================================
# Helper function tests
# ============================================================


class TestNormalizePath:
    """Tests for _normalize_path utility function."""

    def test_root_path(self) -> None:
        assert _normalize_path("/") == ""

    def test_empty_path(self) -> None:
        assert _normalize_path("") == ""

    def test_simple_path(self) -> None:
        assert _normalize_path("wiki/concepts") == "wiki/concepts"

    def test_leading_slash(self) -> None:
        assert _normalize_path("/wiki/concepts") == "wiki/concepts"

    def test_trailing_slash(self) -> None:
        assert _normalize_path("wiki/concepts/") == "wiki/concepts"

    def test_double_slash(self) -> None:
        assert _normalize_path("wiki//concepts") == "wiki/concepts"

    def test_dot_components(self) -> None:
        assert _normalize_path("wiki/./concepts") == "wiki/concepts"

    def test_dotdot_components(self) -> None:
        assert _normalize_path("wiki/concepts/../entities") == "wiki/entities"


# ============================================================
# ls tests
# ============================================================


class TestLs:
    """Tests for WikiFs.ls() method."""

    @pytest.mark.asyncio
    async def test_ls_root(self, wikifs: WikiFs) -> None:
        """List root directory contents."""
        entries = await wikifs.ls("/")
        # Root only has wiki/ subdirectory since all files are nested under wiki/
        assert "wiki/" in entries

    @pytest.mark.asyncio
    async def test_ls_subdirectory(self, wikifs: WikiFs) -> None:
        """List subdirectory contents."""
        entries = await wikifs.ls("wiki/concepts")
        assert "oauth.md" in entries
        assert "rbac.md" in entries
        assert "sso.md" in entries

    @pytest.mark.asyncio
    async def test_ls_with_leading_slash(self, wikifs: WikiFs) -> None:
        """ls with leading slash should work same as without."""
        entries = await wikifs.ls("/wiki/concepts")
        assert len(entries) == 3

    @pytest.mark.asyncio
    async def test_ls_nonexistent_path(self, wikifs: WikiFs) -> None:
        """ls on nonexistent path should raise FileNotFoundError."""
        with pytest.raises(FileNotFoundError, match="No such file or directory"):
            await wikifs.ls("nonexistent")

    @pytest.mark.asyncio
    async def test_ls_on_file(self, wikifs: WikiFs) -> None:
        """ls on a file path should raise NotADirectoryError."""
        with pytest.raises(NotADirectoryError, match="Not a directory"):
            await wikifs.ls("wiki/concepts/oauth.md")

    @pytest.mark.asyncio
    async def test_ls_empty_filesystem(self, empty_wikifs: WikiFs) -> None:
        """ls on empty filesystem root should return empty list."""
        entries = await empty_wikifs.ls("/")
        assert entries == []


# ============================================================
# cat tests
# ============================================================


class TestCat:
    """Tests for WikiFs.cat() method."""

    @pytest.mark.asyncio
    async def test_cat_single_chunk(self, wikifs: WikiFs) -> None:
        """Read a page with a single chunk."""
        content = await wikifs.cat("wiki/concepts/rbac.md")
        assert "RBAC" in content
        assert "Role-Based Access Control" in content

    @pytest.mark.asyncio
    async def test_cat_multiple_chunks(self, wikifs: WikiFs) -> None:
        """Read a page with multiple chunks - should be joined by newline."""
        content = await wikifs.cat("wiki/concepts/oauth.md")
        assert "OAuth 2.0" in content
        assert "Authorization Request" in content
        assert "Token Exchange" in content

    @pytest.mark.asyncio
    async def test_cat_cache_hit(self, wikifs: WikiFs, cache: MockCache) -> None:
        """Second cat call should hit cache and not query vector store."""
        # First call populates cache
        content1 = await wikifs.cat("wiki/index.md")
        assert "# Wiki Index" in content1

        # Verify cache was populated
        cached = await cache.get("test", "v1", "wiki/index.md")
        assert cached is not None
        assert "# Wiki Index" in cached

        # Second call should return same content from cache
        content2 = await wikifs.cat("wiki/index.md")
        assert content1 == content2

    @pytest.mark.asyncio
    async def test_cat_nonexistent_file(self, wikifs: WikiFs) -> None:
        """cat on nonexistent file should raise FileNotFoundError."""
        with pytest.raises(FileNotFoundError, match="No such file or directory"):
            await wikifs.cat("nonexistent.md")

    @pytest.mark.asyncio
    async def test_cat_on_directory(self, wikifs: WikiFs) -> None:
        """cat on a directory should raise FileNotFoundError with 'Is a directory'."""
        with pytest.raises(FileNotFoundError, match="Is a directory"):
            await wikifs.cat("wiki/concepts")

    @pytest.mark.asyncio
    async def test_cat_file_with_no_chunks(
        self, path_tree: dict
    ) -> None:
        """cat on a file that exists in path tree but has no chunks returns empty string."""
        vs = MockVectorStore({})
        empty_cache = MockCache()
        fs = WikiFs("test", "v1", path_tree, vs, empty_cache)
        content = await fs.cat("wiki/concepts/oauth.md")
        assert content == ""

    @pytest.mark.asyncio
    async def test_cat_with_leading_slash(self, wikifs: WikiFs) -> None:
        """cat with leading slash should normalize path."""
        content = await wikifs.cat("/wiki/index.md")
        assert "# Wiki Index" in content


# ============================================================
# grep tests
# ============================================================


class TestGrep:
    """Tests for WikiFs.grep() method."""

    @pytest.mark.asyncio
    async def test_grep_fixed_string(self, wikifs: WikiFs) -> None:
        """Search with a fixed string pattern."""
        matches = await wikifs.grep("OAuth")
        assert len(matches) > 0
        # Should find OAuth in multiple files
        files_with_matches = {m["file"] for m in matches}
        assert "wiki/concepts/oauth.md" in files_with_matches

    @pytest.mark.asyncio
    async def test_grep_with_path_filter(self, wikifs: WikiFs) -> None:
        """Search within a specific directory."""
        matches = await wikifs.grep("OAuth", path="wiki/concepts")
        # All matches should be under wiki/concepts
        for match in matches:
            assert match["file"].startswith("wiki/concepts/")

    @pytest.mark.asyncio
    async def test_grep_regex(self, wikifs: WikiFs) -> None:
        """Search with a regex pattern."""
        matches = await wikifs.grep(r"auth.*token", flags="i")
        assert len(matches) > 0
        for match in matches:
            assert "file" in match
            assert "line" in match
            assert "content" in match

    @pytest.mark.asyncio
    async def test_grep_case_insensitive(self, wikifs: WikiFs) -> None:
        """Search with case-insensitive flag."""
        matches = await wikifs.grep("oauth", flags="i")
        assert len(matches) > 0

    @pytest.mark.asyncio
    async def test_grep_no_matches(self, wikifs: WikiFs) -> None:
        """Search with a pattern that has no matches."""
        matches = await wikifs.grep("ZZZNONEXISTENTZZZ")
        assert matches == []

    @pytest.mark.asyncio
    async def test_grep_returns_line_numbers(self, wikifs: WikiFs) -> None:
        """Each grep match should have a line number."""
        matches = await wikifs.grep("OAuth")
        for match in matches:
            assert isinstance(match["line"], int)
            assert match["line"] >= 1

    @pytest.mark.asyncio
    async def test_grep_returns_content(self, wikifs: WikiFs) -> None:
        """Each grep match should have the matched line content."""
        matches = await wikifs.grep("OAuth")
        for match in matches:
            assert "OAuth" in match["content"]


# ============================================================
# find tests
# ============================================================


class TestFind:
    """Tests for WikiFs.find() method."""

    @pytest.mark.asyncio
    async def test_find_all(self, wikifs: WikiFs) -> None:
        """Find all files with default glob pattern."""
        matches = await wikifs.find("/")
        assert len(matches) == 8  # All files in sample data

    @pytest.mark.asyncio
    async def test_find_by_extension(self, wikifs: WikiFs) -> None:
        """Find all .md files."""
        matches = await wikifs.find("/", name_pattern="*.md")
        assert len(matches) == 8
        for f in matches:
            assert f.endswith(".md")

    @pytest.mark.asyncio
    async def test_find_in_subdirectory(self, wikifs: WikiFs) -> None:
        """Find files within a specific subdirectory."""
        matches = await wikifs.find("wiki/concepts", "*.md")
        assert len(matches) == 3
        for f in matches:
            assert f.startswith("wiki/concepts/")

    @pytest.mark.asyncio
    async def test_find_specific_filename(self, wikifs: WikiFs) -> None:
        """Find a specific file by exact name."""
        matches = await wikifs.find("/", name_pattern="oauth.md")
        assert len(matches) == 1
        assert matches[0] == "wiki/concepts/oauth.md"

    @pytest.mark.asyncio
    async def test_find_glob_pattern(self, wikifs: WikiFs) -> None:
        """Find files matching a glob pattern."""
        matches = await wikifs.find("/", name_pattern="oauth*")
        assert len(matches) == 1
        assert "oauth" in matches[0]

    @pytest.mark.asyncio
    async def test_find_no_matches(self, wikifs: WikiFs) -> None:
        """Find with a pattern that matches nothing."""
        matches = await wikifs.find("/", name_pattern="nonexistent*")
        assert matches == []

    @pytest.mark.asyncio
    async def test_find_results_are_sorted(self, wikifs: WikiFs) -> None:
        """Find results should be sorted."""
        matches = await wikifs.find("wiki/concepts")
        assert matches == sorted(matches)


# ============================================================
# tree tests
# ============================================================


class TestTree:
    """Tests for WikiFs.tree() method."""

    @pytest.mark.asyncio
    async def test_tree_root(self, wikifs: WikiFs) -> None:
        """Display tree from root."""
        result = await wikifs.tree("/")
        assert "/" in result
        assert "wiki/" in result

    @pytest.mark.asyncio
    async def test_tree_subdirectory(self, wikifs: WikiFs) -> None:
        """Display tree from a subdirectory."""
        result = await wikifs.tree("wiki/concepts")
        assert "wiki/concepts" in result
        assert "oauth.md" in result
        assert "rbac.md" in result

    @pytest.mark.asyncio
    async def test_tree_depth_limit(self, wikifs: WikiFs) -> None:
        """Tree with depth=1 should only show immediate children."""
        result = await wikifs.tree("/", depth=1)
        lines = result.split("\n")
        # Root line + immediate children, no deeper nesting
        assert len(lines) > 1

    @pytest.mark.asyncio
    async def test_tree_nonexistent_path(self, wikifs: WikiFs) -> None:
        """Tree on nonexistent path should raise FileNotFoundError."""
        with pytest.raises(FileNotFoundError, match="No such file or directory"):
            await wikifs.tree("nonexistent")

    @pytest.mark.asyncio
    async def test_tree_has_tree_connectors(self, wikifs: WikiFs) -> None:
        """Tree output should use Unicode tree connectors."""
        result = await wikifs.tree("wiki/concepts")
        # Should contain tree-drawing characters
        assert "├──" in result or "└──" in result


# ============================================================
# head tests
# ============================================================


class TestHead:
    """Tests for WikiFs.head() method."""

    @pytest.mark.asyncio
    async def test_head_default_lines(self, wikifs: WikiFs) -> None:
        """Head with default 20 lines."""
        content = await wikifs.head("wiki/concepts/oauth.md")
        lines = content.split("\n")
        assert len(lines) <= 20
        assert "OAuth 2.0" in content

    @pytest.mark.asyncio
    async def test_head_custom_lines(self, wikifs: WikiFs) -> None:
        """Head with custom line count."""
        content = await wikifs.head("wiki/concepts/oauth.md", lines=1)
        lines = content.split("\n")
        assert len(lines) == 1

    @pytest.mark.asyncio
    async def test_head_nonexistent_file(self, wikifs: WikiFs) -> None:
        """Head on nonexistent file should raise FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            await wikifs.head("nonexistent.md")

    @pytest.mark.asyncio
    async def test_head_on_directory(self, wikifs: WikiFs) -> None:
        """Head on a directory should raise FileNotFoundError."""
        with pytest.raises(FileNotFoundError, match="Is a directory"):
            await wikifs.head("wiki/concepts")


# ============================================================
# Error scenario tests
# ============================================================


class TestErrorScenarios:
    """Tests for error handling in WikiFs."""

    @pytest.mark.asyncio
    async def test_readonly_filesystem_error(self) -> None:
        """ReadOnlyFilesystemError should contain EROFS message."""
        err = ReadOnlyFilesystemError("write")
        assert "EROFS" in str(err)

    @pytest.mark.asyncio
    async def test_file_not_found_error_is_wikifs_error(self) -> None:
        """FileNotFoundError should be a subclass of WikiFsError."""
        assert issubclass(FileNotFoundError, WikiFsError)

    @pytest.mark.asyncio
    async def test_not_a_directory_error_is_wikifs_error(self) -> None:
        """NotADirectoryError should be a subclass of WikiFsError."""
        assert issubclass(NotADirectoryError, WikiFsError)

    @pytest.mark.asyncio
    async def test_readonly_error_is_wikifs_error(self) -> None:
        """ReadOnlyFilesystemError should be a subclass of WikiFsError."""
        assert issubclass(ReadOnlyFilesystemError, WikiFsError)

    @pytest.mark.asyncio
    async def test_ls_deep_nonexistent(self, wikifs: WikiFs) -> None:
        """ls on deeply nested nonexistent path should raise error."""
        with pytest.raises(FileNotFoundError):
            await wikifs.ls("wiki/concepts/oauth/deeply/nested")

    @pytest.mark.asyncio
    async def test_cat_file_not_in_path_tree(
        self, path_tree: dict, cache: MockCache
    ) -> None:
        """cat on a file not in path tree should raise FileNotFoundError."""
        vs = MockVectorStore()
        fs = WikiFs("test", "v1", path_tree, vs, cache)
        with pytest.raises(FileNotFoundError):
            await fs.cat("wiki/nonexistent.md")


# ============================================================
# Properties and initialization tests
# ============================================================


class TestWikiFsProperties:
    """Tests for WikiFs properties and initialization."""

    def test_namespace_property(self, wikifs: WikiFs) -> None:
        """Namespace property should return the initialized namespace."""
        assert wikifs.namespace == "test"

    def test_version_property(self, wikifs: WikiFs) -> None:
        """Version property should return the initialized version."""
        assert wikifs.version == "v1"

    def test_custom_cache_ttl(self, path_tree: dict) -> None:
        """WikiFs should accept custom cache TTL."""
        fs = WikiFs(
            namespace="test",
            version="v1",
            path_tree=path_tree,
            vector_store=MockVectorStore(),
            cache=MockCache(),
            cache_ttl=7200,
        )
        assert fs._cache_ttl == 7200
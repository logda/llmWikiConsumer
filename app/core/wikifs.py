"""WikiFs - Virtual filesystem for LLM Agent to interact with wiki knowledge base.

Translates filesystem-like commands (ls, cat, grep, find, tree, head) into
database queries against Redis (path tree) and Qdrant (chunk storage),
with Redis caching for page content.

This is a read-only virtual filesystem — all write operations raise EROFS.
"""

import fnmatch
import logging
import re
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class VectorStoreProtocol(Protocol):
    """Protocol for vector store interaction (decouples WikiFs from Qdrant)."""

    async def get_chunks_by_path(
        self, namespace: str, version: str, path: str
    ) -> list[dict[str, Any]]: ...

    async def search_by_metadata(
        self,
        namespace: str,
        version: str,
        path_prefix: str | None = None,
        contains: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]: ...


class CacheProtocol(Protocol):
    """Protocol for cache interaction (decouples WikiFs from Redis)."""

    async def get(self, namespace: str, version: str, path: str) -> str | None: ...

    async def set(self, namespace: str, version: str, path: str, content: str) -> None: ...


class WikiFsError(Exception):
    """Base exception for WikiFs operations."""


class FileNotFoundError(WikiFsError):
    """Raised when a path does not exist in the virtual filesystem."""


class NotADirectoryError(WikiFsError):
    """Raised when a directory operation is attempted on a file."""


class ReadOnlyFilesystemError(WikiFsError):
    """Raised when a write operation is attempted on the read-only filesystem."""

    def __init__(self, operation: str = "write") -> None:
        super().__init__(f"Read-only filesystem: {operation} operation not permitted (EROFS)")


def _normalize_path(path: str) -> str:
    """Normalize a virtual filesystem path.

    - Removes leading/trailing slashes
    - Collapses multiple slashes
    - Resolves . and ..
    - Returns empty string for root
    """
    if not path or path == "/":
        return ""

    # Remove leading slash
    if path.startswith("/"):
        path = path[1:]

    # Collapse multiple slashes
    parts = path.split("/")
    resolved: list[str] = []
    for part in parts:
        if part == "" or part == ".":
            continue
        if part == "..":
            if resolved:
                resolved.pop()
        else:
            resolved.append(part)

    return "/".join(resolved)


class WikiFs:
    """Virtual filesystem - translates filesystem commands into database queries.

    Provides a read-only filesystem interface for LLM Agents to explore
    wiki knowledge bases. All directory information comes from an in-memory
    path tree, and file content is fetched from the vector store with
    Redis caching.
    """

    def __init__(
        self,
        namespace: str,
        version: str,
        path_tree: dict[str, Any],
        vector_store: VectorStoreProtocol,
        cache: CacheProtocol,
        cache_ttl: int = 3600,
    ) -> None:
        """Initialize WikiFs with a specific namespace and version.

        Args:
            namespace: Wiki namespace identifier.
            version: Wiki version identifier.
            path_tree: Path tree data structure loaded from Redis.
                Expected format:
                {
                    "files": ["wiki/index.md", ...],
                    "directories": {
                        "wiki/": ["index.md", "summaries/", ...],
                        ...
                    }
                }
            vector_store: Vector store client for chunk retrieval.
            cache: Cache client for page content caching.
            cache_ttl: Cache TTL in seconds for page content.
        """
        self._namespace = namespace
        self._version = version
        self._path_tree = path_tree
        self._vector_store = vector_store
        self._cache = cache
        self._cache_ttl = cache_ttl

        # Build lookup sets for fast validation
        self._files: set[str] = set(path_tree.get("files", []))
        self._directories: dict[str, list[str]] = path_tree.get("directories", {})

    @property
    def namespace(self) -> str:
        """Current namespace."""
        return self._namespace

    @property
    def version(self) -> str:
        """Current version."""
        return self._version

    def _resolve_directory_key(self, path: str) -> str:
        """Convert a path to a directory key used in the path tree.

        Directory keys always end with '/'.
        Root directory key is "".
        """
        normalized = _normalize_path(path)
        if not normalized:
            return ""
        return normalized + "/"

    def _is_directory(self, path: str) -> bool:
        """Check if a path is a directory."""
        key = self._resolve_directory_key(path)
        return key in self._directories or key == ""

    def _is_file(self, path: str) -> bool:
        """Check if a path is a file."""
        normalized = _normalize_path(path)
        return normalized in self._files

    def _exists(self, path: str) -> bool:
        """Check if a path exists (file or directory)."""
        return self._is_file(path) or self._is_directory(path)

    async def ls(self, path: str = "/") -> list[str]:
        """List directory contents from the in-memory path tree.

        Args:
            path: Directory path to list. Defaults to root "/".

        Returns:
            List of entry names in the directory.

        Raises:
            FileNotFoundError: If the path does not exist.
            NotADirectoryError: If the path is not a directory.
        """
        normalized = _normalize_path(path)

        if not self._exists(normalized):
            raise FileNotFoundError(f"No such file or directory: {path}")

        if not self._is_directory(normalized):
            raise NotADirectoryError(f"Not a directory: {path}")

        key = self._resolve_directory_key(normalized)
        entries = self._directories.get(key, [])

        # For root, also check if there are top-level entries
        if key == "" and not entries:
            # Derive entries from files and directories
            top_entries: set[str] = set()
            for f in self._files:
                parts = f.split("/")
                if parts:
                    top_entries.add(parts[0] + "/" if len(parts) > 1 else parts[0])
            for d in self._directories:
                if d:
                    parts = d.rstrip("/").split("/")
                    if parts:
                        top_entries.add(parts[0] + "/")
            return sorted(top_entries)

        return list(entries)

    async def cat(self, path: str) -> str:
        """Read complete page content - check cache → query chunks → sort → join → cache.

        Args:
            path: File path to read.

        Returns:
            Complete page content as a string.

        Raises:
            FileNotFoundError: If the path does not exist or is a directory.
        """
        normalized = _normalize_path(path)

        if not self._is_file(normalized):
            if self._is_directory(normalized):
                raise FileNotFoundError(f"Is a directory: {path}")
            raise FileNotFoundError(f"No such file or directory: {path}")

        # Step 1: Check cache
        cached = await self._cache.get(self._namespace, self._version, normalized)
        if cached is not None:
            logger.debug("Cache hit for %s", normalized)
            return cached

        # Step 2: Query vector store for chunks
        chunks = await self._vector_store.get_chunks_by_path(
            self._namespace, self._version, normalized
        )

        if not chunks:
            logger.warning("No chunks found for %s in vector store", normalized)
            return ""

        # Step 3: Sort by chunk_index
        chunks.sort(key=lambda c: c.get("chunk_index", 0))

        # Step 4: Join into complete text
        content = "\n".join(c.get("text", "") for c in chunks)

        # Step 5: Store in cache
        await self._cache.set(self._namespace, self._version, normalized, content)
        logger.debug("Cached page %s (%d bytes)", normalized, len(content))

        # Step 6: Return
        return content

    async def grep(
        self,
        pattern: str,
        path: str = "/",
        flags: str = "",
    ) -> list[dict[str, Any]]:
        """Search content using a three-stage pipeline: coarse filter → prefetch → fine filter.

        Args:
            pattern: Search pattern (fixed string or regex).
            path: Directory path to search within. Defaults to root.
            flags: Regex flags string. "i" for case-insensitive.

        Returns:
            List of matches, each with "file", "line", and "content" keys.

        Example:
            >>> matches = await wikifs.grep("OAuth", "/wiki/concepts")
            >>> matches = await wikifs.grep(r"auth.*token", flags="i")
        """
        normalized = _normalize_path(path)

        # Determine if path prefix filtering applies
        path_prefix = normalized if normalized else None

        # Stage 1: Coarse filter - metadata-based filtering in vector store
        chunks = await self._vector_store.search_by_metadata(
            self._namespace,
            self._version,
            path_prefix=path_prefix,
            contains=pattern if not _is_regex(pattern) else None,
            limit=1000,
        )

        if not chunks:
            return []

        # Stage 2: Prefetch - group chunks by file, assemble page content
        file_chunks: dict[str, list[dict[str, Any]]] = {}
        for chunk in chunks:
            fpath = chunk.get("page_path", "")
            if fpath not in file_chunks:
                file_chunks[fpath] = []
            file_chunks[fpath].append(chunk)

        # Assemble full content per file
        file_contents: dict[str, str] = {}
        for fpath, fchunks in file_chunks.items():
            fchunks.sort(key=lambda c: c.get("chunk_index", 0))
            file_contents[fpath] = "\n".join(c.get("text", "") for c in fchunks)

        # Also try cache for files that might not have been in the coarse results
        # but could still match the pattern

        # Stage 3: Fine filter - in-memory regex/string matching
        re_flags = 0
        if "i" in flags:
            re_flags |= re.IGNORECASE

        try:
            regex = re.compile(pattern, re_flags)
        except re.error:
            # If pattern is not a valid regex, treat as fixed string
            if "i" in flags:
                regex = re.compile(re.escape(pattern), re_flags)
            else:
                regex = re.compile(re.escape(pattern), re_flags)

        matches: list[dict[str, Any]] = []
        for fpath, content in sorted(file_contents.items()):
            for line_num, line in enumerate(content.split("\n"), start=1):
                if regex.search(line):
                    matches.append({
                        "file": fpath,
                        "line": line_num,
                        "content": line,
                    })

        return matches

    async def find(self, path: str = "/", name_pattern: str = "*") -> list[str]:
        """Find files matching a glob pattern in the path tree.

        Args:
            path: Directory to search within. Defaults to root.
            name_pattern: Glob pattern for file names. Defaults to "*".

        Returns:
            List of matching file paths.
        """
        normalized = _normalize_path(path)
        prefix = normalized + "/" if normalized else ""

        matching: list[str] = []
        for fpath in self._files:
            # Filter by path prefix
            if prefix and not fpath.startswith(prefix):
                continue

            # Match the filename part against the glob pattern
            filename = fpath.split("/")[-1] if "/" in fpath else fpath
            if fnmatch.fnmatch(filename, name_pattern):
                matching.append(fpath)

        return sorted(matching)

    async def tree(self, path: str = "/", depth: int = 3) -> str:
        """Display directory tree structure.

        Args:
            path: Root path for the tree. Defaults to root.
            depth: Maximum depth to display. Defaults to 3.

        Returns:
            String representation of the directory tree.

        Raises:
            FileNotFoundError: If the path does not exist.
        """
        normalized = _normalize_path(path)

        if not self._exists(normalized):
            raise FileNotFoundError(f"No such file or directory: {path}")

        lines: list[str] = [normalized if normalized else "/"]
        self._build_tree(normalized, "", depth, lines)
        return "\n".join(lines)

    def _build_tree(self, base_path: str, prefix: str, depth: int, lines: list[str]) -> None:
        """Recursively build tree lines.

        Args:
            base_path: Current directory path (normalized, no trailing slash).
            prefix: Visual prefix for tree lines.
            depth: Remaining depth to traverse.
            lines: Accumulator for tree output lines.
        """
        if depth <= 0:
            return

        key = self._resolve_directory_key(base_path)
        entries = self._directories.get(key, [])

        if not entries and key == "":
            # Derive entries for root
            top_entries: set[str] = set()
            for f in self._files:
                parts = f.split("/")
                if parts:
                    top_entries.add(parts[0] + "/" if len(parts) > 1 else parts[0])
            for d in self._directories:
                if d:
                    parts = d.rstrip("/").split("/")
                    if parts:
                        top_entries.add(parts[0] + "/")
            entries = sorted(top_entries)

        for i, entry in enumerate(entries):
            is_last = i == len(entries) - 1
            connector = "└── " if is_last else "├── "
            lines.append(f"{prefix}{connector}{entry}")

            # Recurse into directories
            if entry.endswith("/"):
                child_path = (base_path + "/" + entry.rstrip("/")) if base_path else entry.rstrip("/")
                extension = "    " if is_last else "│   "
                self._build_tree(child_path, prefix + extension, depth - 1, lines)

    async def head(self, path: str, lines: int = 20) -> str:
        """Read the first N lines of a page.

        Args:
            path: File path to read.
            lines: Number of lines to return. Defaults to 20.

        Returns:
            The first N lines of the file.

        Raises:
            FileNotFoundError: If the path does not exist or is a directory.
        """
        content = await self.cat(path)
        content_lines = content.split("\n")
        return "\n".join(content_lines[:lines])


def _is_regex(pattern: str) -> bool:
    """Check if a pattern appears to be a regex (contains special characters)."""
    regex_chars = set(r"\.^$*+?{}[]|()")
    return any(c in regex_chars for c in pattern)
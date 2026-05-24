"""Pytest configuration and shared fixtures for WikiFs tests."""

from typing import Any

import pytest

from app.core.wikifs import WikiFs

# ---------- Sample Path Tree Data ----------

SAMPLE_PATH_TREE: dict[str, Any] = {
    "files": [
        "wiki/index.md",
        "wiki/summaries/project-overview.md",
        "wiki/entities/auth-service.md",
        "wiki/entities/user-service.md",
        "wiki/concepts/oauth.md",
        "wiki/concepts/rbac.md",
        "wiki/concepts/sso.md",
        "wiki/synthesis/architecture.md",
    ],
    "directories": {
        "wiki/": ["index.md", "summaries/", "entities/", "concepts/", "synthesis/"],
        "wiki/summaries/": ["project-overview.md"],
        "wiki/entities/": ["auth-service.md", "user-service.md"],
        "wiki/concepts/": ["oauth.md", "rbac.md", "sso.md"],
        "wiki/synthesis/": ["architecture.md"],
    },
}


# ---------- Sample Chunk Data ----------

SAMPLE_CHUNKS: dict[str, list[dict[str, Any]]] = {
    "wiki/index.md": [
        {"text": "# Wiki Index", "chunk_index": 0, "page_path": "wiki/index.md"},
        {"text": "Welcome to the project wiki.", "chunk_index": 1, "page_path": "wiki/index.md"},
    ],
    "wiki/concepts/oauth.md": [
        {
            "text": "# OAuth 2.0\n\nOAuth 2.0 is an authorization framework.",
            "chunk_index": 0,
            "page_path": "wiki/concepts/oauth.md",
        },
        {
            "text": (
                "The OAuth flow involves these steps:"
                "\n1. Authorization Request\n2. Token Exchange"
            ),
            "chunk_index": 1,
            "page_path": "wiki/concepts/oauth.md",
        },
    ],
    "wiki/concepts/rbac.md": [
        {
            "text": "# RBAC\n\nRole-Based Access Control assigns permissions to roles.",
            "chunk_index": 0,
            "page_path": "wiki/concepts/rbac.md",
        },
    ],
    "wiki/concepts/sso.md": [
        {
            "text": "# SSO\n\nSingle Sign-On allows one login for multiple services.",
            "chunk_index": 0,
            "page_path": "wiki/concepts/sso.md",
        },
    ],
    "wiki/entities/auth-service.md": [
        {
            "text": "# Auth Service\n\nHandles OAuth token validation and RBAC checks.",
            "chunk_index": 0,
            "page_path": "wiki/entities/auth-service.md",
        },
    ],
    "wiki/entities/user-service.md": [
        {
            "text": "# User Service\n\nManages user profiles and role assignments.",
            "chunk_index": 0,
            "page_path": "wiki/entities/user-service.md",
        },
    ],
    "wiki/summaries/project-overview.md": [
        {
            "text": "# Project Overview\n\nThis project uses OAuth for authentication.",
            "chunk_index": 0,
            "page_path": "wiki/summaries/project-overview.md",
        },
    ],
    "wiki/synthesis/architecture.md": [
        {
            "text": "# Architecture\n\nMicroservices architecture with OAuth gateway.",
            "chunk_index": 0,
            "page_path": "wiki/synthesis/architecture.md",
        },
    ],
}


# ---------- Mock Factories ----------


class MockVectorStore:
    """Mock vector store that returns chunks from SAMPLE_CHUNKS."""

    def __init__(self, chunks: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self._chunks = chunks if chunks is not None else SAMPLE_CHUNKS

    async def get_chunks_by_path(
        self, namespace: str, version: str, path: str
    ) -> list[dict[str, Any]]:
        return list(self._chunks.get(path, []))

    async def search_by_metadata(
        self,
        namespace: str,
        version: str,
        path_prefix: str | None = None,
        contains: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for fpath, chunks in self._chunks.items():
            if path_prefix and not fpath.startswith(path_prefix):
                continue
            for chunk in chunks:
                if contains and contains.lower() in chunk.get("text", "").lower():
                    results.append(chunk)
                elif not contains:
                    results.append(chunk)
        return results[:limit]


class MockCache:
    """Mock cache with an in-memory dict."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    def _key(self, namespace: str, version: str, path: str) -> str:
        return f"page:{namespace}:{version}:{path}"

    async def get(self, namespace: str, version: str, path: str) -> str | None:
        return self._store.get(self._key(namespace, version, path))

    async def set(self, namespace: str, version: str, path: str, content: str) -> None:
        self._store[self._key(namespace, version, path)] = content


# ---------- Fixtures ----------


@pytest.fixture
def path_tree() -> dict[str, Any]:
    """Provide the sample path tree."""
    return SAMPLE_PATH_TREE


@pytest.fixture
def vector_store() -> MockVectorStore:
    """Provide a mock vector store with sample chunks."""
    return MockVectorStore()


@pytest.fixture
def cache() -> MockCache:
    """Provide a mock cache."""
    return MockCache()


@pytest.fixture
def wikifs(vector_store: MockVectorStore, cache: MockCache, path_tree: dict) -> WikiFs:
    """Provide a WikiFs instance with all mocks."""
    return WikiFs(
        namespace="test",
        version="v1",
        path_tree=path_tree,
        vector_store=vector_store,
        cache=cache,
    )


@pytest.fixture
def empty_wikifs() -> WikiFs:
    """Provide a WikiFs instance with empty path tree."""
    return WikiFs(
        namespace="test",
        version="v1",
        path_tree={"files": [], "directories": {}},
        vector_store=MockVectorStore({}),
        cache=MockCache(),
    )

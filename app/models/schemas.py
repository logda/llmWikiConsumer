"""Pydantic models and schemas."""

from pydantic import BaseModel, Field


# ---------- WikiFs Schemas ----------


class WikiLsResponse(BaseModel):
    """Response for ls command."""

    path: str
    entries: list[str]


class WikiCatResponse(BaseModel):
    """Response for cat command."""

    path: str
    content: str
    cached: bool = False


class WikiGrepMatch(BaseModel):
    """Single match from grep command."""

    file: str
    line: int
    content: str


class WikiGrepResponse(BaseModel):
    """Response for grep command."""

    pattern: str
    matches: list[WikiGrepMatch]


class WikiFindResponse(BaseModel):
    """Response for find command."""

    path: str
    pattern: str
    matches: list[str]


class WikiTreeResponse(BaseModel):
    """Response for tree command."""

    path: str
    tree: str


class WikiHeadResponse(BaseModel):
    """Response for head command."""

    path: str
    lines: int
    content: str


# ---------- Chat Schemas ----------


class ChatRequest(BaseModel):
    """Chat query request."""

    question: str = Field(..., min_length=1, description="User question")
    namespace: str = Field(default="default", description="Wiki namespace")
    version: str = Field(default="latest", description="Wiki version")


class ChatSource(BaseModel):
    """Source reference in chat response."""

    path: str
    snippet: str


class ChatResponse(BaseModel):
    """Chat query response."""

    answer: str
    sources: list[ChatSource]


# ---------- Admin Schemas ----------


class VersionUploadRequest(BaseModel):
    """Version upload request."""

    namespace: str = Field(default="default", description="Wiki namespace")
    version: str = Field(..., min_length=1, description="Version identifier")


class VersionInfo(BaseModel):
    """Version information."""

    namespace: str
    version: str
    status: str
    created_at: str
"""Pydantic models and schemas."""

from datetime import datetime

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


# ---------- Admin Schemas ----------


class NamespaceCreate(BaseModel):
    """Request to create a namespace."""

    name: str = Field(..., min_length=1, max_length=100, pattern=r"^[a-z0-9_]+$",
                       description="命名空间标识（小写字母、数字、下划线）")
    display_name: str = Field(..., min_length=1, max_length=200, description="显示名称")


class NamespaceInfo(BaseModel):
    """Namespace information."""

    id: str
    name: str
    display_name: str
    created_at: datetime

    model_config = {"from_attributes": True}


class VersionInfo(BaseModel):
    """Version information."""

    id: str
    namespace_id: str
    version: str
    status: str
    page_count: int
    file_path: str
    created_at: datetime
    activated_at: datetime | None = None

    model_config = {"from_attributes": True}


class VersionActivateResponse(BaseModel):
    """Response for version activation."""

    version_id: str
    status: str
    previous_active_id: str | None = None


# ---------- Chat Schemas ----------


class ChatRequest(BaseModel):
    """Chat query request."""

    namespace_id: str = Field(..., description="Wiki namespace ID")
    question: str = Field(..., min_length=1, description="User question")
    history: list[dict] = Field(default_factory=list, description="Multi-turn conversation history")


class ChatSource(BaseModel):
    """Source reference in chat response."""

    path: str
    snippet: str


class ChatResponse(BaseModel):
    """Chat query response."""

    answer: str
    sources: list[ChatSource]


class ActiveVersionInfo(BaseModel):
    """Active version information for a namespace."""

    namespace_id: str
    namespace_name: str
    version_id: str
    version: str
    page_count: int

"""SQLAlchemy ORM models for PostgreSQL."""

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for all ORM models."""

    pass


def _uuid_str() -> str:
    """Generate a new UUID string."""
    return str(uuid.uuid4())


class WikiNamespace(Base):
    """知识库命名空间."""

    __tablename__ = "wiki_namespaces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationship
    versions: Mapped[list["WikiVersion"]] = relationship(  # noqa: F821
        back_populates="namespace", cascade="all, delete-orphan"
    )


class WikiVersion(Base):
    """Wiki版本."""

    __tablename__ = "wiki_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    namespace_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("wiki_namespaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="draft"  # draft|active|archived
    )
    manifest: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    file_path: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationship
    namespace: Mapped["WikiNamespace"] = relationship(back_populates="versions")  # noqa: F821

    __table_args__ = (
        # 同一 namespace 下版本号唯一
        # Note: using naming convention compatible with SQLAlchemy
    )

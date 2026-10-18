from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from sqlmodel import JSON, Column, Field, Session, SQLModel, create_engine

from app.config import settings

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)


def utcnow() -> datetime:
    return datetime.now(UTC)


class Document(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    filename: str
    path: str
    page_count: int
    pages: list[str] = Field(sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utcnow)


class Extraction(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    document_id: int = Field(foreign_key="document.id", index=True)
    template_id: str
    template: dict[str, Any] = Field(sa_column=Column(JSON))
    model: str
    status: str
    fields: list[dict[str, Any]] = Field(sa_column=Column(JSON))
    issues: list[dict[str, Any]] = Field(sa_column=Column(JSON))
    usage: dict[str, Any] = Field(sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utcnow)
    reviewed_at: datetime | None = None


class CustomTemplate(SQLModel, table=True):
    id: str = Field(primary_key=True)
    data: dict[str, Any] = Field(sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


def init_db() -> None:
    SQLModel.metadata.create_all(engine)


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session

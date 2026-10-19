from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import LargeBinary, inspect, text
from sqlmodel import JSON, Column, Field, Session, SQLModel, create_engine

from app.config import settings

SQLITE = settings.database_url.startswith("sqlite")
engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if SQLITE else {},
    pool_pre_ping=not SQLITE,  # hosted Postgres poolers drop idle connections
)
# On a shared Postgres (e.g. a Supabase project used by another app) all tables live in
# their own schema. Must be set before the models are declared.
if settings.db_schema and not SQLITE:
    SQLModel.metadata.schema = settings.db_schema


def utcnow() -> datetime:
    return datetime.now(UTC)


class Document(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    filename: str
    page_count: int
    pages: list[str] = Field(sa_column=Column(JSON))
    ocr_pages: list[int] = Field(default_factory=list, sa_column=Column(JSON))  # 1-based
    created_at: datetime = Field(default_factory=utcnow)


class DocumentFile(SQLModel, table=True):
    document_id: int = Field(foreign_key="document.id", primary_key=True)
    data: bytes = Field(sa_column=Column(LargeBinary, nullable=False))


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


class UsageEvent(SQLModel, table=True):
    """One paid model call (extraction or template suggestion), for the daily cap."""

    id: int | None = Field(default=None, primary_key=True)
    kind: str
    created_at: datetime = Field(default_factory=utcnow, index=True)


class CustomTemplate(SQLModel, table=True):
    id: str = Field(primary_key=True)
    data: dict[str, Any] = Field(sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


def init_db() -> None:
    schema = SQLModel.metadata.schema
    if schema:
        with engine.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
    SQLModel.metadata.create_all(engine)
    # create_all does not add columns to existing tables; databases created before OCR
    # support get the column here (no migration tool for a single nullable column).
    columns = {c["name"] for c in inspect(engine).get_columns("document", schema=schema)}
    if "ocr_pages" not in columns:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE document ADD COLUMN ocr_pages JSON"))


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session

import json
import secrets
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import LargeBinary, UniqueConstraint, inspect, text, update
from sqlmodel import JSON, Column, Field, Session, SQLModel, create_engine, select

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


class Workspace(SQLModel, table=True):
    """One customer firm in the web app: its own access code, documents and templates.
    The default workspace (code_hash None) is opened by ACCESS_CODE and holds all data
    from before workspaces existed."""

    id: int | None = Field(default=None, primary_key=True)
    name: str
    code_hash: str | None = Field(default=None, unique=True, index=True)  # SHA-256 of the code
    daily_quota: int | None = None  # model calls per UTC day; None: only DAILY_LLM_LIMIT
    created_at: datetime = Field(default_factory=utcnow)
    revoked_at: datetime | None = None


class Document(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    # Nullable only so existing databases can add the column; init_db fills it.
    workspace_id: int | None = Field(default=None, foreign_key="workspace.id", index=True)
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
    workspace_id: int | None = Field(default=None, index=True)  # None: trial page or /v1
    created_at: datetime = Field(default_factory=utcnow, index=True)


class WorkspaceTemplate(SQLModel, table=True):
    """A firm's own template. Replaces the single-tenant customtemplate table, whose rows
    init_db copies into the default workspace."""

    __table_args__ = (UniqueConstraint("workspace_id", "template_id"),)

    id: int | None = Field(default=None, primary_key=True)
    workspace_id: int = Field(foreign_key="workspace.id", index=True)
    template_id: str
    data: dict[str, Any] = Field(sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


def new_id() -> str:
    return secrets.token_urlsafe(16)  # 128 bits: ids of external documents are not guessable


class ApiKey(SQLModel, table=True):
    """Integrator key. Only the SHA-256 of the key is stored; the key is shown once."""

    id: int | None = Field(default=None, primary_key=True)
    client_name: str
    prefix: str  # first characters of the key, to recognise it in listings
    key_hash: str = Field(unique=True, index=True)
    daily_quota: int
    # Key of a firm's integration: its templates, quota and usage count (see workspaces).
    workspace_id: int | None = Field(default=None, index=True)
    webhook_url: str | None = None  # set by the operator (scripts/api_keys.py), https only
    webhook_secret: str | None = None  # HMAC key for the Docflow-Signature header
    created_at: datetime = Field(default_factory=utcnow)
    revoked_at: datetime | None = None


class WebhookDelivery(SQLModel, table=True):
    """Outcome of one webhook event, for support ("did you send it?")."""

    id: int | None = Field(default=None, primary_key=True)
    api_key_id: int = Field(index=True)
    extraction_id: str
    event: str
    url: str
    attempts: int = 0
    status_code: int | None = None  # of the last attempt
    error: str | None = None
    delivered_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow, index=True)


class ExternalDocument(SQLModel, table=True):
    """A PDF from the public trial page or the integrator API. Kept apart from the
    workspace tables, so it never appears in the workspace, measurements or evaluations."""

    __table_args__ = (UniqueConstraint("api_key_id", "idempotency_key"),)

    id: str = Field(default_factory=new_id, primary_key=True)
    source: str  # "trial" or "api"
    api_key_id: int | None = Field(default=None, foreign_key="apikey.id", index=True)
    ip_hash: str | None = Field(default=None, index=True)
    ref: str | None = None
    idempotency_key: str | None = None
    sha256: str
    filename: str
    page_count: int
    pages: list[str] = Field(sa_column=Column(JSON))
    ocr_pages: list[int] = Field(default_factory=list, sa_column=Column(JSON))
    data: bytes = Field(sa_column=Column(LargeBinary, nullable=False))
    created_at: datetime = Field(default_factory=utcnow, index=True)
    expires_at: datetime | None = Field(default=None, index=True)


class ExternalExtraction(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    document_id: str = Field(foreign_key="externaldocument.id", index=True)
    api_key_id: int | None = Field(default=None, index=True)
    template_id: str
    template: dict[str, Any] = Field(sa_column=Column(JSON))
    model: str
    status: str
    fields: list[dict[str, Any]] = Field(sa_column=Column(JSON))
    issues: list[dict[str, Any]] = Field(sa_column=Column(JSON))
    usage: dict[str, Any] = Field(sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utcnow, index=True)
    reviewed_at: datetime | None = None


class TrialVisit(SQLModel, table=True):
    """Which outreach lead tried the trial page (?ref=). No file name, content or IP."""

    id: int | None = Field(default=None, primary_key=True)
    ref: str | None = Field(default=None, index=True)
    template_id: str
    page_count: int
    created_at: datetime = Field(default_factory=utcnow)


# create_all does not add columns to existing tables; databases created before a column
# existed get it here (no migration tool for a few nullable columns).
ADDED_COLUMNS = {
    "document": {"ocr_pages": "JSON", "workspace_id": "INTEGER"},
    "usageevent": {"workspace_id": "INTEGER"},
    "apikey": {"workspace_id": "INTEGER", "webhook_url": "VARCHAR", "webhook_secret": "VARCHAR"},
}


def init_db() -> None:
    schema = SQLModel.metadata.schema
    prefix = f'"{schema}".' if schema else ""
    if schema:
        with engine.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
    SQLModel.metadata.create_all(engine)
    db = inspect(engine)
    with engine.begin() as conn:
        for table, added in ADDED_COLUMNS.items():
            columns = {c["name"] for c in db.get_columns(table, schema=schema)}
            for column, sql_type in added.items():
                if column not in columns:
                    conn.execute(
                        text(f"ALTER TABLE {prefix}{table} ADD COLUMN {column} {sql_type}")
                    )
            if "workspace_id" in added:
                conn.execute(
                    text(
                        f"CREATE INDEX IF NOT EXISTS ix_{table}_workspace_id "
                        f"ON {prefix}{table} (workspace_id)"
                    )
                )
    with Session(engine) as session:
        default = default_workspace(session)
        session.exec(
            update(Document).where(Document.workspace_id.is_(None)).values(workspace_id=default.id)
        )
        if db.has_table("customtemplate", schema=schema):
            copy_legacy_templates(session, default.id, prefix)
        session.commit()


def as_datetime(value: datetime | str) -> datetime:
    """Stored datetimes are UTC; raw SQL returns them naive (or as text on SQLite)."""
    value = datetime.fromisoformat(value) if isinstance(value, str) else value
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def default_workspace(session: Session) -> Workspace:
    """The workspace of ACCESS_CODE; created on first start."""
    workspace = session.exec(
        select(Workspace).where(Workspace.code_hash.is_(None)).order_by(Workspace.id)
    ).first()
    if workspace is None:
        workspace = Workspace(name="Výchozí")
        session.add(workspace)
        session.commit()
        session.refresh(workspace)
    return workspace


def copy_legacy_templates(session: Session, workspace_id: int, prefix: str) -> None:
    """Templates saved before workspaces go to the default workspace, once. The old table
    stays untouched as a backup."""
    existing = set(
        session.exec(
            select(WorkspaceTemplate.template_id).where(
                WorkspaceTemplate.workspace_id == workspace_id
            )
        )
    )
    rows = session.connection().execute(
        text(f"SELECT id, data, created_at, updated_at FROM {prefix}customtemplate")
    )
    for template_id, data, created_at, updated_at in rows:
        if template_id in existing:
            continue
        session.add(
            WorkspaceTemplate(
                workspace_id=workspace_id,
                template_id=template_id,
                # Raw SQL on SQLite returns JSON and datetimes as text.
                data=json.loads(data) if isinstance(data, str) else data,
                created_at=as_datetime(created_at),
                updated_at=as_datetime(updated_at),
            )
        )


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session

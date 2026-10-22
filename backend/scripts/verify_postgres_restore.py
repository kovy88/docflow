"""Run a destructive restore drill ONLY inside a disposable local PostgreSQL cluster.

Requires initdb, pg_ctl, createdb, pg_dump and pg_restore on PATH. Never uses .env
database credentials or connects to an existing cluster. Also exercises XLSX export
and document deletion against the restored PostgreSQL data.
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SEED = """
from app.db import *
from app.templates import load_templates
init_db()
with Session(engine) as session:
    doc = Document(workspace_id=default_workspace(session).id, filename="nájem-obnova.pdf",
        page_count=1, pages=["fiktivní smlouva"])
    session.add(doc)
    session.flush()
    session.add(DocumentFile(document_id=doc.id, data=b"%PDF-restore-test"))
    template = load_templates()["lease"]
    session.add(Extraction(document_id=doc.id, template_id="lease", template=template.model_dump(),
        model="restore-test", status="approved", fields=[dict(key=f.key, label=f.label,
        type=f.type, value=None) for f in template.fields], issues=[], usage={}, reviewed_at=utcnow()))
    session.commit()
"""

VERIFY = """
import hashlib, json
from fastapi.testclient import TestClient
from sqlmodel import select
from app.db import *
from app.main import app
with Session(engine) as session:
    rows = []
    for model in (Workspace, Document, DocumentFile, Extraction, WorkspaceTemplate, UsageEvent):
        rows.append([r.model_dump() for r in session.exec(select(model).order_by(model.__table__.primary_key.columns.values()[0]))])
    digest = hashlib.sha256(json.dumps(rows, sort_keys=True, default=str).encode()).hexdigest()
with TestClient(app) as client:
    assert client.post("/exports/xlsx", json={"extraction_ids":[1]}).status_code == 401
    headers = {"X-Access-Code": "local-restore-drill"}
    response = client.post("/exports/xlsx", json={"extraction_ids":[1]}, headers=headers)
    assert response.status_code == 200 and response.content.startswith(b"PK")
print(digest)
"""

DELETE = """
from fastapi.testclient import TestClient
from app.main import app
with TestClient(app) as client:
    headers = {"X-Access-Code": "local-restore-drill"}
    assert client.delete("/documents/1", headers=headers).status_code == 204
    assert client.get("/documents/1/file", headers=headers).status_code == 404
    assert client.get("/extractions/1", headers=headers).status_code == 404
"""


def main():
    for name in ("initdb", "pg_ctl", "createdb", "pg_dump", "pg_restore"):
        if not shutil.which(name):
            raise SystemExit(f"Missing PostgreSQL executable: {name}")
    with tempfile.TemporaryDirectory(prefix="docflow-pg-") as directory:
        root = Path(directory)
        cluster = root / "cluster"
        env = {k: v for k, v in os.environ.items() if not k.startswith("PG")}
        env.update(PGHOST=directory, PGPORT="55439")

        def run(args):
            return subprocess.run(args, env=env, capture_output=True, text=True, check=True)

        run(
            [
                "initdb",
                "-D",
                str(cluster),
                "--auth-local=trust",
                "--auth-host=reject",
                "--no-locale",
                "--encoding=UTF8",
            ]
        )
        try:
            run(
                [
                    "pg_ctl",
                    "-D",
                    str(cluster),
                    "-l",
                    str(root / "postgres.log"),
                    "-o",
                    f"-k {directory} -h '' -p 55439",
                    "-w",
                    "start",
                ]
            )
            for db in ("docflow_source", "docflow_restored"):
                run(["createdb", db])

            def python(code, database):
                child_env = env | {
                    "DATABASE_URL": f"postgresql+psycopg:///{database}?host={directory}&port=55439",
                    "DB_SCHEMA": "docflow",
                    "PILOT_MODE": "true",
                    "ACCESS_CODE": "local-restore-drill",
                    "LLM_PROVIDER": "openai",
                    "OPENAI_API_KEY": "",
                }
                return subprocess.run(
                    [sys.executable, "-c", code],
                    env=child_env,
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout.strip()

            python(SEED, "docflow_source")
            before = python(VERIFY, "docflow_source")
            archive = root / "pilot.dump"
            run(
                [
                    "pg_dump",
                    "--format=custom",
                    "--schema=docflow",
                    "--no-owner",
                    "--no-acl",
                    "--file",
                    str(archive),
                    "docflow_source",
                ]
            )
            run(
                [
                    "pg_restore",
                    "--no-owner",
                    "--no-acl",
                    "--single-transaction",
                    "--exit-on-error",
                    "--dbname=docflow_restored",
                    str(archive),
                ]
            )
            after = python(VERIFY, "docflow_restored")
            if before != after:
                raise RuntimeError("Restored data differ from the original snapshot")
            python(DELETE, "docflow_restored")
            print(
                "PASS: Postgres snapshot restored; all table contents match; authenticated XLSX export and deletion work."
            )
        finally:
            subprocess.run(
                ["pg_ctl", "-D", str(cluster), "-m", "immediate", "-w", "stop"],
                env=env,
                capture_output=True,
            )


if __name__ == "__main__":
    main()

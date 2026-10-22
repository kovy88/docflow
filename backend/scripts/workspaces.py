"""Customer workspaces of the web app. Uses DATABASE_URL (backend/.env or environment).

uv run python scripts/workspaces.py create "Správa nemovitostí s.r.o." --quota 50
uv run python scripts/workspaces.py list                 # documents and model calls per month
uv run python scripts/workspaces.py list --month 2026-10
uv run python scripts/workspaces.py revoke 3
uv run python scripts/workspaces.py intake 3 --template invoice --senders @firma.cz   # or --off
uv run python scripts/workspaces.py inbox 3              # last e-mails of the intake
"""

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session, func, select  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import (  # noqa: E402
    Document,
    IntakeMessage,
    UsageEvent,
    Workspace,
    engine,
    init_db,
    utcnow,
)
from app.intake import new_token  # noqa: E402
from app.templates import load_templates  # noqa: E402
from app.workspaces import create_workspace, custom_templates  # noqa: E402


def month_range(month: str) -> tuple[datetime, datetime]:
    start = datetime.strptime(month, "%Y-%m").replace(tzinfo=UTC)
    if start.month == 12:
        return start, start.replace(year=start.year + 1, month=1)
    return start, start.replace(month=start.month + 1)


def count(session: Session, model, workspace_id: int, start: datetime, end: datetime) -> int:
    query = (
        select(func.count())
        .select_from(model)
        .where(
            model.workspace_id == workspace_id, model.created_at >= start, model.created_at < end
        )
    )
    return session.exec(query).one()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="new workspace; the code is printed only once")
    create.add_argument("name")
    create.add_argument("--quota", type=int, help="model calls per UTC day (default: none)")
    listing = commands.add_parser("list", help="workspaces with usage, the basis for invoicing")
    listing.add_argument("--month", default=f"{utcnow():%Y-%m}", help="YYYY-MM, default: now")
    revoke = commands.add_parser("revoke", help="end access; the data stays")
    revoke.add_argument("workspace_id", type=int)
    intake = commands.add_parser("intake", help="e-mail address for PDFs (new on each call)")
    intake.add_argument("workspace_id", type=int)
    intake.add_argument("--template", help="extract right away with this template")
    intake.add_argument("--senders", help="allowed senders: addresses or @domains, comma-separated")
    intake.add_argument("--off", action="store_true")
    inbox = commands.add_parser("inbox", help="last e-mails read for the workspace")
    inbox.add_argument("workspace_id", type=int)
    args = parser.parse_args()

    init_db()
    with Session(engine) as session:
        if args.command == "create":
            workspace, code = create_workspace(session, args.name, args.quota)
            quota = f"denní kvóta {workspace.daily_quota}" if workspace.daily_quota else "bez kvóty"
            print(f"Pracovní prostor: {workspace.name} (id {workspace.id}), {quota}")
            print(f"Přístupový kód (zobrazí se jen teď, předejte ho bezpečně):\n{code}")
        elif args.command == "list":
            start, end = month_range(args.month)
            print(f"Využití za {args.month}: nahrané dokumenty / volání modelu")
            for workspace in session.exec(select(Workspace).order_by(Workspace.id)):
                documents = count(session, Document, workspace.id, start, end)
                calls = count(session, UsageEvent, workspace.id, start, end)
                if workspace.code_hash is None:
                    state = "ACCESS_CODE"
                elif workspace.revoked_at:
                    state = f"ukončen {workspace.revoked_at:%Y-%m-%d}"
                else:
                    state = "aktivní"
                quota = f"kvóta {workspace.daily_quota}/den" if workspace.daily_quota else "-"
                mail = f"  e-mail {workspace.intake_token}" if workspace.intake_token else ""
                print(
                    f"{workspace.id:>3}  {workspace.name}  {documents} / {calls}  {quota}  {state}"
                    f"{mail}"
                )
        elif args.command in ("intake", "inbox"):
            workspace = session.get(Workspace, args.workspace_id)
            if workspace is None:
                sys.exit(f"Pracovní prostor {args.workspace_id} neexistuje.")
            if args.command == "inbox":
                query = (
                    select(IntakeMessage)
                    .where(IntakeMessage.workspace_id == workspace.id)
                    .order_by(IntakeMessage.id.desc())
                    .limit(20)
                )
                for m in session.exec(query):
                    print(
                        f"{m.created_at:%Y-%m-%d %H:%M} UTC  {m.sender or '-'}  {m.status}  "
                        f"PDF {m.documents}  {m.subject or ''}  {m.detail or ''}"
                    )
                return
            if args.off:
                workspace.intake_token = workspace.intake_template_id = None
                workspace.intake_senders = None
                session.add(workspace)
                session.commit()
                print(f"Příjem e-mailem pro {workspace.name} je vypnutý.")
                return
            templates = load_templates() | custom_templates(session, workspace.id)
            if args.template and args.template not in templates:
                sys.exit(f"Šablona {args.template!r} neexistuje: {', '.join(templates)}")
            workspace.intake_token = new_token()
            workspace.intake_template_id = args.template
            workspace.intake_senders = args.senders
            session.add(workspace)
            session.commit()
            if not settings.intake_enabled:
                print("Pozor: INTAKE_* proměnné nejsou nastavené, schránka se nečte.")
            local, _, domain = (settings.intake_address or "dokumenty@example.cz").partition("@")
            print(f"Adresa pro {workspace.name}: {local}+{workspace.intake_token}@{domain}")
            print(
                f"Šablona: {args.template or 'žádná (jen uložit)'}; odesílatelé: {args.senders or 'kdokoli'}"
            )
        else:
            workspace = session.get(Workspace, args.workspace_id)
            if workspace is None:
                sys.exit(f"Pracovní prostor {args.workspace_id} neexistuje.")
            if workspace.code_hash is None:
                sys.exit("Výchozí prostor otevírá ACCESS_CODE; změňte ho v nastavení serveru.")
            if workspace.revoked_at is None:
                workspace.revoked_at = utcnow()
                session.add(workspace)
                session.commit()
            print(f"Přístup do prostoru {workspace.id} ({workspace.name}) je ukončený.")


if __name__ == "__main__":
    main()

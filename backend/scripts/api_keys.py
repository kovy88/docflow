"""Integrator API keys and trial visits. Uses DATABASE_URL (backend/.env or environment).

uv run python scripts/api_keys.py create "CB Sped s.r.o." --quota 200 [--workspace 2]
uv run python scripts/api_keys.py list
uv run python scripts/api_keys.py revoke 3
uv run python scripts/api_keys.py webhook 3 https://erp.example.cz/docflow   # or --off
uv run python scripts/api_keys.py deliveries 3    # last webhook events of the key
uv run python scripts/api_keys.py trials          # who tried /vyzkouset (?ref=)
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session, select  # noqa: E402

from app.api_v1 import create_key  # noqa: E402
from app.db import (  # noqa: E402
    ApiKey,
    TrialVisit,
    WebhookDelivery,
    Workspace,
    engine,
    init_db,
    utcnow,
)
from app.webhooks import new_secret  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="new key; printed only once")
    create.add_argument("client_name")
    create.add_argument("--quota", type=int, default=100, help="extractions per UTC day")
    create.add_argument(
        "--workspace", type=int, help="firm workspace: its templates, quota and usage count"
    )
    commands.add_parser("list")
    revoke = commands.add_parser("revoke")
    revoke.add_argument("key_id", type=int)
    webhook = commands.add_parser("webhook", help="set the URL; prints a new signing secret")
    webhook.add_argument("key_id", type=int)
    webhook.add_argument("url", nargs="?")
    webhook.add_argument("--off", action="store_true")
    deliveries = commands.add_parser("deliveries")
    deliveries.add_argument("key_id", type=int)
    commands.add_parser("trials")
    args = parser.parse_args()

    init_db()
    with Session(engine) as session:
        if args.command == "create":
            if args.workspace is not None and session.get(Workspace, args.workspace) is None:
                sys.exit(f"Pracovní prostor {args.workspace} neexistuje.")
            key, plain = create_key(session, args.client_name, args.quota)
            if args.workspace is not None:
                key.workspace_id = args.workspace
                session.add(key)
                session.commit()
            print(f"Klient: {key.client_name} (id {key.id}), denní kvóta {key.daily_quota}")
            print(f"API klíč (zobrazí se jen teď, uložte ho bezpečně):\n{plain}")
        elif args.command == "list":
            for key in session.exec(select(ApiKey).order_by(ApiKey.id)):
                state = f"odvolán {key.revoked_at:%Y-%m-%d}" if key.revoked_at else "aktivní"
                firm = f"  prostor {key.workspace_id}" if key.workspace_id else ""
                hook = f"  webhook {key.webhook_url}" if key.webhook_url else ""
                print(
                    f"{key.id:>3}  {key.prefix}…  {key.client_name}  "
                    f"kvóta {key.daily_quota}/den  {state}{firm}{hook}"
                )
        elif args.command == "revoke":
            key = session.get(ApiKey, args.key_id)
            if key is None:
                sys.exit(f"Klíč {args.key_id} neexistuje.")
            if key.revoked_at is None:
                key.revoked_at = utcnow()
                session.add(key)
                session.commit()
            print(f"Klíč {key.id} ({key.client_name}) je odvolaný.")
        elif args.command == "webhook":
            key = session.get(ApiKey, args.key_id)
            if key is None:
                sys.exit(f"Klíč {args.key_id} neexistuje.")
            if args.off:
                key.webhook_url = key.webhook_secret = None
                print(f"Webhook klíče {key.id} je vypnutý.")
            elif not args.url or not args.url.startswith("https://"):
                sys.exit("Zadejte adresu webhooku začínající https:// (nebo --off).")
            else:
                key.webhook_url, key.webhook_secret = args.url, new_secret()
                print(f"Webhook klíče {key.id}: {key.webhook_url}")
                print(f"Podpisový klíč (zobrazí se jen teď):\n{key.webhook_secret}")
            session.add(key)
            session.commit()
        elif args.command == "deliveries":
            query = (
                select(WebhookDelivery)
                .where(WebhookDelivery.api_key_id == args.key_id)
                .order_by(WebhookDelivery.id.desc())
                .limit(20)
            )
            for d in session.exec(query):
                result = "doručeno" if d.delivered_at else f"nedoručeno ({d.error or '-'})"
                print(
                    f"{d.created_at:%Y-%m-%d %H:%M} UTC  {d.event}  {d.extraction_id[:8]}…  "
                    f"HTTP {d.status_code or '-'}  pokusy {d.attempts}  {result}"
                )
        else:
            for visit in session.exec(select(TrialVisit).order_by(TrialVisit.id)):
                print(
                    f"{visit.created_at:%Y-%m-%d %H:%M} UTC  ref={visit.ref or '-'}  "
                    f"{visit.template_id}  {visit.page_count} str."
                )


if __name__ == "__main__":
    main()

"""Integrator API keys and trial visits. Uses DATABASE_URL (backend/.env or environment).

uv run python scripts/api_keys.py create "CB Sped s.r.o." --quota 200
uv run python scripts/api_keys.py list
uv run python scripts/api_keys.py revoke 3
uv run python scripts/api_keys.py trials          # who tried /vyzkouset (?ref=)
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session, select  # noqa: E402

from app.api_v1 import create_key  # noqa: E402
from app.db import ApiKey, TrialVisit, engine, init_db, utcnow  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="new key; printed only once")
    create.add_argument("client_name")
    create.add_argument("--quota", type=int, default=100, help="extractions per UTC day")
    commands.add_parser("list")
    revoke = commands.add_parser("revoke")
    revoke.add_argument("key_id", type=int)
    commands.add_parser("trials")
    args = parser.parse_args()

    init_db()
    with Session(engine) as session:
        if args.command == "create":
            key, plain = create_key(session, args.client_name, args.quota)
            print(f"Klient: {key.client_name} (id {key.id}), denní kvóta {key.daily_quota}")
            print(f"API klíč (zobrazí se jen teď, uložte ho bezpečně):\n{plain}")
        elif args.command == "list":
            for key in session.exec(select(ApiKey).order_by(ApiKey.id)):
                state = f"odvolán {key.revoked_at:%Y-%m-%d}" if key.revoked_at else "aktivní"
                print(
                    f"{key.id:>3}  {key.prefix}…  {key.client_name}  "
                    f"kvóta {key.daily_quota}/den  {state}"
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
        else:
            for visit in session.exec(select(TrialVisit).order_by(TrialVisit.id)):
                print(
                    f"{visit.created_at:%Y-%m-%d %H:%M} UTC  ref={visit.ref or '-'}  "
                    f"{visit.template_id}  {visit.page_count} str."
                )


if __name__ == "__main__":
    main()

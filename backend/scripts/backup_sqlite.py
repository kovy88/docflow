"""Consistent SQLite snapshots and restore drills; never overwrite an existing file.

Postgres pilot backups use pg_dump/pg_restore; see docs/pilot/operations.md.
"""

import argparse
import os
import sqlite3
from pathlib import Path


def snapshot(source: Path, target: Path) -> dict[str, int]:
    if not source.is_file():
        raise ValueError("Source database does not exist")
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    try:
        with sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True) as src:
            with sqlite3.connect(target) as dst:
                src.backup(dst)
                if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("Snapshot integrity check failed")
                if dst.execute("PRAGMA foreign_key_check").fetchall():
                    raise ValueError("Snapshot foreign key check failed")
                names = [
                    r[0] for r in dst.execute("SELECT name FROM sqlite_master WHERE type='table'")
                ]
                return {
                    name: dst.execute(
                        'SELECT COUNT(*) FROM "' + name.replace('"', '""') + '"'
                    ).fetchone()[0]
                    for name in names
                }
    except Exception:
        target.unlink(missing_ok=True)
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    print(snapshot(args.source, args.target))

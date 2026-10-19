import importlib.util
import sqlite3
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "backup_sqlite", Path(__file__).parents[1] / "scripts/backup_sqlite.py"
)
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)


def test_snapshot_restore_preserves_pdf_and_review(tmp_path):
    source, snapshot, restored = [
        tmp_path / name for name in ("source.db", "backup.db", "restored.db")
    ]
    with sqlite3.connect(source) as db:
        db.execute("CREATE TABLE document (id INTEGER PRIMARY KEY, pdf BLOB, review TEXT)")
        db.execute("INSERT INTO document VALUES (1, ?, ?)", (b"%PDF-test", "schváleno"))
    assert backup.snapshot(source, snapshot) == {"document": 1}
    assert backup.snapshot(snapshot, restored) == {"document": 1}
    with sqlite3.connect(restored) as db:
        assert db.execute("SELECT pdf, review FROM document").fetchone() == (
            b"%PDF-test",
            "schváleno",
        )
    with pytest.raises(FileExistsError):
        backup.snapshot(source, restored)
    assert snapshot.stat().st_mode & 0o777 == 0o600


def test_missing_source_does_not_create_empty_backup(tmp_path):
    with pytest.raises(ValueError):
        backup.snapshot(tmp_path / "missing", tmp_path / "target")
    assert not (tmp_path / "target").exists()

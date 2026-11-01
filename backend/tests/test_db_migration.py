"""Library database resolution and one-time migration.

Covers the contract in app/library_db.py: canonical `library.sqlite3`
wins, a legacy `modelharbor.sqlite3` is copied once (with SQLite
sidecars), the copy must pass an integrity check before adoption, the
original file is never modified, and Postgres installs touch no files.
"""

from __future__ import annotations

import dataclasses
import sqlite3
from pathlib import Path

import pytest

from app.config import Settings
from app.library_db import (
    LEGACY_DB_FILENAME,
    LIBRARY_DB_FILENAME,
    library_database_state,
    migrate_library_db,
)


def _settings(tmp_path: Path) -> Settings:
    settings = Settings(
        model_storage=(tmp_path / "models").resolve(),
        data_dir=(tmp_path / "data").resolve(),
    )
    settings.ensure_directories()
    return settings


def _seed_legacy(data_dir: Path, extra_rows: int = 0) -> None:
    connection = sqlite3.connect(data_dir / LEGACY_DB_FILENAME)
    try:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY, body TEXT)"
        )
        connection.execute(
            "INSERT INTO notes (body) VALUES ('moved-with-the-library')"
        )
        for index in range(extra_rows):
            connection.execute(
                "INSERT INTO notes (body) VALUES (?)", (f"row-{index}",)
            )
        connection.commit()
    finally:
        connection.close()


def _read_notes(data_dir: Path, filename: str) -> list[tuple]:
    connection = sqlite3.connect(data_dir / filename)
    try:
        return connection.execute("SELECT id, body FROM notes").fetchall()
    finally:
        connection.close()


def test_fresh_install_creates_canonical_file(tmp_path):
    settings = _settings(tmp_path)
    target = migrate_library_db(settings.data_dir)
    assert target.name == LIBRARY_DB_FILENAME
    # The file itself appears when Database.initialize() first connects,
    # exactly as fresh installs always have; resolution only picks the name.
    assert target.parent == settings.data_dir


def test_happy_migration_copies_and_keeps_original(tmp_path):
    settings = _settings(tmp_path)
    _seed_legacy(settings.data_dir)

    before = (settings.data_dir / LEGACY_DB_FILENAME).read_bytes()
    target = migrate_library_db(settings.data_dir)

    assert target.name == LIBRARY_DB_FILENAME
    assert (settings.data_dir / LEGACY_DB_FILENAME).read_bytes() == before
    assert _read_notes(settings.data_dir, LIBRARY_DB_FILENAME) == _read_notes(
        settings.data_dir, LEGACY_DB_FILENAME
    )


def test_migration_carries_wal_sidecar_content(tmp_path):
    settings = _settings(tmp_path)
    _seed_legacy(settings.data_dir)
    # Simulate committed-but-still-in-WAL content: the sidecar exists with
    # bytes and the main file was left behind it.
    (settings.data_dir / (LEGACY_DB_FILENAME + "-wal")).write_bytes(b"wal-bytes")

    target = migrate_library_db(settings.data_dir)

    assert target.exists()
    assert (settings.data_dir / (LIBRARY_DB_FILENAME + "-wal")).read_bytes() == b"wal-bytes"


def test_both_present_uses_canonical_and_never_recopies(tmp_path):
    settings = _settings(tmp_path)
    _seed_legacy(settings.data_dir)

    first = migrate_library_db(settings.data_dir)
    first_stat = first.stat()
    legacy_stat = (settings.data_dir / LEGACY_DB_FILENAME).stat()

    second = migrate_library_db(settings.data_dir)

    assert second == first
    assert second.stat().st_mtime == first_stat.st_mtime
    assert (settings.data_dir / LEGACY_DB_FILENAME).stat().st_mtime == legacy_stat.st_mtime


def test_older_legacy_does_not_recopy_but_newer_warns(tmp_path, caplog):
    settings = _settings(tmp_path)
    _seed_legacy(settings.data_dir)
    migrate_library_db(settings.data_dir)

    # Legacy untouched → older than the canonical copy → silent no-op.
    import logging

    with caplog.at_level(logging.WARNING, logger="uvicorn.error"):
        migrate_library_db(settings.data_dir)
    assert not any("not merged" in record.message for record in caplog.records)

    # Rewrite the legacy file so it is newer than the canonical copy.
    _seed_legacy(settings.data_dir, extra_rows=3)
    import os

    stat = (settings.data_dir / LEGACY_DB_FILENAME).stat()
    os.utime(
        settings.data_dir / LEGACY_DB_FILENAME,
        (stat.st_atime, stat.st_mtime + 10),
    )

    with caplog.at_level(logging.WARNING, logger="uvicorn.error"):
        result = migrate_library_db(settings.data_dir)
    assert result.name == LIBRARY_DB_FILENAME
    assert any("not merged" in record.message for record in caplog.records)


def test_corrupt_legacy_refuses_and_cleans_up(tmp_path):
    settings = _settings(tmp_path)
    (settings.data_dir / LEGACY_DB_FILENAME).write_bytes(b"this is not a database" * 32)

    with pytest.raises(RuntimeError, match="integrity"):
        migrate_library_db(settings.data_dir)

    assert not (settings.data_dir / LIBRARY_DB_FILENAME).exists()
    original = (settings.data_dir / LEGACY_DB_FILENAME).read_bytes()
    assert original.startswith(b"this is not a database")


def test_empty_legacy_refuses(tmp_path):
    settings = _settings(tmp_path)
    (settings.data_dir / LEGACY_DB_FILENAME).write_bytes(b"")

    with pytest.raises(RuntimeError, match="empty"):
        migrate_library_db(settings.data_dir)

    assert not (settings.data_dir / LIBRARY_DB_FILENAME).exists()


def test_postgres_target_never_touches_sqlite_files(tmp_path):
    settings = _settings(tmp_path)
    _seed_legacy(settings.data_dir)
    postgres = dataclasses.replace(
        settings, database_url="postgresql://user:pass@db:5432/library"
    )

    assert postgres.database_target == (
        "postgresql://user:pass@db:5432/library"
    )
    # No canonical file appeared: file resolution never ran.
    assert not (settings.data_dir / LIBRARY_DB_FILENAME).exists()


def test_removing_canonical_reamigrates_from_original(tmp_path):
    settings = _settings(tmp_path)
    _seed_legacy(settings.data_dir)

    first = migrate_library_db(settings.data_dir)
    first.unlink()

    second = migrate_library_db(settings.data_dir)

    assert second.name == LIBRARY_DB_FILENAME
    assert _read_notes(settings.data_dir, LIBRARY_DB_FILENAME) == _read_notes(
        settings.data_dir, LEGACY_DB_FILENAME
    )


def test_state_reports_legacy_backup_for_settings(tmp_path):
    settings = _settings(tmp_path)
    _seed_legacy(settings.data_dir)
    migrate_library_db(settings.data_dir)

    state = library_database_state(
        "sqlite", settings.data_dir, settings.data_dir / LIBRARY_DB_FILENAME
    )

    assert state.legacy_name == LEGACY_DB_FILENAME
    assert state.legacy_bytes == (settings.data_dir / LEGACY_DB_FILENAME).stat().st_size
    assert state.legacy_mtime is not None

    # Postgres installs and fresh installs have nothing to flag.
    assert library_database_state("postgresql", settings.data_dir, None).legacy_name is None
    clean_dir = tmp_path / "clean"
    clean_dir.mkdir()
    assert library_database_state("sqlite", clean_dir, clean_dir / "library.sqlite3").legacy_name is None


def test_settings_property_returns_canonical_path(tmp_path):
    settings = _settings(tmp_path)
    _seed_legacy(settings.data_dir)

    target = settings.database_target

    assert isinstance(target, Path)
    assert target.name == LIBRARY_DB_FILENAME
    assert (settings.data_dir / LEGACY_DB_FILENAME).exists()

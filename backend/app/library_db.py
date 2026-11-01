"""Resolution and one-time migration of the SQLite library database.

The canonical database file is ``library.sqlite3`` in the data directory.
Installs from before that name get their ``modelharbor.sqlite3`` copied to
the canonical filename on first use; the original file is left in place as
the user's backup. A copy that does not pass SQLite's integrity check is
deleted, the original is untouched, and startup fails loudly — running the
app against a half-migrated library is worse than refusing to begin.
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .library_files import LEGACY_DB_FILENAME, LIBRARY_DB_FILENAME

logger = logging.getLogger("uvicorn.error")

_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")


@dataclass(frozen=True)
class LibraryDatabaseState:
    engine: str  # "sqlite" | "postgresql"
    active_file: str | None  # canonical filename in use, when sqlite
    legacy_name: str | None  # legacy filename present next to the active file
    legacy_mtime: float | None
    legacy_bytes: int | None


def migrate_library_db(data_dir: Path) -> Path:
    """Return the canonical sqlite path for ``data_dir``, migrating a legacy
    library when needed. Calls no path helper from Settings: callers pass the
    resolved directory, and this module never creates it."""
    canonical = data_dir / LIBRARY_DB_FILENAME
    legacy = data_dir / LEGACY_DB_FILENAME

    if canonical.exists() or not legacy.exists():
        if canonical.exists() and legacy.exists():
            _warn_if_legacy_newer(canonical, legacy)
        return canonical

    if legacy.stat().st_size == 0:
        # SQLite reports a zero-length file as a valid empty database, so
        # the integrity check alone cannot refuse it. An empty legacy file
        # is never a library; refuse loudly without adopting anything.
        raise RuntimeError(
            f"Migration refused: {LEGACY_DB_FILENAME} is empty and cannot be "
            f"a library. Nothing was adopted; delete the file or restore it "
            f"from your backup before upgrading."
        )

    shutil.copy2(legacy, canonical)
    for suffix in _SIDECAR_SUFFIXES:
        sidecar = legacy.with_name(LEGACY_DB_FILENAME + suffix)
        if sidecar.exists():
            shutil.copy2(sidecar, canonical.with_name(LIBRARY_DB_FILENAME + suffix))

    if not _integrity_ok(canonical):
        for suffix in ("",) + _SIDECAR_SUFFIXES:
            candidate = canonical.with_name(LIBRARY_DB_FILENAME + suffix)
            candidate.unlink(missing_ok=True)
        raise RuntimeError(
            f"Migration refused: {LEGACY_DB_FILENAME} failed SQLite's integrity "
            f"check, so no copy was adopted. The original file is untouched; "
            f"restore it from your backup before upgrading."
        )

    for handle in (canonical, *[
        canonical.with_name(LIBRARY_DB_FILENAME + suffix)
        for suffix in _SIDECAR_SUFFIXES
        if canonical.with_name(LIBRARY_DB_FILENAME + suffix).exists()
    ]):
        _fsync(handle)

    logger.info(
        "Migrated library database: %s (%s) → %s; the original is kept as a "
        "backup and can be deleted whenever you like.",
        LEGACY_DB_FILENAME,
        _human_bytes(legacy.stat().st_size),
        LIBRARY_DB_FILENAME,
    )
    return canonical


def library_database_state(
    engine: str, data_dir: Path, active_path: Path | None
) -> LibraryDatabaseState:
    """Stateless view for the settings surface: which file is live and
    whether the legacy database still sits next to it (a leftover the user
    may delete). No persistence, no writes."""
    if engine != "sqlite" or active_path is None:
        return LibraryDatabaseState(engine, None, None, None, None)
    legacy = data_dir / LEGACY_DB_FILENAME
    if not legacy.exists():
        return LibraryDatabaseState(engine, active_path.name, None, None, None)
    stat = legacy.stat()
    if active_path.name == LEGACY_DB_FILENAME:
        # Pre-migration boot failed before the copy; nothing to flag yet.
        return LibraryDatabaseState(engine, active_path.name, None, None, None)
    return LibraryDatabaseState(
        engine, active_path.name, legacy.name, stat.st_mtime, stat.st_size
    )


def _warn_if_legacy_newer(canonical: Path, legacy: Path) -> None:
    try:
        if legacy.stat().st_mtime > canonical.stat().st_mtime:
            logger.warning(
                "%s was written after %s (a downgrade ran in between?). Its "
                "contents are not merged; the canonical database stays as it "
                "is, and %s remains as a backup.",
                LEGACY_DB_FILENAME,
                LIBRARY_DB_FILENAME,
                LEGACY_DB_FILENAME,
            )
    except OSError:
        pass


def _integrity_ok(path: Path) -> bool:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        result = connection.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.DatabaseError:
        # Not a database at all (corrupt, truncated, empty): refuse.
        return False
    finally:
        connection.close()
    return bool(result) and result[0] == "ok"


def _fsync(path: Path) -> None:
    handle = os.open(path, os.O_RDONLY)
    try:
        os.fsync(handle)
    finally:
        os.close(handle)


def _human_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} TiB"

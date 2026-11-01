"""Per-repository library manifest.

Every stored repository carries one manifest describing it: status, origin,
integrity fields, and (detailed in the download-planning work that follows)
the provenance audit trail.

The upstream HuggingHack manifest filename is accepted for reading only:
libraries and S3 buckets produced by the upstream project keep working, but
every new write uses the current name and removes a legacy sibling so a
directory holds exactly one manifest.

Writes are staged into a temporary file and atomically renamed, so readers
never observe a half-written manifest.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .library_files import LEGACY_MANIFEST_FILENAME, MANIFEST_FILENAME

MANIFEST_FILENAMES = (MANIFEST_FILENAME, LEGACY_MANIFEST_FILENAME)


def is_manifest_name(name: str) -> bool:
    """True for the manifest file itself or its staged-write variants."""
    return (
        name == MANIFEST_FILENAME
        or name == LEGACY_MANIFEST_FILENAME
        or name.startswith(f"{MANIFEST_FILENAME}.")
    )


def find_manifest(directory: Path) -> Path | None:
    """Return the path of the manifest file in this directory, if any."""
    path = directory / MANIFEST_FILENAME
    if path.is_file():
        return path
    legacy = directory / LEGACY_MANIFEST_FILENAME
    if legacy.is_file():
        return legacy
    return None


def read_manifest(directory: Path) -> dict[str, Any]:
    """Return the directory's manifest as a dict, or {} when unreadable."""
    for filename in MANIFEST_FILENAMES:
        path = directory / filename
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            return payload
    return {}


def write_manifest(
    directory: Path,
    payload: dict[str, Any],
    *,
    schema_version: int = 2,
) -> Path:
    """Atomically write the current manifest and drop any legacy sibling."""
    document = dict(payload)
    document.setdefault("schema_version", schema_version)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / MANIFEST_FILENAME
    descriptor, staged = tempfile.mkstemp(
        dir=directory,
        prefix=f"{MANIFEST_FILENAME}.",
        suffix=".staged",
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(document, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(staged, path)
    except BaseException:
        try:
            os.unlink(staged)
        except OSError:
            pass
        raise
    legacy = directory / LEGACY_MANIFEST_FILENAME
    try:
        legacy.unlink()
    except OSError:
        pass
    return path

"""Safe deletion for tracked library copies (plan -> apply).

Deletion is the third librarian operation beside acquire and update,
and it follows the same discipline: every destructive step requires an
explicitly approved request, and nothing is ever removed silently.

``build_deletion_proposal`` describes *only*: the tracked files the copy
records (candidates for narrowing the selection), untracked leftovers
found on disk (partial files from cancelled jobs, the Hub's resume
cache, stray artifacts), and the whole-copy summary. It deletes nothing.

``delete_selected_files`` applies an approved selection: it removes
those paths from disk, narrows the stored manifest selection so the
record keeps describing reality, and stamps a ``pruned`` audit entry
with what was freed. Records that remain keep their content ids, so
upstream diffs stay meaningful afterwards.

``delete_whole_copy`` removes the entire repository tree and its
library record; the caller must pass an exact-name confirmation, and
S3-backed copies also drop their durable remote objects. File-level
deletion is refused for S3-backed copies because their local files are
a cache of a durable remote copy: shrinking that cache file-by-file
would silently desynchronize the two.
"""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .indexer import directory_stats
from .manifests import (
    LEGACY_MANIFEST_FILENAME,
    MANIFEST_FILENAME,
    is_manifest_name,
    read_manifest,
    write_manifest,
)

DELETION_PROPOSAL_VERSION = 1

#: Directories collapsed into one proposal entry each (cumulative size).
_CACHE_DIRECTORY = ".cache"


class DeletionError(ValueError):
    """Raised when a copy cannot be deleted as requested."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extras(root: Path) -> list[dict[str, Any]]:
    """Untracked-on-disk entries: partial files, cache dirs, strays.

    The manifest file itself (and staged variants) are never proposed.
    Anything under ``.cache`` is collapsed into a single entry because it
    is the Hub's resume metadata, not library content.
    """
    entries: list[dict[str, Any]] = []
    cache_size = 0
    for path in root.rglob("*"):
        if path.is_dir():
            continue
        relative = path.relative_to(root).as_posix()
        if relative.split("/", 1)[0] == _CACHE_DIRECTORY:
            cache_size += path.stat().st_size
            continue
        name = path.name
        if len(relative.split("/")) == 1 and is_manifest_name(name):
            continue
        if name == ".write-test":
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        entries.append({"path": relative, "size": size, "kind": "file"})
    if cache_size:
        entries.append({"path": _CACHE_DIRECTORY, "size": cache_size, "kind": "directory"})
    entries.sort(key=lambda item: (-item["size"], item["path"]))
    return entries


def build_deletion_proposal(repo_id: str, root: Path) -> dict[str, Any]:
    """Build the versioned, JSON-serializable deletion proposal artifact.

    ``root`` is the copy's directory; it may not exist (rescanned-away
    copies then propose only whole-copy removal).
    """
    manifest = read_manifest(root) if root.is_dir() else {}
    tracked = [
        {
            "path": entry.get("path"),
            "size": int(entry.get("size") or 0),
            "blob": entry.get("blob"),
        }
        for entry in (manifest.get("selection") or {}).get("files") or []
        if entry.get("path")
    ]
    extras = _extras(root) if root.is_dir() else []
    whole = directory_stats(root) if root.is_dir() else (0, 0, 0.0)
    deletion, file_count, _ = whole
    proposal = {
        "schema_version": DELETION_PROPOSAL_VERSION,
        "repo_id": repo_id,
        "tracked": tracked,
        "extras": extras,
        "whole_copy": {
            "size_bytes": deletion,
            "file_count": file_count,
            "tracked_manifest": bool(manifest),
        },
        "built_at": _now_iso(),
    }
    return proposal


def _validate_requested_paths(root: Path, requested: list[str]) -> list[str]:
    """Clean a deletion request into safe, root-relative, unique paths."""
    if not requested:
        raise DeletionError("No files were selected for deletion.")
    cleaned: list[str] = []
    for raw in requested:
        value = str(raw).strip()
        if not value:
            continue
        candidate = Path(value)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise DeletionError(f"Deletion paths must stay inside this model folder: {value}")
        resolved = (root / candidate).resolve()
        try:
            resolved.relative_to(root.resolve())
        except ValueError as error:
            raise DeletionError(
                f"Deletion paths must stay inside this model folder: {value}"
            ) from error
        if candidate.as_posix() in (MANIFEST_FILENAME, LEGACY_MANIFEST_FILENAME):
            raise DeletionError("The manifest file is this copy's identity; delete the whole copy instead.")
        if root.is_dir() and not (root / candidate).exists():
            raise DeletionError(f"'{value}' does not exist on disk.")
        if candidate.as_posix() not in cleaned:
            cleaned.append(candidate.as_posix())
    if not cleaned:
        raise DeletionError("No files were selected for deletion.")
    return sorted(cleaned)


def delete_selected_files(
    repo_id: str,
    root: Path,
    requested: list[str],
    *,
    storage_backend: str,
) -> dict[str, Any]:
    """Apply an approved file deletion and narrow the stored selection.

    Returns a summary with the deleted paths, freed bytes, and the
    narrowed manifest document (also written to disk).
    """
    if storage_backend != "filesystem":
        raise DeletionError(
            "Individual files of this model are a local cache of a durable S3 copy. "
            "Delete the whole copy or remove the local cache instead."
        )
    paths = _validate_requested_paths(root, requested)
    manifest = read_manifest(root)
    files_before = len((manifest.get("selection") or {}).get("files") or [])
    if not files_before:
        raise DeletionError(
            "This copy has no tracked selection to narrow; delete the whole copy instead."
        )

    freed = 0
    deleted: list[str] = []
    for relative in paths:
        target = root / relative
        if target.is_dir():
            freed += sum(item.stat().st_size for item in target.rglob("*") if item.is_file())
            shutil.rmtree(target)
        else:
            freed += target.stat().st_size
            target.unlink()
        deleted.append(relative)

    deleted_set = set(deleted)
    selection = dict(manifest.get("selection") or {})
    remaining = [
        entry for entry in selection.get("files") or [] if entry.get("path") not in deleted_set
    ]
    selection["files"] = remaining
    selection["file_count"] = len(remaining)
    selection["selected_bytes"] = sum(int(entry.get("size") or 0) for entry in remaining)
    disk_size, disk_count, _ = directory_stats(root)
    stamps = list(manifest.get("pruned") or [])
    stamps.append(
        {
            "paths": deleted,
            "freed_bytes": freed,
            "files_before": files_before,
            "files_after": len(remaining),
            "pruned_at": _now_iso(),
        }
    )
    document = {
        **manifest,
        "selection": selection,
        "total_bytes": disk_size,
        "file_count": disk_count,
        "pruned": stamps,
    }
    write_manifest(root, document)
    return {"deleted": deleted, "freed_bytes": freed, "manifest": document}


def delete_whole_copy(
    repo_id: str,
    root: Path,
    confirmation: str,
    *,
    model_storage: Any,
    storage_root: Path,
) -> None:
    """Remove the entire copy, its durable S3 objects if any, and the record.

    Requires the repository id as confirmation so automation cannot
    destroy a copy with a bare request. The database record removal is
    the caller's responsibility (endpoint layer) so both backends stay
    symmetric there.
    """
    if str(confirmation).strip() != repo_id:
        raise DeletionError("Repository name confirmation does not match.")
    if root.resolve() == storage_root.resolve():
        raise DeletionError("Refusing to delete the storage root itself.")
    model_storage.delete_repository(repo_id)
    if root.exists():
        shutil.rmtree(root)

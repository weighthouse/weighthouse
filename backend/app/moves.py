"""Moving library copies between declared storage locations (plan -> apply).

The third storage pillar of D3 ("the library lives in N places").
Everything here rides on the location registry: both the source and the
destination must be declared locations, and the copy's manifest is the
portable record that travels with it.

The proposal artifact (``build_move_plan``) describes only: what would
move, how big it is, whether the destination has room, and whether the
move is possible at all. ``apply_move`` performs the physical
transfer — copy into a staging directory on the destination, verify
byte counts, swap into place, refresh the manifest with a ``moved``
audit stamp, and remove the source last. From the copy's point of view
it is never a half-state: either the whole tree exists complete at the
destination or it never left the source.

Copies backed by durable S3 object storage are not movable here: their
"durability" *is* S3 and the local tree is a cache; the existing
restore/evict flow already covers the meaningful relocations. Filesystem
locations only, pending the S3 tier work being field-tested.
"""

from __future__ import annotations

import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .indexer import directory_stats
from .locations import StorageLocation
from .manifests import read_manifest, write_manifest

MOVE_PLAN_VERSION = 1


class MoveError(ValueError):
    """Raised when a move cannot be planned or applied as requested."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_move_plan(
    repo_id: str,
    source: StorageLocation,
    destination: StorageLocation,
    relative_path: str,
    *,
    destination_free_bytes: int | None = None,
) -> dict[str, Any]:
    """Describe moving one copy between two declared locations."""
    if source.name == destination.name:
        raise MoveError("The source and destination location are the same.")
    manifest = read_manifest(source.root / relative_path)
    if not manifest:
        raise MoveError(
            "This copy has no manifest, so it cannot be moved as a tracked library copy."
        )
    if manifest.get("status") != "complete":
        raise MoveError("Only complete copies can be moved.")
    source_stats = directory_stats(source.root / relative_path)
    size_bytes, file_count = source_stats[0], source_stats[1]
    if size_bytes <= 0:
        raise MoveError("Nothing to move: the copy has no content on disk.")
    same_filesystem = source.root.stat().st_dev == destination.root.stat().st_dev
    if destination_free_bytes is not None and destination_free_bytes < size_bytes:
        raise MoveError(
            f"Not enough free space at '{destination.name}': "
            f"needs {size_bytes} bytes, has {destination_free_bytes}."
        )
    if (destination.root / relative_path).exists():
        raise MoveError(
            f"'{destination.name}' already contains a copy or partial tree at this path."
        )
    return {
        "schema_version": MOVE_PLAN_VERSION,
        "repo_id": repo_id,
        "from": {"name": source.name, "root": str(source.root)},
        "to": {"name": destination.name, "root": str(destination.root)},
        "relative_path": relative_path,
        "size_bytes": size_bytes,
        "file_count": file_count,
        "same_filesystem": same_filesystem,
        "strategy": "rename" if same_filesystem else "copy-verify-swap",
        "built_at": _now_iso(),
    }


def apply_move(
    plan: dict[str, Any],
    source: StorageLocation,
    destination: StorageLocation,
    *,
    indexer: Any,
) -> dict[str, Any]:
    """Physically move the copy and refresh its record.

    Copy-verify-swap: content is staged under a temporary directory at
    the destination, byte counts are verified against the plan, then the
    tree is swapped into place and the source is removed. On any
    failure, the staged tree is removed and the source stands untouched.
    The manifest travels and gains a ``moved`` audit stamp.
    """
    if plan.get("schema_version") != MOVE_PLAN_VERSION:
        raise MoveError("Unsupported move plan schema_version.")
    from_path = source.root / plan["relative_path"]
    destination_path = destination.root / plan["relative_path"]
    if destination_path.exists():
        raise MoveError(
            f"'{destination.name}' already contains a copy or partial tree at this path."
        )
    if plan.get("strategy") == "copy-verify-swap":
        destination.root.mkdir(parents=True, exist_ok=True)
        staging = Path(
            tempfile.mkdtemp(
                dir=destination.root,
                prefix=f".{destination_path.name}.moving-",
            )
        )
        staged_copy = staging / plan["relative_path"]
        try:
            shutil.copytree(from_path, staged_copy)
            moved_size, moved_count, _ = directory_stats(staged_copy)
            if moved_size != int(plan["size_bytes"]) or moved_count != int(plan["file_count"]):
                raise MoveError(
                    "Verification failed while copying: the staged content does not "
                    "match the plan, so nothing was changed."
                )
            destination_path.parent.mkdir(parents=True, exist_ok=True)
            staged_copy.rename(destination_path)
            _finalize(plan, destination_path, destination.name, indexer)
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
    else:
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(from_path), str(destination_path))
        _finalize(plan, destination_path, destination.name, indexer)
    _remove_source(from_path)
    return {"moved_to": destination.name, "path": str(destination_path)}


def _finalize(
    plan: dict[str, Any],
    final_path: Path,
    destination_name: str,
    indexer: Any,
) -> None:
    """Stamp the traveled manifest, then re-index through the destination.

    The indexer re-stats the copy and upserts its record with the
    manifest's location, which is how the stored record learns its new
    home; no separate record surgery is needed.
    """
    manifest = read_manifest(final_path)
    if manifest:
        stamps = list(manifest.get("moved") or [])
        stamps.append(
            {
                "from": plan["from"]["name"],
                "to": destination_name,
                "moved_at": _now_iso(),
            }
        )
        manifest["moved"] = stamps
        manifest["location"] = destination_name
        write_manifest(final_path, manifest)
    if indexer is not None:
        indexer.index_path(final_path)


def _remove_source(from_path: Path) -> None:
    """Remove the source tree last, after the destination stands complete."""
    if from_path.exists():
        shutil.rmtree(from_path)

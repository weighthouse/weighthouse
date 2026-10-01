"""Verifying a stored copy against its manifest.

The librarian's periodic question — "is what's on disk still what the
manifest describes?" — answered as a versioned artifact, symmetric with
the update diff and the deletion proposal: it *reports* and never
mutates.

Verification here is honest about what it can prove without the
upstream: the manifest records upstream content ids (git blob SHAs) and
byte sizes, so a local walk attests *presence* and *size consistency*
per tracked file, lists untracked extras on disk, and flags when the
record itself no longer describes reality. Content equality is proven
through the tracked-update flow instead: a size change pairs with an
upstream blob change, and the update diff decides what to re-fetch.

Repair paths, in order of preference: missing or size-drifted tracked
files that are unchanged upstream re-fetch through the existing
"needs re-fetch"/"changed" rows of the updates flow; a manifest that
lost its selection entirely (pre-tracking copy) is re-download
territory. Nothing here deletes or rewrites anything.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .indexer import directory_stats
from .manifests import is_manifest_name, read_manifest

VERIFY_ARTIFACT_VERSION = 1

STATUS_PRESENT = "present"
STATUS_MISSING = "missing"
STATUS_SIZE_DIFFERS = "size-differs"

_CACHE_DIRECTORY = ".cache"


class VerifyError(ValueError):
    """Raised when a copy cannot be verified as requested."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def verify_copy(root: Path) -> dict[str, Any]:
    """Walk ``root`` against its manifest and return the drift artifact."""
    manifest = read_manifest(root)
    if not manifest:
        raise VerifyError(
            "This copy has no manifest, so it is not a tracked library copy; "
            "there is nothing to verify it against."
        )
    if manifest.get("status") != "complete":
        raise VerifyError("Only complete copies can be verified.")
    files = (manifest.get("selection") or {}).get("files") or []
    if not files:
        raise VerifyError(
            "This copy has no tracked selection to verify "
            "(pre-tracking copies verify by re-downloading)."
        )

    rows: list[dict[str, Any]] = []
    summary = {STATUS_PRESENT: 0, STATUS_MISSING: 0, STATUS_SIZE_DIFFERS: 0}
    for entry in files:
        path = str(entry.get("path") or "")
        if not path:
            continue
        expected = int(entry.get("size") or 0)
        on_disk = root / path
        if not on_disk.is_file():
            status = STATUS_MISSING
            actual = 0
        else:
            actual = on_disk.stat().st_size
            status = STATUS_PRESENT if actual == expected else STATUS_SIZE_DIFFERS
        summary[status] += 1
        rows.append(
            {
                "path": path,
                "status": status,
                "expected_size": expected,
                "actual_size": actual,
                "blob": entry.get("blob"),
            }
        )

    tracked_paths = {row["path"] for row in rows}
    extras: list[dict[str, Any]] = []
    for item in root.rglob("*"):
        if item.is_dir():
            continue
        relative = item.relative_to(root).as_posix()
        if relative.split("/", 1)[0] == _CACHE_DIRECTORY:
            continue
        if is_manifest_name(item.name):
            continue
        if relative in tracked_paths:
            continue
        extras.append({"path": relative, "size": item.stat().st_size})

    rows.sort(
        key=lambda row: (
            {STATUS_MISSING: 0, STATUS_SIZE_DIFFERS: 1, STATUS_PRESENT: 2}[row["status"]],
            row["path"],
        )
    )
    extras.sort(key=lambda item: (-item["size"], item["path"]))
    disk_size, disk_count, _ = directory_stats(root)
    drift = bool(summary[STATUS_MISSING] or summary[STATUS_SIZE_DIFFERS])
    return {
        "schema_version": VERIFY_ARTIFACT_VERSION,
        "verified_at": _now_iso(),
        "location": manifest.get("location"),
        "summary": summary,
        "drifted": drift,
        "extras_beyond_manifest": extras,
        "disk": {"size_bytes": disk_size, "file_count": disk_count},
        "files": rows,
        "repair": (
            "Missing and size-differed files re-fetch through the updates flow "
            "(they appear there as changed or needs-re-fetch rows)."
            if drift
            else None
        ),
    }

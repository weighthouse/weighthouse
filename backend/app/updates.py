"""Tracked updates: per-file upstream diffs for library copies.

A tracked copy is one whose manifest records, per selected file, the
upstream content id (the ``blob`` key written by the download planner).
Comparing those ids against ``model_details`` for the same repository
answers, per file: unchanged, changed, added, removed, or
downstream-missing (recorded in the manifest but absent on disk).

This module holds three pieces:

``build_update_diff`` produces a versioned, JSON-serializable diff
artifact. It only *describes*; it never touches storage and never
deletes anything. Upstream removals are reported, not applied.

``apply_paths``/``exact_patterns`` validate an apply request against the
artifact and translate the approved paths into concrete download
patterns (approval happens in the API layer; the queue freezes the plan
against the diff's pinned SHA).

``merge_update_manifest`` folds a completed update plan back into the
copy's existing manifest, so the manifest keeps describing the whole
library copy (not just the last update) while the update itself stays
visible in an ``update`` audit stamp.

Selection-semantics fallbacks are kept conservative and explicit:
when an older manifest predates pattern recording, "added" contains
only metadata files (safe, universal additions); weight-file additions
would risk surprising multi-gigabyte downloads, so they are only
reported once the manifest records the original selection patterns.
"""

from __future__ import annotations

import glob
from datetime import datetime, timezone
from fnmatch import fnmatch
from typing import Any

from .planning import Selection, is_metadata_file, selection_covers

UPDATE_DIFF_VERSION = 1

STATUS_CHANGED = "changed"
STATUS_ADDED = "added"
STATUS_MISSING = "missing"
STATUS_REMOVED = "removed"
STATUS_UNCHANGED = "unchanged"

APPLYABLE_STATUSES = (STATUS_CHANGED, STATUS_ADDED, STATUS_MISSING)

#: Status display order for the artifact's file list.
_STATUS_RANK = {
    STATUS_CHANGED: 0,
    STATUS_ADDED: 1,
    STATUS_MISSING: 2,
    STATUS_REMOVED: 3,
    STATUS_UNCHANGED: 4,
}


class UpdateDiffError(ValueError):
    """Raised when a copy cannot be diffed (no manifest, or pre-tracking)."""


class UpdateApplyError(ValueError):
    """Raised when an apply request references files that are not updatable."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _selection_patterns(manifest: dict[str, Any]) -> Selection | None:
    """Rebuild the original selection semantics from manifest record.

    Returns None when the manifest predates pattern recording; callers
    then fall back to conservative metadata-only "added" reporting.
    """
    selection = manifest.get("selection")
    if not isinstance(selection, dict):
        return None
    patterns = selection.get("patterns")
    if not isinstance(patterns, dict):
        return None
    allow = [str(item) for item in patterns.get("allow") or []]
    ignore = [str(item) for item in patterns.get("ignore") or []]
    if not allow:
        # A full-recorded download covers everything; that is still
        # meaningful information.
        if str(patterns.get("mode") or "") == "full":
            return Selection(
                mode="full",
                allow_patterns=[],
                ignore_patterns=ignore,
            )
        return None
    mode = str(patterns.get("mode") or "custom")
    gguf_path = None
    if mode == "gguf":
        # resolve_selection puts the gguf path first when it is set.
        gguf_path = allow[0]
        return Selection(
            mode=mode,
            allow_patterns=allow,
            ignore_patterns=ignore,
            gguf_path=gguf_path,
        )
    return Selection(mode=mode, allow_patterns=allow, ignore_patterns=ignore)


def _covered_by_selection(selection: Selection | None, path: str) -> bool:
    if selection is None:
        # Conservative fallback: only metadata additions are reported.
        return is_metadata_file(path)
    return selection_covers(selection, path)


def build_update_diff(
    manifest: dict[str, Any],
    upstream: dict[str, Any],
    *,
    root: Any = None,
    requested_revision: str = "main",
) -> dict[str, Any]:
    """Build the versioned per-file update diff artifact.

    ``manifest`` is a read manifest document (may be empty when the
    directory carries none). ``upstream`` is the ``model_details``
    payload: ``sha`` plus ``files`` entries with ``path``, ``size``, and
    ``blob_id``. ``root`` (optional path) enables the downstream-missing
    check by stat-ing each recorded file.
    """
    if not manifest:
        raise UpdateDiffError(
            "This library copy has no manifest, so there is nothing to diff."
        )
    if manifest.get("status") not in ("complete", None):
        raise UpdateDiffError(
            "This library copy is not in a complete state; rescan or restore it first."
        )
    if (manifest.get("schema_version") or 1) < 2 or "selection" not in manifest:
        raise UpdateDiffError(
            "This library copy predates tracked manifests and cannot be diffed. "
            "Re-download it to adopt tracking."
        )

    local_files: dict[str, dict[str, Any]] = {
        str(entry.get("path") or ""): entry
        for entry in (manifest.get("selection") or {}).get("files") or []
        if entry.get("path")
    }
    upstream_files: dict[str, dict[str, Any]] = {
        str(item.get("path") or ""): item
        for item in upstream.get("files") or []
        if item.get("path")
    }

    def disk_has(path: str) -> bool:
        if root is None:
            return True
        return (root / path).is_file()

    selection = _selection_patterns(manifest)
    added_limited = False

    rows: list[dict[str, Any]] = []
    summary = {
        STATUS_CHANGED: 0,
        STATUS_ADDED: 0,
        STATUS_MISSING: 0,
        STATUS_REMOVED: 0,
        STATUS_UNCHANGED: 0,
    }
    transfer_bytes = 0

    for path, item in upstream_files.items():
        local_entry = local_files.get(path)
        upstream_blob = str(item.get("blob_id") or "") or None
        upstream_size = int(item.get("size") or 0)
        if local_entry is None:
            if not _covered_by_selection(selection, path):
                continue
            status = STATUS_ADDED
            local_blob = None
            local_size = 0
            delta = upstream_size
        else:
            local_blob = str(local_entry.get("blob") or "") or None
            local_size = int(local_entry.get("size") or 0)
            if local_blob is None or upstream_blob is None:
                # Either side lacked a content id: equality cannot be
                # proven, so treat the file as changed (worst case, the
                # update re-fetches identical content).
                status = STATUS_CHANGED
            elif local_blob == upstream_blob:
                status = STATUS_UNCHANGED if disk_has(path) else STATUS_MISSING
            else:
                status = STATUS_CHANGED
            delta = upstream_size - local_size
        if status in APPLYABLE_STATUSES:
            transfer_bytes += upstream_size
        summary[status] += 1
        rows.append(
            {
                "path": path,
                "status": status,
                "local": {"size": local_size, "blob": local_blob},
                "upstream": {"size": upstream_size, "blob": upstream_blob},
                "size_delta": delta,
            }
        )

    for path, local_entry in local_files.items():
        if path in upstream_files:
            continue
        summary[STATUS_REMOVED] += 1
        rows.append(
            {
                "path": path,
                "status": STATUS_REMOVED,
                "local": {
                    "size": int(local_entry.get("size") or 0),
                    "blob": str(local_entry.get("blob") or "") or None,
                },
                "upstream": {"size": 0, "blob": None},
                "size_delta": 0,
            }
        )

    rows.sort(key=lambda row: (_STATUS_RANK[row["status"]], row["path"]))
    if selection is None and summary[STATUS_ADDED]:
        added_limited = True

    return {
        "schema_version": UPDATE_DIFF_VERSION,
        "repo_id": manifest.get("repo_id"),
        "requested_revision": requested_revision,
        "local": {
            "revision": manifest.get("revision"),
            "sha": manifest.get("sha"),
            "status": manifest.get("status"),
        },
        "upstream": {
            "sha": str(upstream.get("sha") or ""),
            "fetched_at": _now_iso(),
        },
        "updates_available": bool(
            summary[STATUS_CHANGED] or summary[STATUS_ADDED] or summary[STATUS_MISSING]
        ),
        "results_in": {
            "transfer_bytes": transfer_bytes,
            "changed": summary[STATUS_CHANGED],
            "added": summary[STATUS_ADDED],
            "missing": summary[STATUS_MISSING],
        },
        "summary": summary,
        "added_reporting_limited": added_limited,
        "files": rows,
    }


def applyable_paths(diff: dict[str, Any]) -> list[str]:
    """Paths in ``diff`` that an apply request may target, sorted."""
    return sorted(
        row["path"]
        for row in diff.get("files") or []
        if row.get("status") in APPLYABLE_STATUSES
    )


def apply_paths(diff: dict[str, Any], requested: list[str] | None) -> list[str]:
    """Validate an apply request and return the effective path set.

    An empty request means "all applyable files" (the "Update all"
    semantic). Requesting a non-applyable path is an error, not a silent
    no-op.
    """
    available = applyable_paths(diff)
    if not requested or (len(requested) == 1 and not str(requested[0]).strip()):
        return available
    cleaned: list[str] = []
    for raw in requested:
        path = str(raw).strip()
        if not path:
            continue
        if path not in available:
            raise UpdateApplyError(f"'{path}' is not an updatable file in this diff.")
        if path not in cleaned:
            cleaned.append(path)
    if not cleaned:
        raise UpdateApplyError("The apply request named no updatable files.")
    return sorted(cleaned)


def exact_patterns(paths: list[str]) -> list[str]:
    """Download patterns that match exactly these repository paths.

    Paths are glob-escaped so literal ``[...]`` names survive pattern
    matching; an empty list stays empty.
    """
    return [glob.escape(str(path)) for path in paths if str(path).strip()]


def merge_update_manifest(
    existing: dict[str, Any],
    plan_payload: dict[str, Any],
    *,
    writer_version: str,
    written_at: str,
    disk_file_count: int,
    disk_total_bytes: int,
) -> dict[str, Any]:
    """Fold a completed update plan into the copy's existing manifest.

    The result describes the whole library copy again: existing per-file
    records are kept (their blob ids still prove content identity) and
    updated/added paths are replaced by the update plan's records. The
    update itself is visible in the ``update`` audit stamp and the
    selection summary's transfer bytes.
    """
    selection = existing.get("selection")
    if not isinstance(selection, dict) or not selection.get("files"):
        raise UpdateDiffError(
            "Cannot merge an update into a manifest without a tracked selection."
        )
    plan_selection = plan_payload.get("selection") or {}
    plan_files = {str(item.get("path") or ""): dict(item) for item in plan_selection.get("files") or [] if item.get("path")}
    if not plan_files:
        raise UpdateDiffError("The update plan selected no files.")

    merged: dict[str, dict[str, Any]] = {
        str(entry.get("path") or ""): dict(entry)
        for entry in selection.get("files") or []
        if entry.get("path")
    }
    merged.update(plan_files)
    files = sorted(merged.values(), key=lambda item: (-int(item.get("size") or 0), str(item.get("path") or "")))
    selected_bytes = sum(int(item.get("size") or 0) for item in files)
    transfer_bytes = int(plan_selection.get("transfer_bytes") or 0)
    document = dict(existing)
    document.update(
        {
            "status": "complete",
            "revision": plan_payload.get("requested_revision") or existing.get("revision"),
            "sha": plan_payload.get("resolved_sha") or existing.get("sha"),
            "downloaded_at": written_at,
            "total_bytes": disk_total_bytes,
            "file_count": disk_file_count,
            "requested_revision": plan_payload.get("requested_revision") or existing.get("requested_revision"),
            "selection": {
                "files": files,
                "file_count": len(files),
                "selected_bytes": selected_bytes,
                "transfer_bytes": transfer_bytes,
                "cached_bytes": max(0, selected_bytes - transfer_bytes),
                **(
                    {"patterns": dict(existing["selection"]["patterns"])}
                    if isinstance(existing.get("selection"), dict)
                    and isinstance(existing["selection"].get("patterns"), dict)
                    else {}
                ),
            },
            "update": {
                "applied_at": written_at,
                "from_sha": existing.get("sha"),
                "to_sha": plan_payload.get("resolved_sha"),
                "mode": "tracked-update",
                "app_version": writer_version,
                "transfer_bytes": transfer_bytes,
            },
        }
    )
    return document

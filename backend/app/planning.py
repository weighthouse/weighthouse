"""Download planning.

``resolve_selection`` is the single implementation of the selection
semantics (the frontend sends mode plus parameters and receives the
derived patterns). ``plan_download`` freezes the exact file list,
sizes, and selected-vs-transfer split against a resolved commit SHA at
queue time; the worker executes the pinned plan and never re-resolves.
Upstream may move the branch between queue time and worker start — the
plan does not.

Plans are JSON-serializable dicts with ``schema_version`` and are
covered by versioned fixtures in the test suite.
"""

from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

DOWNLOAD_MODES = ("full", "safetensors", "gguf", "metadata", "custom")
PLANNER_VERSION = 1

#: Metadata globs sent to ``snapshot_download``. Kept byte-identical with
#: the upstream selection logic these replace.
METADATA_PATTERNS = [
    "*.json",
    "*.md",
    "*.txt",
    "*.yaml",
    "*.yml",
    "*.jinja",
    "*.model",
    "*.tiktoken",
    "LICENSE*",
    "tokenizer*",
]

UNSAFE_PATTERNS = ["*.bin", "*.pt", "*.pth", "*.pkl", "*.pickle", "*.ckpt"]

#: Suffixes the selection UI treats as metadata, for parity checks.
_METADATA_SUFFIXES = (
    ".json",
    ".md",
    ".txt",
    ".yaml",
    ".yml",
    ".jinja",
    ".model",
    ".tiktoken",
)


class SelectionError(ValueError):
    """Raised when a download request's mode parameters are inconsistent."""


class PlanError(ValueError):
    """Raised when a download plan cannot be produced."""


@dataclass(frozen=True)
class Selection:
    """Resolved download mode: validated params plus concrete patterns."""

    mode: str
    allow_patterns: list[str] = field(default_factory=list)
    ignore_patterns: list[str] = field(default_factory=list)
    gguf_path: str | None = None
    exclude_unsafe: bool = False

    def to_payload(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "allow_patterns": list(self.allow_patterns),
            "ignore_patterns": list(self.ignore_patterns),
            "gguf_path": self.gguf_path,
            "exclude_unsafe": self.exclude_unsafe,
        }


def _extra_params_check(mode: str, **values: Any) -> SelectionError | None:
    for name, value in values.items():
        if value not in (None, [], False, ""):
            return SelectionError(
                f"{name} is only valid for the modes that use it, not '{mode}'."
            )
    return None


def parse_pattern_list(values: list[str] | None, *, field_name: str) -> list[str]:
    """Split comma-joined pattern strings and drop blanks; cap list length."""
    cleaned: list[str] = []
    for raw in values or []:
        for chunk in str(raw).split(","):
            value = chunk.strip()
            if value and value not in cleaned:
                cleaned.append(value)
    if len(cleaned) > 50:
        raise SelectionError(f"{field_name} contains more than 50 patterns.")
    return cleaned


def resolve_selection(
    mode: str,
    *,
    gguf_path: str | None = None,
    custom_include: list[str] | None = None,
    custom_exclude: list[str] | None = None,
    exclude_unsafe: bool = False,
) -> Selection:
    """Derive concrete patterns from a download mode and its parameters."""
    if mode not in DOWNLOAD_MODES:
        raise SelectionError(f"Unknown download mode '{mode}'.")
    gguf_path = (gguf_path or "").strip() or None

    if mode == "full":
        error = _extra_params_check(mode, gguf_path=gguf_path, custom_include=custom_include, custom_exclude=custom_exclude)
        if error:
            raise error
        allow: list[str] = []
    elif mode == "safetensors":
        error = _extra_params_check(mode, gguf_path=gguf_path, custom_include=custom_include, custom_exclude=custom_exclude)
        if error:
            raise error
        allow = ["*.safetensors", *METADATA_PATTERNS]
    elif mode == "gguf":
        error = _extra_params_check(mode, custom_include=custom_include, custom_exclude=custom_exclude)
        if error:
            raise error
        if not gguf_path:
            raise SelectionError("A GGUF file must be selected for the 'gguf' download mode.")
        allow = [gguf_path, *METADATA_PATTERNS]
    elif mode == "metadata":
        error = _extra_params_check(mode, gguf_path=gguf_path, custom_include=custom_include, custom_exclude=custom_exclude)
        if error:
            raise error
        allow = list(METADATA_PATTERNS)
    else:  # custom
        error = _extra_params_check(mode, gguf_path=gguf_path)
        if error:
            raise error
        allow = parse_pattern_list(custom_include, field_name=" Included patterns")
        if not allow:
            raise SelectionError("Custom downloads need at least one included pattern.")

    ignore = list(UNSAFE_PATTERNS) if exclude_unsafe else []
    if mode == "custom":
        ignore.extend(parse_pattern_list(custom_exclude, field_name="Excluded patterns"))
    return Selection(
        mode=mode,
        allow_patterns=allow,
        ignore_patterns=ignore,
        gguf_path=gguf_path if mode == "gguf" else None,
        exclude_unsafe=bool(exclude_unsafe),
    )


def is_metadata_file(path: str) -> bool:
    """Suffix/basename parity check for metadata files (display + tests)."""
    name = path.lower().rsplit("/", 1)[-1]
    return name.endswith(_METADATA_SUFFIXES) or name.startswith("license") or name.startswith("tokenizer")


@dataclass(frozen=True)
class DownloadPlan:
    """A frozen plan produced at queue time and executed by the worker."""

    requested_revision: str
    sha: str
    mode: str
    allow_patterns: list[str]
    ignore_patterns: list[str]
    files: list[dict[str, Any]]
    selected_bytes: int
    transfer_bytes: int
    cached_bytes: int
    resolved_at: str
    planned_at: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": PLANNER_VERSION,
            "requested_revision": self.requested_revision,
            "resolved_sha": self.sha,
            "resolved_at": self.resolved_at,
            "mode": self.mode,
            "allow_patterns": list(self.allow_patterns),
            "ignore_patterns": list(self.ignore_patterns),
            "selection": {
                "files": [dict(item) for item in self.files],
                "file_count": len(self.files),
                "selected_bytes": self.selected_bytes,
                "transfer_bytes": self.transfer_bytes,
                "cached_bytes": self.cached_bytes,
            },
            "planned_at": self.planned_at,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_payload())


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def plan_download(
    *,
    requested_revision: str,
    resolved_sha: str,
    selection: Selection,
    dry_run_files: list[Any],
    blob_by_path: dict[str, str] | None = None,
    planned_at: str | None = None,
    resolved_at: str = "",
) -> DownloadPlan:
    """Freeze a dry-run result into a plan.

    ``dry_run_files`` is the ``DryRunFileInfo`` list from
    ``snapshot_download(..., dry_run=True)``; attribute access is duck-typed
    so tests can pass plain stand-ins.

    ``blob_by_path`` maps repository paths to upstream content ids (git blob
    SHAs, LFS-aware callers may pass LFS ids; whatever id the caller uses,
    it must stay the same id family for later update diffs). Recorded per
    file so a future update can tell "unchanged" from "changed" per file;
    absent entries simply omit ``blob`` and stay old-compatible.
    """
    blobs = blob_by_path or {}
    files: list[dict[str, Any]] = []
    selected = transfer = cached = 0
    for item in dry_run_files:
        size = int(getattr(item, "file_size", 0) or 0)
        is_cached = bool(getattr(item, "is_cached", False))
        will_download = bool(getattr(item, "will_download", not is_cached))
        commit = str(getattr(item, "commit_hash", resolved_sha) or resolved_sha)
        if commit and commit != resolved_sha:
            raise PlanError("Dry run returned files from a different commit than the resolved SHA.")
        path = str(getattr(item, "filename", ""))
        entry = {
            "path": path,
            "size": size,
            "is_cached": is_cached,
            "will_download": will_download,
        }
        blob = blobs.get(path)
        if blob:
            entry["blob"] = blob
        files.append(entry)
        selected += size
        cached += size if is_cached else 0
        transfer += size if will_download else 0
    files.sort(key=lambda item: (-item["size"], item["path"]))
    timestamp = planned_at or _now_iso()
    return DownloadPlan(
        requested_revision=requested_revision,
        sha=resolved_sha,
        mode=selection.mode,
        allow_patterns=list(selection.allow_patterns),
        ignore_patterns=list(selection.ignore_patterns),
        files=files,
        selected_bytes=selected,
        transfer_bytes=transfer,
        cached_bytes=cached,
        resolved_at=resolved_at or timestamp,
        planned_at=timestamp,
    )


def plan_from_payload(payload: dict[str, Any]) -> DownloadPlan:
    """Rebuild a plan from a stored job payload (attested by fixtures)."""
    if payload.get("schema_version") != PLANNER_VERSION:
        raise PlanError(f"Unsupported plan schema_version: {payload.get('schema_version')!r}")
    try:
        selection = payload["selection"]
        plan = DownloadPlan(
            requested_revision=payload["requested_revision"],
            sha=payload["resolved_sha"],
            mode=payload["mode"],
            allow_patterns=list(payload["allow_patterns"]),
            ignore_patterns=list(payload["ignore_patterns"]),
            files=[dict(item) for item in selection["files"]],
            selected_bytes=int(selection["selected_bytes"]),
            transfer_bytes=int(selection["transfer_bytes"]),
            cached_bytes=int(selection["cached_bytes"]),
            resolved_at=payload["resolved_at"],
            planned_at=payload["planned_at"],
        )
    except (KeyError, TypeError, ValueError) as error:
        raise PlanError("Stored download plan is incomplete.") from error
    return plan


def selection_covers(selection: Selection, path: str) -> bool:
    """Reference semantic: does ``selection`` include this repo path?

    Mirrors what the patterns eventually select, giving the dry-run and
    the resolver an independent cross-check in tests.
    """
    if any(fnmatch.fnmatch(path, pattern) for pattern in selection.ignore_patterns):
        return False
    if selection.mode == "full":
        return True
    include = selection.allow_patterns
    if selection.mode in {"safetensors", "gguf", "metadata"}:
        name = path.lower()
        metadata = is_metadata_file(path)
        if selection.mode == "safetensors":
            return name.endswith(".safetensors") or metadata
        if selection.mode == "gguf":
            return path == selection.gguf_path or metadata
        if selection.mode == "metadata":
            return metadata
        return False
    if selection.mode == "custom":
        return any(fnmatch.fnmatch(path, pattern) for pattern in include)
    return False

"""Model Context Protocol server over the agent-key API.

Agents that speak MCP connect here the way a browser connects to the
web app: the same library, the same visibility rules, the same scope
ladder. The transport is the streamable-HTTP shape of the protocol in
its ``2025-06-18`` form — one JSON-RPC message per POST, answered with
a single JSON response — kept deliberately stateless: no sessions, no
SSE streams, no server-to-client requests for a library that acts only
when asked.

Every tool call is the owner acting through a key: tools resolve
against the acting user's visible models exactly like the HTTP
endpoints they reuse, so an agent cannot see past what its owner can.
Authorization, once more, is the scope ladder — a key without the
matching scope gets an honest, per-tool denial it can report.

Tool failures are reported inside ``tools/call`` results (``isError``)
per the protocol, so an LLM sees the reason and can self-correct;
protocol-level errors are reserved for framing: unknown methods,
malformed JSON, rejected batches.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .brand import BRAND
from .config import repository_path, settings as app_settings
from .deletion import DeletionError, delete_selected_files, delete_whole_copy
from .planning import SelectionError, resolve_selection
from .updates import UpdateApplyError, apply_paths, exact_patterns
from .verify import VerifyError, verify_copy
from .watch import WatchError

MCP_PROTOCOL_VERSION = "2025-06-18"
JSONRPC_VERSION = "2.0"

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

MAX_TEXT_CONTENT = 240_000


class McpToolError(ValueError):
    """A tool-origin failure that belongs inside an isError result."""


@dataclass
class McpServices:
    """Everything the tool handlers reuse from the application."""

    database: Any
    hub: Any
    downloads: Any
    indexer: Any
    model_storage: Any
    settings: Any
    rescan_index: Callable[[], dict[str, Any]] | None = None
    tracked_update_diff: Callable[..., dict[str, Any]] | None = None
    watch_ensure: Callable[..., Any] | None = None
    locations: list = field(default_factory=list)


def _rpc_result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}


def _rpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": JSONRPC_VERSION,
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def parse_error_response() -> dict[str, Any]:
    """The framed error for a body that never parsed as JSON."""
    return _rpc_error(None, PARSE_ERROR, "Invalid JSON payload.")


def _text_call_result(result: dict[str, Any]) -> dict[str, Any]:
    text = json.dumps(result, indent=2)
    return {
        "content": [
            {
                "type": "text",
                "text": text[:MAX_TEXT_CONTENT],
            }
        ],
        "structuredContent": result,
    }


def tool_definitions() -> list[dict[str, Any]]:
    """The tools an MCP client sees from ``tools/list``."""
    return [
        {
            "name": "search_models",
            "description": (
                "Search the Model Hub by keyword with optional task filter; "
                "returns catalog metadata (downloads, likes, sizes) for each match."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Free-text search terms."},
                    "sort": {
                        "type": "string",
                        "enum": ["trending", "downloads", "updated", "likes"],
                    },
                    "task": {"type": "string", "description": "Pipeline tag filter."},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                },
            },
            "annotations": {"readOnlyHint": True, "openWorldHint": True},
            "requiredScope": "search",
        },
        {
            "name": "model_details",
            "description": (
                "Full Hub record for one repository: revision, file list with "
                "sizes and blob ids, totals, and the source link."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "repo_id": {"type": "string", "description": "org/name on the Hub."},
                    "revision": {"type": "string", "description": "Branch or tag, default main."},
                },
                "required": ["repo_id"],
            },
            "annotations": {"readOnlyHint": True, "openWorldHint": True},
            "requiredScope": "search",
        },
        {
            "name": "library_list",
            "description": (
                "List the stored model copies visible to this key's owner, "
                "with sizes, locations, and staleness timestamps."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Substring filter."}},
            },
            "annotations": {"readOnlyHint": True, "openWorldHint": False},
            "requiredScope": "read",
        },
        {
            "name": "model_files",
            "description": (
                "Enumerate the files of one stored copy as they exist on "
                "disk (or object storage), with per-file sizes."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"repo_id": {"type": "string"}},
                "required": ["repo_id"],
            },
            "annotations": {"readOnlyHint": True, "openWorldHint": False},
            "requiredScope": "read",
        },
        {
            "name": "check_updates",
            "description": (
                "Compare a stored copy against the tracked revision upstream: "
                "changed, added, and removed files, and what a refetch would transfer."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"repo_id": {"type": "string"}},
                "required": ["repo_id"],
            },
            "annotations": {"readOnlyHint": True, "openWorldHint": True},
            "requiredScope": "read",
        },
        {
            "name": "verify_copy",
            "description": (
                "Attest a stored copy against its manifest: present, missing, "
                "and size-drifted files plus untracked extras. Reports and never mutates."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"repo_id": {"type": "string"}},
                "required": ["repo_id"],
            },
            "annotations": {"readOnlyHint": True, "openWorldHint": False},
            "requiredScope": "read",
        },
        {
            "name": "storage_locations",
            "description": "The declared storage locations and which one is primary.",
            "inputSchema": {"type": "object", "properties": {}},
            "annotations": {"readOnlyHint": True, "openWorldHint": False},
            "requiredScope": "read",
        },
        {
            "name": "plan_download",
            "description": (
                "Dry-run a download: resolve the revision, select the files the "
                "mode and patterns imply, and return sizes to transfer — nothing stored, nothing queued."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "repo_id": {"type": "string"},
                    "revision": {"type": "string"},
                    "mode": {
                        "type": "string",
                        "enum": ["full", "safetensors", "gguf", "metadata", "custom"],
                    },
                    "gguf_path": {"type": "string", "description": "Required when mode is gguf."},
                    "include_patterns": {"type": "array", "items": {"type": "string"}},
                    "exclude_patterns": {"type": "array", "items": {"type": "string"}},
                    "exclude_unsafe": {"type": "boolean"},
                },
                "required": ["repo_id"],
            },
            "annotations": {"readOnlyHint": True, "openWorldHint": True},
            "requiredScope": "read",
        },
        {
            "name": "queue_download",
            "description": (
                "Queue a download into the library with the same selection "
                "options as plan_download. Repeats return the existing job. "
                "By default this also watches the repository for upstream change."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "repo_id": {"type": "string"},
                    "revision": {"type": "string"},
                    "mode": {
                        "type": "string",
                        "enum": ["full", "safetensors", "gguf", "metadata", "custom"],
                    },
                    "gguf_path": {"type": "string"},
                    "include_patterns": {"type": "array", "items": {"type": "string"}},
                    "exclude_patterns": {"type": "array", "items": {"type": "string"}},
                    "exclude_unsafe": {"type": "boolean"},
                    "watch": {
                        "type": "boolean",
                        "description": "Also watch the repository for updates (default true).",
                    },
                },
                "required": ["repo_id"],
            },
            "annotations": {
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": True,
            },
            "requiredScope": "queue",
        },
        {
            "name": "download_status",
            "description": (
                "Report one download job exactly, or the recent jobs when no "
                "id is given, including progress and errors."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"download_id": {"type": "string"}},
            },
            "annotations": {"readOnlyHint": True, "openWorldHint": False},
            "requiredScope": "read",
        },
        {
            "name": "queue_update",
            "description": (
                "Refetch changed or missing files of a stored copy through the "
                "update flow. Pass no paths to take every applyable row."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "repo_id": {"type": "string"},
                    "paths": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["repo_id"],
            },
            "annotations": {
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": False,
                "openWorldHint": True,
            },
            "requiredScope": "queue",
        },
        {
            "name": "move_copy",
            "description": (
                "Move a stored copy between declared storage locations. "
                "Staged on the destination, verified, then the source is removed last."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "repo_id": {"type": "string"},
                    "destination": {"type": "string", "description": "Declared location name."},
                },
                "required": ["repo_id", "destination"],
            },
            "annotations": {
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": False,
                "openWorldHint": False,
            },
            "requiredScope": "mutate",
        },
        {
            "name": "delete_files",
            "description": (
                "Delete chosen files from one stored copy. The manifest "
                "travels with what remains; the copy stays in the library."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "repo_id": {"type": "string"},
                    "paths": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                },
                "required": ["repo_id", "paths"],
            },
            "annotations": {
                "readOnlyHint": False,
                "destructiveHint": True,
                "idempotentHint": False,
                "openWorldHint": False,
            },
            "requiredScope": "mutate",
        },
        {
            "name": "delete_copy",
            "description": (
                "Remove an entire stored copy from the library. Requires "
                "confirmation=repo_id exactly, like the HTTP API; the whole "
                "directory is deleted."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "repo_id": {"type": "string"},
                    "confirmation": {
                        "type": "string",
                        "description": "Repeat repo_id exactly to confirm.",
                    },
                },
                "required": ["repo_id", "confirmation"],
            },
            "annotations": {
                "readOnlyHint": False,
                "destructiveHint": True,
                "idempotentHint": False,
                "openWorldHint": False,
            },
            "requiredScope": "mutate",
        },
        {
            "name": "rescan_storage",
            "description": (
                "Walk every declared location and object storage, rebuilding "
                "the index from what is actually on disk."
            ),
            "inputSchema": {"type": "object", "properties": {}},
            "annotations": {
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": False,
            },
            "requiredScope": "mutate",
        },
    ]


def _require_scope(scopes: set[str], required: str) -> None:
    if required not in scopes:
        raise McpToolError(
            f"This agent key lacks the '{required}' scope required for that tool."
        )


def _visible_model(services: McpServices, user: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    repo_id = str(args.get("repo_id") or "")
    try:
        from .config import validate_repo_id

        validated = validate_repo_id(repo_id)
    except ValueError as error:
        raise McpToolError(str(error)) from error
    model = services.database.get_visible_local_model(user["id"], validated)
    if not model:
        raise McpToolError(f"Local model not found: {repo_id}")
    return model


def _model_root(services: McpServices, model: dict[str, Any]) -> Path:
    """Resolve a copy's directory through its declared location.

    Mirrors the application's model_root: records whose location is
    undeclared fall back to the first (primary) location.
    """
    from .locations import find_location

    declared = find_location(services.locations, model.get("location"))
    root = (declared or services.locations[0]).root
    target = (root / model["relative_path"]).resolve()
    resolved_root = root.resolve()
    if target != resolved_root and resolved_root not in target.parents:
        raise ValueError("A storage record pointed outside its declared location.")
    return target


def _tool_search_models(services: McpServices, user: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    sort = args.get("sort") or "trending"
    if sort not in ("trending", "downloads", "updated", "likes"):
        raise McpToolError("sort must be trending, downloads, updated, or likes.")
    limit = int(args.get("limit") or 30)
    items = services.hub.search_models(
        str(args.get("query") or ""),
        sort,
        str(args.get("task") or ""),
        "",
        "",
        "",
        max(1, min(limit, 50)),
    )
    local_ids = {model["repo_id"] for model in services.database.list_local_models()}
    for item in items:
        item["local"] = item["id"] in local_ids
    return {"items": items, "count": len(items)}


def _tool_model_details(services: McpServices, user: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    repo_id = str(args.get("repo_id") or "")
    from .config import validate_repo_id

    try:
        validated = validate_repo_id(repo_id)
    except ValueError as error:
        raise McpToolError(str(error)) from error
    details = services.hub.model_details(validated, str(args.get("revision") or "main"))
    details["local"] = services.database.get_local_model(validated) is not None
    return details


def _tool_library_list(services: McpServices, user: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    items = services.database.list_visible_local_models(user["id"], str(args.get("query") or ""))
    return {
        "items": items,
        "count": len(items),
        "total_bytes": sum(item["size_bytes"] for item in items),
    }


def _tool_model_files(services: McpServices, user: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    model = _visible_model(services, user, args)
    root = _model_root(services, model)
    if model["storage_backend"] == "s3" and (not model["cached"] or not root.is_dir()):
        remote = services.model_storage.list_repository_files(model["repo_id"])
        if not remote:
            raise McpToolError(f"S3 model files were not found for {model['repo_id']}.")
        return remote
    result = services.indexer.files_for_model(model["repo_id"])
    if not result:
        raise McpToolError(f"Local model not found: {model['repo_id']}")
    return result


def _tool_check_updates(services: McpServices, user: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    if services.tracked_update_diff is None:
        raise McpToolError("Update checking is not available in this deployment.")
    model = _visible_model(services, user, args)
    return services.tracked_update_diff(model, _model_root(services, model))


def _tool_verify_copy(services: McpServices, user: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    model = _visible_model(services, user, args)
    try:
        return verify_copy(_model_root(services, model))
    except VerifyError as error:
        raise McpToolError(str(error)) from error


def _tool_storage_locations(services: McpServices, user: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    from .locations import find_location

    primary = find_location(services.locations, "primary")
    return {
        "items": [
            {"name": location.name, "root": str(location.root)}
            for location in services.locations
        ],
        "primary": primary.name if primary else None,
        "count": len(services.locations),
    }


def _download_options(args: dict[str, Any]) -> dict[str, Any]:
    mode = args.get("mode") or "full"
    if mode not in ("full", "safetensors", "gguf", "metadata", "custom"):
        raise McpToolError("mode must be full, safetensors, gguf, metadata, or custom.")
    include = args.get("include_patterns")
    exclude = args.get("exclude_patterns")
    if include is not None and not (isinstance(include, list) and all(isinstance(i, str) for i in include)):
        raise McpToolError("include_patterns must be a list of strings.")
    if exclude is not None and not (isinstance(exclude, list) and all(isinstance(i, str) for i in exclude)):
        raise McpToolError("exclude_patterns must be a list of strings.")
    return {
        "revision": str(args.get("revision") or "main").strip() or "main",
        "mode": mode,
        "gguf_path": str(args.get("gguf_path") or ""),
        "exclude_unsafe": bool(args.get("exclude_unsafe")),
        "custom_include": list(include or []),
        "custom_exclude": list(exclude or []),
    }


def _tool_plan_download(services: McpServices, user: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    from .config import validate_repo_id

    try:
        validated = validate_repo_id(str(args.get("repo_id") or ""))
    except ValueError as error:
        raise McpToolError(str(error)) from error
    options = _download_options(args)
    try:
        selection = resolve_selection(
            options["mode"],
            gguf_path=options["gguf_path"],
            custom_include=options["custom_include"],
            custom_exclude=options["custom_exclude"],
            exclude_unsafe=options["exclude_unsafe"],
        )
    except SelectionError as error:
        raise McpToolError(str(error)) from error
    target = repository_path(validated, services.settings.model_storage)
    try:
        details = services.downloads.hub.model_details(validated, options["revision"])
        plan, _ = services.downloads._plan(
            validated, options["revision"], selection, target, details
        )
    except Exception as error:
        raise McpToolError(f"Planning failed: {error}") from error
    return {"status": "planned", "plan": plan.to_payload()}


def _tool_queue_download(services: McpServices, user: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    options = _download_options(args)
    try:
        job = services.downloads.queue(
            str(args.get("repo_id") or ""),
            options["revision"],
            options["mode"],
            user_id=user["id"],
            gguf_path=options["gguf_path"],
            exclude_unsafe=options["exclude_unsafe"],
            custom_include=options["custom_include"],
            custom_exclude=options["custom_exclude"],
        )
    except ValueError as error:
        raise McpToolError(str(error)) from error
    result: dict[str, Any] = {"status": "queued", "job": job}
    if args.get("watch", True):
        # D13 default: the download subscribes its owner. A failed
        # subscription must not misreport the queued download.
        if services.watch_ensure is not None:
            try:
                services.watch_ensure(str(args.get("repo_id") or ""), user["id"])
            except WatchError as error:
                result["watch_error"] = str(error)
    return result


def _tool_download_status(services: McpServices, user: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    download_id = args.get("download_id")
    if download_id:
        job = services.database.get_download(str(download_id))
        if not job or (
            job.get("user_id") != user["id"]
            and not (user["role"] == "admin" and job.get("user_id") is None)
        ):
            raise McpToolError("Download not found")
        return {"job": job}
    jobs = services.database.list_downloads(user_id=user["id"], include_unowned=False)
    active = sum(job["status"] in ("queued", "preparing", "downloading") for job in jobs)
    jobs = jobs[:25]
    return {"items": jobs, "count": len(jobs), "active": active}


def _tool_queue_update(services: McpServices, user: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    if services.tracked_update_diff is None:
        raise McpToolError("Update application is not available in this deployment.")
    model = _visible_model(services, user, args)
    if services.database.find_active_download(model["repo_id"]):
        raise McpToolError("Wait for the active download to finish before updating this model.")
    diff = services.tracked_update_diff(model, _model_root(services, model))
    requested = args.get("paths")
    if requested is None:
        paths = None
    else:
        if not (isinstance(requested, list) and all(isinstance(item, str) for item in requested)):
            raise McpToolError("paths must be a list of strings.")
        paths = requested
    try:
        applyable = apply_paths(diff, paths)
    except UpdateApplyError as error:
        raise McpToolError(str(error)) from error
    if not applyable:
        raise McpToolError("Nothing to update: this copy already matches the upstream revision.")
    try:
        job = services.downloads.queue(
            model["repo_id"],
            diff["upstream"]["sha"],
            mode="custom",
            user_id=user["id"],
            custom_include=exact_patterns(applyable),
            update_merge=True,
        )
    except ValueError as error:
        raise McpToolError(str(error)) from error
    return {"status": "queued", "paths": applyable, "job": job}


def _require_declared(services: McpServices, name: str) -> Any:
    from .locations import find_location

    location = find_location(services.locations, name)
    if not location:
        raise McpToolError(f"Unknown storage location '{name}'.")
    return location


def _tool_move_copy(services: McpServices, user: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    from .moves import MoveError, apply_move, build_move_plan

    model = _visible_model(services, user, args)
    if model["storage_backend"] != "filesystem":
        raise McpToolError(
            "S3-backed copies are moved by restoring or evicting the local cache instead."
        )
    if services.database.find_active_download(model["repo_id"]):
        raise McpToolError("Wait for the active download to finish before moving this model.")
    source = _require_declared(services, str(model.get("location") or "primary"))
    destination = _require_declared(services, str(args.get("destination") or ""))
    if destination.name == source.name:
        raise McpToolError("This copy is already stored on that location.")
    try:
        try:
            free_bytes: int | None = shutil.disk_usage(destination.root).free
        except OSError:
            free_bytes = None
        plan = build_move_plan(
            model["repo_id"],
            source,
            destination,
            model["relative_path"],
            destination_free_bytes=free_bytes,
        )
        result = apply_move(plan, source, destination, indexer=services.indexer)
    except MoveError as error:
        raise McpToolError(str(error)) from error
    return {"status": "moved", **result}


def _tool_delete_files(services: McpServices, user: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    paths = args.get("paths")
    if not (isinstance(paths, list) and paths and all(isinstance(item, str) for item in paths)):
        raise McpToolError("paths must be a non-empty list of strings.")
    if len(paths) > 200:
        raise McpToolError("Delete at most 200 files per call.")
    model = _visible_model(services, user, args)
    if services.database.find_active_download(model["repo_id"]):
        raise McpToolError("Wait for the active download to finish before deleting files.")
    try:
        summary = delete_selected_files(
            model["repo_id"],
            _model_root(services, model),
            [str(path) for path in paths],
            storage_backend=model["storage_backend"],
        )
    except DeletionError as error:
        raise McpToolError(str(error)) from error
    services.indexer.index_path(_model_root(services, model))
    services.model_storage.sync_repository(model["repo_id"], _model_root(services, model))
    return summary


def _tool_delete_copy(services: McpServices, user: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    # Same contract as DELETE /api/local-models/{repo_id}: the caller
    # types the exact repo id. An agent acting on a prompt injection
    # cannot destroy a copy without spelling its name.
    confirmation = args.get("confirmation")
    if confirmation != args.get("repo_id"):
        raise McpToolError(
            "Refusing to delete: pass confirmation=repo_id exactly to "
            "remove an entire stored copy."
        )
    model = _visible_model(services, user, args)
    if services.database.find_active_download(model["repo_id"]):
        raise McpToolError("Wait for the active download to finish before deleting this model.")
    try:
        delete_whole_copy(
            model["repo_id"],
            _model_root(services, model),
            model["repo_id"],
            model_storage=services.model_storage,
            storage_root=services.settings.model_storage,
        )
    except DeletionError as error:
        raise McpToolError(str(error)) from error
    services.database.delete_local_model(model["repo_id"])
    return {"status": "deleted", "repo_id": model["repo_id"]}


def _tool_rescan_storage(services: McpServices, user: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    if services.rescan_index is None:
        raise McpToolError("Storage rescanning is not available in this deployment.")
    result = services.rescan_index()
    return {
        "status": "rescanned",
        "indexed_models": result.get("count"),
        "local_count": result.get("local_count"),
        "remote_count": result.get("remote_count"),
        "remote_error": result.get("remote_error"),
        "scanned_at": result.get("scanned_at"),
    }


TOOL_HANDLERS: dict[str, Callable[[McpServices, dict[str, Any], dict[str, Any]], dict[str, Any]]] = {
    "search_models": _tool_search_models,
    "model_details": _tool_model_details,
    "library_list": _tool_library_list,
    "model_files": _tool_model_files,
    "check_updates": _tool_check_updates,
    "verify_copy": _tool_verify_copy,
    "storage_locations": _tool_storage_locations,
    "plan_download": _tool_plan_download,
    "queue_update": _tool_queue_update,
    "download_status": _tool_download_status,
    "queue_download": _tool_queue_download,
    "move_copy": _tool_move_copy,
    "delete_files": _tool_delete_files,
    "delete_copy": _tool_delete_copy,
    "rescan_storage": _tool_rescan_storage,
}


def _tool_scope(name: str) -> str | None:
    for tool in tool_definitions():
        if tool["name"] == name:
            return tool["requiredScope"]
    return None


def call_tool(
    name: str,
    arguments: dict[str, Any] | None,
    user: dict[str, Any],
    scopes: set[str],
    services: McpServices,
) -> dict[str, Any]:
    """Run one tool, returning a CallToolResult-shaped dict."""
    scope = _tool_scope(name)
    if scope is None:
        raise LookupError(f"Unknown tool: {name}")
    try:
        _require_scope(scopes, scope)
        handler = TOOL_HANDLERS[name]
        result = handler(services, user, arguments if isinstance(arguments, dict) else {})
        return _text_call_result(result)
    except McpToolError as error:
        return {
            "content": [{"type": "text", "text": str(error)}],
            "isError": True,
        }
    except Exception as error:  # tool-origin failure, LLM-owned, never a crash
        return {
            "content": [{"type": "text", "text": f"{error.__class__.__name__}: {error}"}],
            "isError": True,
        }


def initialize_result(client_protocol_version: Any) -> dict[str, Any]:
    return {
        "protocolVersion": MCP_PROTOCOL_VERSION,
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {
            "name": BRAND.display_name,
            "title": BRAND.display_name,
            "version": app_settings.app_version,
        },
        "instructions": (
            "A self-hosted model library. Discover repositories with "
            "search_models and model_details, inspect stored copies with "
            "library_list and model_files, check health with check_updates "
            "and verify_copy, preview with plan_download, then act with "
            "queue_download, queue_update, move_copy, or deletions. Acting "
            "through this server uses the identity and scopes of the agent key."
        ),
    }


def handle_mcp_message(
    payload: Any,
    user: dict[str, Any],
    scopes: set[str],
    services: McpServices,
) -> tuple[int, dict[str, Any] | None]:
    """Dispatch one JSON-RPC message; (status, response-or-None for notifications)."""
    if not isinstance(payload, dict):
        return 400, _rpc_error(None, INVALID_REQUEST, "A single JSON-RPC message object is expected.")
    if payload.get("jsonrpc") != JSONRPC_VERSION:
        return 400, _rpc_error(payload.get("id"), INVALID_REQUEST, "Missing jsonrpc 2.0 envelope.")
    method = payload.get("method")
    request_id = payload.get("id")
    if not isinstance(method, str) or not method:
        return 400, _rpc_error(request_id, INVALID_REQUEST, "Missing method.")
    is_request = "id" in payload and not isinstance(payload.get("id"), (dict, list))

    if method == "initialize":
        params = payload.get("params") or {}
        return 200, _rpc_result(request_id, initialize_result(params.get("protocolVersion")))
    if method == "ping":
        return 200, _rpc_result(request_id, {})
    if method == "tools/list":
        tools = [
            {key: value for key, value in tool.items() if key != "requiredScope"}
            for tool in tool_definitions()
        ]
        return 200, _rpc_result(request_id, {"tools": tools})
    if method == "tools/call":
        params = payload.get("params") or {}
        name = params.get("name")
        if not isinstance(name, str) or name not in TOOL_HANDLERS:
            return 400, _rpc_error(
                request_id, INVALID_PARAMS, f"Unknown tool: {name!r}"
            )
        result = call_tool(name, params.get("arguments"), user, scopes, services)
        return 200, _rpc_result(request_id, result)
    if method.startswith("notifications/"):
        if not is_request:
            return 202, None
        return 400, _rpc_error(request_id, INVALID_REQUEST, f"'{method}' is a notification.")
    return 400, _rpc_error(request_id, METHOD_NOT_FOUND, f"Method not found: {method}")

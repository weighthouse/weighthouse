from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .config import Settings, validate_repo_id
from .database import Database
from .locations import StorageLocation, find_location
from .gguf import gguf_parameter_count
from .manifests import MANIFEST_FILENAMES, read_manifest


WEIGHT_EXTENSIONS = {
    ".safetensors",
    ".gguf",
    ".bin",
    ".pt",
    ".pth",
    ".ckpt",
    ".onnx",
    ".h5",
    ".msgpack",
}
UNSAFE_EXTENSIONS = {".bin", ".pt", ".pth", ".pkl", ".pickle", ".ckpt"}
CONFIG_FILES = {"config.json", "model_index.json", "tokenizer.json", "params.json"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def directory_stats(path: Path, include_cache: bool = False) -> tuple[int, int, float]:
    size = 0
    count = 0
    latest = path.stat().st_mtime if path.exists() else 0
    for root, directories, files in os.walk(path):
        directories[:] = [
            name for name in directories if include_cache or name not in {".cache", "__pycache__"}
        ]
        for name in files:
            file_path = Path(root) / name
            try:
                stat = file_path.stat()
            except (FileNotFoundError, PermissionError, OSError):
                continue
            size += stat.st_size
            count += 1
            latest = max(latest, stat.st_mtime)
    return size, count, latest


def first_gguf_file(path: Path) -> Path | None:
    try:
        for entry in sorted(path.iterdir()):
            if entry.is_file() and entry.suffix.lower() == ".gguf":
                return entry
    except OSError:
        return None
    return None


def parse_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


class LocalModelIndexer:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        locations: list[StorageLocation] | None = None,
    ):
        self.settings = settings
        self.database = database
        self._locations = locations

    def set_locations(self, locations: list[StorageLocation]) -> None:
        """Declare the storage roots the indexer walks.

        Defaults to the primary root only; the application registers the
        parsed allowlist right after startup so scans cover every
        declared location.
        """
        self._locations = locations

    def _roots(self) -> list[tuple[str, Path]]:
        if self._locations:
            return [(loc.name, loc.root) for loc in self._locations]
        return [("primary", self.settings.model_storage)]

    def _owning_location(self, resolved: Path) -> tuple[str, Path]:
        for name, root in self._roots():
            if root == resolved or root in resolved.parents:
                return name, root
        raise ValueError("Model path escapes every configured storage location")

    def _candidates(self) -> Iterable[Path]:
        for _, root in self._roots():
            if not root.exists():
                continue
            for found in self._candidates_under(root):
                yield found

    def _candidates_under(self, storage: Path) -> Iterable[Path]:
        for root, directories, files in os.walk(storage):
            path = Path(root)
            relative = path.relative_to(storage)
            directories[:] = [
                name
                for name in directories
                if name not in {".cache", "__pycache__"} and not name.startswith(".")
            ]
            if len(relative.parts) > 3:
                directories[:] = []
                continue
            file_set = set(files)
            has_manifest = any(name in file_set for name in MANIFEST_FILENAMES)
            has_config = bool(file_set & CONFIG_FILES)
            has_weight = any(Path(name).suffix.lower() in WEIGHT_EXTENSIONS for name in files)
            if has_manifest or has_config or has_weight:
                if has_manifest:
                    manifest = read_manifest(path)
                    if manifest.get("status") not in {None, "complete"}:
                        directories[:] = []
                        continue
                yield path
                directories[:] = []

    def index_path(self, path: Path) -> dict[str, Any]:
        resolved = path.resolve()
        location_name, storage = self._owning_location(resolved)
        relative = resolved.relative_to(storage).as_posix()
        manifest = read_manifest(resolved)
        config = parse_json(resolved / "config.json")
        repo_id = manifest.get("repo_id") or (
            relative if "/" in relative else f"local/{relative}"
        )
        try:
            validate_repo_id(repo_id)
        except ValueError as error:
            # An out-of-band manifest cannot register a record no API
            # route could address or manage; discovery skips the tree,
            # exactly as remote storage discovery already does.
            raise ValueError(
                f"Manifest names an invalid repository id: {repo_id}"
            ) from error
        if manifest.get("source") == "user-upload":
            owner_id = manifest.get("owner_id")
            if (
                not owner_id
                or not self.database.get_owned_repository(repo_id, owner_id)
            ):
                raise PermissionError(
                    "User-uploaded repositories require matching ownership metadata."
                )
        size, file_count, latest = directory_stats(resolved)
        tags = manifest.get("tags") or []
        gguf = first_gguf_file(resolved)
        record = {
            "repo_id": repo_id,
            "relative_path": relative,
            "size_bytes": size,
            "file_count": file_count,
            "modified_at": datetime.fromtimestamp(latest, timezone.utc).isoformat(),
            "downloaded_at": manifest.get("downloaded_at"),
            "revision": manifest.get("revision"),
            "sha": manifest.get("sha"),
            "pipeline_tag": manifest.get("pipeline_tag") or config.get("model_type"),
            "library_name": manifest.get("library_name"),
            "license": manifest.get("license"),
            "tags_json": json.dumps(tags),
            "config_json": json.dumps(
                {
                    "architectures": config.get("architectures"),
                    "model_type": config.get("model_type"),
                    "torch_dtype": config.get("torch_dtype"),
                    "vocab_size": config.get("vocab_size"),
                }
            ),
            "source_url": manifest.get("source_url"),
            "managed": 1 if manifest else 0,
            "storage_backend": manifest.get("storage_backend") or "filesystem",
            "cached": 1,
            "remote_uri": manifest.get("remote_uri"),
            "location": manifest.get("location") or location_name,
            "parameter_count": gguf_parameter_count(gguf) if gguf else None,
        }
        self.database.upsert_local_model(record)
        return self.database.get_local_model(repo_id)

    def index_remote(self, model: dict[str, Any]) -> dict[str, Any] | None:
        record = {
            "repo_id": model["repo_id"],
            "relative_path": model.get("relative_path") or model["repo_id"],
            "size_bytes": int(model.get("size_bytes") or 0),
            "file_count": int(model.get("file_count") or 0),
            "modified_at": model.get("modified_at") or utc_now(),
            "downloaded_at": model.get("downloaded_at"),
            "revision": model.get("revision"),
            "sha": model.get("sha"),
            "pipeline_tag": model.get("pipeline_tag"),
            "library_name": model.get("library_name"),
            "license": model.get("license"),
            "tags_json": json.dumps(model.get("tags") or []),
            "config_json": json.dumps(model.get("config") or {}),
            "source_url": model.get("source_url"),
            "managed": int(bool(model.get("managed", True))),
            "storage_backend": "s3",
            "cached": int(bool(model.get("cached"))),
            "remote_uri": model.get("remote_uri"),
            "parameter_count": model.get("parameter_count"),
        }
        self.database.upsert_local_model(record)
        return self.database.get_local_model(model["repo_id"])

    def scan(self) -> dict[str, Any]:
        indexed = []
        paths: set[str] = set()
        seen_repos: set[str] = set()
        for candidate in self._candidates() or []:
            repo_id = self._candidate_repo_id(candidate)
            if repo_id in seen_repos:
                # One repo lives at one location at a time. The first
                # declared location wins deterministically; an identical
                # tree at a later root is skipped, never re-recorded.
                continue
            try:
                model = self.index_path(candidate)
            except (OSError, ValueError):
                continue
            if model:
                seen_repos.add(model["repo_id"])
                indexed.append(model)
                paths.add(model["relative_path"])
        self.database.prune_local_models(paths)
        return {"count": len(indexed), "models": indexed, "scanned_at": utc_now()}

    def _candidate_repo_id(self, candidate: Path) -> str:
        manifest = read_manifest(candidate)
        if manifest.get("repo_id"):
            return str(manifest["repo_id"])
        try:
            _, storage = self._owning_location(candidate.resolve())
            relative = candidate.resolve().relative_to(storage).as_posix()
        except ValueError:
            relative = candidate.resolve().name
        return relative if "/" in relative else f"local/{relative}"

    def _owning_location_for_model(self, model: dict[str, Any]) -> tuple[str, Path]:
        """Resolve the declared root a stored record points at.

        Falls back to the primary location when the record's location
        name is no longer declared (an operator removed it): the copy is
        still reachable there rather than vanishing from the UI.
        """
        declared = find_location(
            self._locations or [], str(model.get("location") or "")
        )
        if declared:
            return declared.name, declared.root
        return "primary", self.settings.model_storage

    def files_for_model(self, repo_id: str, limit: int = 500) -> dict[str, Any] | None:
        model = self.database.get_local_model(repo_id)
        if not model:
            return None
        name, root = self._owning_location_for_model(model)
        resolved = (root / model["relative_path"]).resolve()
        files = []
        unsafe_count = 0
        for current, directories, names in os.walk(resolved):
            directories[:] = [name for name in directories if name != ".cache"]
            for name in names:
                path = Path(current) / name
                relative = path.relative_to(resolved).as_posix()
                try:
                    stat = path.stat()
                except (OSError, PermissionError):
                    continue
                suffix = path.suffix.lower()
                unsafe = suffix in UNSAFE_EXTENSIONS
                unsafe_count += int(unsafe)
                files.append(
                    {
                        "path": relative,
                        "size": stat.st_size,
                        "modified_at": datetime.fromtimestamp(
                            stat.st_mtime, timezone.utc
                        ).isoformat(),
                        "unsafe_serialization": unsafe,
                    }
                )
                if len(files) >= limit:
                    break
            if len(files) >= limit:
                break
        files.sort(key=lambda item: (-item["size"], item["path"]))
        return {
            "model": model,
            "files": files,
            "unsafe_file_count": unsafe_count,
            "truncated": len(files) >= limit,
        }

from __future__ import annotations

import os
import re
import shutil
import threading
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

from .brand import BRAND
from .config import Settings, validate_repo_id
from .database import Database
from .indexer import LocalModelIndexer, directory_stats, utc_now
from .manifests import MANIFEST_FILENAMES, read_manifest, write_manifest
from .storage import FilesystemModelStorage


SLUG_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
RESERVED_FILENAMES = set(MANIFEST_FILENAMES)
RESERVED_PARTS = {".git", ".cache", "__pycache__"}
PART_SUFFIX = BRAND.part_suffix


def validate_slug(value: str) -> str:
    slug = value.strip()
    if not SLUG_PATTERN.fullmatch(slug):
        raise ValueError(
            "Repository name must be 1-96 letters, numbers, dots, underscores, or hyphens."
        )
    return slug


def validate_upload_path(value: str) -> PurePosixPath:
    cleaned = value.strip().replace("\\", "/")
    path = PurePosixPath(cleaned)
    if (
        not cleaned
        or path.is_absolute()
        or len(cleaned) > 500
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(part in RESERVED_PARTS for part in path.parts)
        or path.name in RESERVED_FILENAMES
        or path.name.endswith(PART_SUFFIX)
    ):
        raise ValueError("Upload path is invalid or reserved.")
    return path


class UploadManager:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        indexer: LocalModelIndexer,
        model_storage: FilesystemModelStorage | None = None,
    ):
        self.settings = settings
        self.database = database
        self.indexer = indexer
        self.model_storage = model_storage or FilesystemModelStorage(settings)
        self._write_lock = threading.RLock()

    def _repository_root(self, repo_id: str) -> Path:
        validated = validate_repo_id(repo_id)
        target = (self.settings.model_storage / validated).resolve()
        if (
            self.settings.model_storage != target
            and self.settings.model_storage not in target.parents
        ):
            raise ValueError("Repository path escapes configured model storage.")
        return target

    def _owned(self, repo_id: str, user_id: str) -> dict[str, Any]:
        repository = self.database.get_owned_repository(repo_id, user_id)
        if not repository:
            raise FileNotFoundError("Uploaded repository not found.")
        return repository

    def _upload_target(self, repo_id: str, file_path: str) -> tuple[Path, Path, PurePosixPath]:
        relative = validate_upload_path(file_path)
        root = self._repository_root(repo_id)
        target = root.joinpath(*relative.parts)
        resolved_parent = target.parent.resolve()
        if root != resolved_parent and root not in resolved_parent.parents:
            raise ValueError("Upload path escapes the owned repository.")
        partial = target.with_name(f".{target.name}{PART_SUFFIX}")
        if target.is_symlink() or partial.is_symlink():
            raise ValueError("Symbolic links are not valid upload targets.")
        return target, partial, relative

    def create_repository(
        self,
        user: dict[str, Any],
        slug: str,
        description: str,
        visibility: str,
    ) -> dict[str, Any]:
        name = validate_slug(slug)
        if visibility not in {"private", "shared"}:
            raise ValueError("Visibility must be private or shared.")
        detail = description.strip()
        if len(detail) > 500:
            raise ValueError("Description must be 500 characters or fewer.")
        repo_id = validate_repo_id(f"{user['username']}/{name}")
        target = self._repository_root(repo_id)
        if target.exists() or self.database.get_owned_repository(repo_id):
            raise FileExistsError("That repository already exists.")
        target.mkdir(parents=True, exist_ok=False)
        timestamp = utc_now()
        manifest = {
            "status": "uploading",
            "repo_id": repo_id,
            "owner_id": user["id"],
            "source": "user-upload",
            "created_at": timestamp,
        }
        write_manifest(target, manifest)
        try:
            return self.database.create_owned_repository(
                {
                    "id": uuid.uuid4().hex,
                    "owner_id": user["id"],
                    "repo_id": repo_id,
                    "description": detail,
                    "visibility": visibility,
                    "status": "uploading",
                    "created_at": timestamp,
                    "updated_at": timestamp,
                }
            )
        except Exception:
            shutil.rmtree(target, ignore_errors=True)
            raise

    def file_status(self, repo_id: str, user_id: str, file_path: str) -> dict[str, Any]:
        self._owned(repo_id, user_id)
        target, partial, _ = self._upload_target(repo_id, file_path)
        if target.is_file():
            return {"offset": target.stat().st_size, "complete": True}
        if partial.is_file():
            return {"offset": partial.stat().st_size, "complete": False}
        return {"offset": 0, "complete": False}

    def upload_chunk(
        self,
        repo_id: str,
        user_id: str,
        file_path: str,
        offset: int,
        total: int,
        payload: bytes,
    ) -> dict[str, Any]:
        repository = self._owned(repo_id, user_id)
        if repository["status"] != "uploading":
            raise ValueError("Repository is finalized and no longer accepts uploads.")
        if offset < 0 or total < 0 or offset + len(payload) > total:
            raise ValueError("Upload offset or total length is invalid.")
        max_bytes = self.settings.max_upload_size_gb * 1024**3
        if total > max_bytes:
            raise ValueError(
                f"One file cannot exceed {self.settings.max_upload_size_gb} GB."
            )
        if len(payload) > self.settings.upload_chunk_mb * 1024**2:
            raise ValueError(
                f"Upload chunks cannot exceed {self.settings.upload_chunk_mb} MB."
            )
        if shutil.disk_usage(self.settings.model_storage).free < len(payload) + 1024**2:
            raise OSError("Not enough free space for this upload chunk.")

        target, partial, relative = self._upload_target(repo_id, file_path)
        with self._write_lock:
            target.parent.mkdir(parents=True, exist_ok=True)
            target, partial, relative = self._upload_target(repo_id, file_path)
            if target.exists():
                if target.is_file() and target.stat().st_size == total:
                    return {"offset": total, "complete": True, "path": relative.as_posix()}
                raise FileExistsError("A different completed file already exists at this path.")
            current = partial.stat().st_size if partial.exists() else 0
            if current != offset:
                raise RuntimeError(f"Upload offset mismatch. Server has {current} bytes.")

            with partial.open("ab") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            uploaded = partial.stat().st_size
            complete = uploaded == total
            if complete:
                partial.replace(target)
        self.database.update_owned_repository(
            repo_id, user_id, updated_at=utc_now()
        )
        return {
            "offset": uploaded,
            "complete": complete,
            "path": relative.as_posix(),
        }

    def finalize(self, repo_id: str, user_id: str) -> dict[str, Any]:
        repository = self._owned(repo_id, user_id)
        with self._write_lock:
            root = self._repository_root(repo_id)
            partials = [
                path for path in root.rglob(f"*{PART_SUFFIX}") if path.is_file()
            ]
            if partials:
                raise ValueError("Finish all file uploads before finalizing the repository.")
            files = [
                path
                for path in root.rglob("*")
                if path.is_file() and path.name not in MANIFEST_FILENAMES
            ]
            if not files:
                raise ValueError("Upload at least one model or metadata file first.")
            size, file_count, _ = directory_stats(root)
            completed = utc_now()
            manifest = {
                "status": "complete",
                "repo_id": repo_id,
                "owner_id": user_id,
                "source": "user-upload",
                "uploaded_at": completed,
                "total_bytes": size,
                "file_count": file_count,
            }
            write_manifest(root, manifest)
            self.model_storage.sync_repository(repo_id, root)
            self.indexer.index_path(root)
            updated = self.database.update_owned_repository(
                repo_id, user_id, status="ready", updated_at=completed
            )
        return updated or repository

    def update_repository(
        self,
        repo_id: str,
        user_id: str,
        description: str,
        visibility: str,
    ) -> dict[str, Any]:
        self._owned(repo_id, user_id)
        if visibility not in {"private", "shared"}:
            raise ValueError("Visibility must be private or shared.")
        detail = description.strip()
        if len(detail) > 500:
            raise ValueError("Description must be 500 characters or fewer.")
        return self.database.update_owned_repository(
            repo_id,
            user_id,
            description=detail,
            visibility=visibility,
            updated_at=utc_now(),
        )

    def delete_repository(self, repo_id: str, user_id: str, confirmation: str) -> None:
        self._owned(repo_id, user_id)
        if confirmation != repo_id:
            raise ValueError("Repository name confirmation does not match.")
        with self._write_lock:
            root = self._repository_root(repo_id)
            manifest = read_manifest(root)
            if not manifest:
                manifest = self.model_storage.repository_manifest(repo_id) or {}
            if not manifest:
                raise ValueError("Repository manifest is missing or unreadable.")
            if (
                manifest.get("owner_id") != user_id
                or manifest.get("source") != "user-upload"
            ):
                raise ValueError("Repository ownership could not be verified.")
            self.model_storage.delete_repository(repo_id)
            if root.exists():
                shutil.rmtree(root)
            self.database.delete_owned_repository(repo_id, user_id)

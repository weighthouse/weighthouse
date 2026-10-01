"""Safe deletion: proposal artifact, selective narrowing, whole-copy removal."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.database import Database
from app.deletion import (
    DeletionError,
    build_deletion_proposal,
    delete_selected_files,
    delete_whole_copy,
)
from app.manifests import MANIFEST_FILENAME, read_manifest, write_manifest


@pytest.fixture()
def storage_root(tmp_path: Path) -> Path:
    root = tmp_path / "storage"
    root.mkdir()
    return root


@pytest.fixture()
def copy_root(storage_root: Path) -> Path:
    root = storage_root / "acme" / "tiny"
    root.mkdir(parents=True)
    (root / "model.safetensors").write_bytes(b"w" * 100)
    (root / "tokenizer.model").write_bytes(b"t" * 24)
    (root / "leftover.bin").write_bytes(b"partial-download")
    cache = root / ".cache" / "huggingface"
    cache.mkdir(parents=True)
    (cache / "metadata.json").write_text("{}", encoding="utf-8")
    write_manifest(
        root,
        {
            "status": "complete",
            "schema_version": 2,
            "repo_id": "acme/tiny",
            "revision": "main",
            "sha": "sha-old",
            "total_bytes": 124,
            "file_count": 2,
            "selection": {
                "files": [
                    {
                        "path": "model.safetensors",
                        "size": 100,
                        "is_cached": False,
                        "will_download": True,
                        "blob": "blob-w",
                    },
                    {
                        "path": "tokenizer.model",
                        "size": 24,
                        "is_cached": True,
                        "will_download": False,
                        "blob": "blob-t",
                    },
                ],
                "file_count": 2,
                "selected_bytes": 124,
                "transfer_bytes": 100,
                "cached_bytes": 24,
                "patterns": {"mode": "full", "allow": [], "ignore": []},
            },
        },
    )
    return root


def test_proposal_lists_tracked_files_untracked_leftovers_and_cache_as_one(
    copy_root: Path,
):
    proposal = build_deletion_proposal("acme/tiny", copy_root)

    assert proposal["schema_version"] == 1
    assert proposal["repo_id"] == "acme/tiny"
    tracked = {entry["path"]: entry["size"] for entry in proposal["tracked"]}
    assert tracked == {"model.safetensors": 100, "tokenizer.model": 24}
    extras_paths = [entry["path"] for entry in proposal["extras"]]
    manifest_names = [name for name in extras_paths if name.endswith(MANIFEST_FILENAME)]
    assert manifest_names == []  # the record itself is never proposed
    assert ".cache" in extras_paths  # hub resume cache collapsed to one entry
    assert "leftover.bin" in extras_paths  # partial from a cancelled job
    assert proposal["whole_copy"]["size_bytes"] > 0
    assert proposal["whole_copy"]["tracked_manifest"] is True


def test_proposal_for_missing_directory_still_works(tmp_path: Path):
    proposal = build_deletion_proposal("acme/gone", tmp_path / "missing" / "nested")
    assert proposal["tracked"] == []
    assert proposal["extras"] == []
    assert proposal["whole_copy"]["size_bytes"] == 0
    assert proposal["whole_copy"]["tracked_manifest"] is False


def test_selective_delete_narrows_the_stored_selection(copy_root: Path):
    summary = delete_selected_files(
        "acme/tiny",
        copy_root,
        ["model.safetensors"],
        storage_backend="filesystem",
    )

    assert summary["deleted"] == ["model.safetensors"]
    assert summary["freed_bytes"] == 100
    assert not (copy_root / "model.safetensors").exists()

    manifest = read_manifest(copy_root)
    files = {entry["path"]: entry for entry in manifest["selection"]["files"]}
    assert set(files) == {"tokenizer.model"}
    assert manifest["selection"]["file_count"] == 1
    assert manifest["selection"]["selected_bytes"] == 24
    assert manifest["selection"]["patterns"]["mode"] == "full"  # semantics kept
    assert manifest["pruned"][-1]["freed_bytes"] == 100
    assert manifest["total_bytes"] > 0  # disk truth, not record arithmetic


def test_selective_delete_accepts_leftovers_and_does_not_touch_the_manifest(
    copy_root: Path,
):
    summary = delete_selected_files(
        "acme/tiny",
        copy_root,
        ["leftover.bin", ".cache"],
        storage_backend="filesystem",
    )

    assert summary["deleted"] == [".cache", "leftover.bin"]
    assert summary["freed_bytes"] == len(b"partial-download") + len(b"{}")
    manifest = read_manifest(copy_root)
    assert manifest["selection"]["file_count"] == 2  # tracked records untouched
    assert not (copy_root / ".cache").exists()


def test_selective_delete_rejects_unsafe_and_unknown_paths(copy_root: Path):
    with pytest.raises(DeletionError, match="stay inside"):
        delete_selected_files(
            "acme/tiny", copy_root, ["../escape.bin"], storage_backend="filesystem"
        )
    with pytest.raises(DeletionError, match="stay inside"):
        delete_selected_files(
            "acme/tiny", copy_root, [str(copy_root / "tokenizer.model")], storage_backend="filesystem"
        )
    with pytest.raises(DeletionError, match="instead"):
        delete_selected_files(
            "acme/tiny", copy_root, [MANIFEST_FILENAME], storage_backend="filesystem"
        )
    with pytest.raises(DeletionError, match="does not exist"):
        delete_selected_files(
            "acme/tiny", copy_root, ["ghost.bin"], storage_backend="filesystem"
        )
    with pytest.raises(DeletionError, match="No files were selected"):
        delete_selected_files("acme/tiny", copy_root, [" "], storage_backend="filesystem")


def test_selective_delete_refused_for_s3_backed_copies(copy_root: Path):
    with pytest.raises(DeletionError, match="durable S3 copy"):
        delete_selected_files(
            "acme/tiny", copy_root, ["model.safetensors"], storage_backend="s3"
        )


def test_selective_delete_requires_a_tracked_selection(tmp_path: Path):
    root = tmp_path / "plain"
    root.mkdir()
    (root / "a.bin").write_bytes(b"x")
    with pytest.raises(DeletionError, match="no tracked selection"):
        delete_selected_files("a/b", root, ["a.bin"], storage_backend="filesystem")


class _RecordingStorage:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    def delete_repository(self, repo_id: str) -> None:
        self.deleted.append(repo_id)


def test_whole_copy_delete_requires_exact_confirmation_and_removes_everything(
    storage_root: Path, copy_root: Path
):
    storage = _RecordingStorage()
    with pytest.raises(DeletionError, match="confirmation does not match"):
        delete_whole_copy(
            "acme/tiny",
            copy_root,
            "acme/other",
            model_storage=storage,
            storage_root=storage_root,
        )
    assert storage.deleted == []
    assert copy_root.exists()

    delete_whole_copy(
        "acme/tiny",
        copy_root,
        "acme/tiny",
        model_storage=storage,
        storage_root=storage_root,
    )
    assert storage.deleted == ["acme/tiny"]
    assert not copy_root.exists()


def test_whole_copy_delete_refuses_the_storage_root(storage_root: Path):
    storage = _RecordingStorage()
    with pytest.raises(DeletionError, match="storage root"):
        delete_whole_copy(
            "weird/repo",
            storage_root,
            "weird/repo",
            model_storage=storage,
            storage_root=storage_root,
        )
    assert storage.deleted == []


def test_database_delete_local_model_removes_the_record(tmp_path: Path):
    from app.config import Settings

    settings = Settings(
        model_storage=(tmp_path / "models").resolve(),
        data_dir=(tmp_path / "data").resolve(),
    )
    database = Database(settings.database_path)
    database.initialize()
    database.upsert_local_model(
        {
            "repo_id": "acme/tiny",
            "relative_path": "acme/tiny",
            "size_bytes": 10,
            "file_count": 1,
            "modified_at": "now",
            "downloaded_at": None,
            "revision": "main",
            "sha": "sha-old",
            "pipeline_tag": "text-generation",
            "library_name": "transformers",
            "license": "mit",
            "tags_json": "[]",
            "config_json": "{}",
            "source_url": None,
            "managed": True,
            "storage_backend": "filesystem",
            "cached": True,
            "remote_uri": None,
        }
    )

    assert database.delete_local_model("acme/tiny") is True
    assert database.get_local_model("acme/tiny") is None
    assert database.delete_local_model("acme/tiny") is False

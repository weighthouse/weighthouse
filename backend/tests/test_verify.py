"""Verification: the drift artifact a manifest walk produces."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.manifests import write_manifest
from app.verify import VerifyError, verify_copy


@pytest.fixture()
def tracked_copy(tmp_path: Path) -> Path:
    root = tmp_path / "acme" / "tiny"
    root.mkdir(parents=True)
    (root / "model.safetensors").write_bytes(b"w" * 1000)
    (root / "tokenizer.model").write_bytes(b"t" * 24)
    (root / "config.json").write_text("{}", encoding="utf-8")
    write_manifest(
        root,
        {
            "status": "complete",
            "schema_version": 2,
            "repo_id": "acme/tiny",
            "sha": "sha-x",
            "location": "primary",
            "selection": {
                "files": [
                    {"path": "model.safetensors", "size": 1000, "blob": "blob-w"},
                    {"path": "tokenizer.model", "size": 24, "blob": "blob-t"},
                ],
                "file_count": 2,
                "selected_bytes": 1024,
                "transfer_bytes": 0,
                "cached_bytes": 1024,
                "patterns": {"mode": "full", "allow": [], "ignore": []},
            },
        },
    )
    return root


def test_healthy_copy_reports_all_present_with_extras_listed(tracked_copy: Path):
    (tracked_copy / "stray-output.log").write_text("left behind", encoding="utf-8")
    report = verify_copy(tracked_copy)

    assert report["schema_version"] == 1
    assert report["drifted"] is False
    assert report["summary"]["present"] == 2
    assert report["summary"]["missing"] == 0
    assert report["summary"]["size-differs"] == 0
    assert {entry["path"] for entry in report["extras_beyond_manifest"]} == {
        "stray-output.log",
        "config.json",
    }
    assert report["repair"] is None


def test_missing_and_size_drifted_files_are_flagged_for_repair(tracked_copy: Path):
    (tracked_copy / "tokenizer.model").unlink()
    (tracked_copy / "model.safetensors").write_bytes(b"grown" * 300)  # 1500 bytes

    report = verify_copy(tracked_copy)

    assert report["drifted"] is True
    by_path = {row["path"]: row for row in report["files"]}
    assert by_path["tokenizer.model"]["status"] == "missing"
    assert by_path["model.safetensors"]["status"] == "size-differs"
    assert by_path["model.safetensors"]["actual_size"] == 1500
    assert report["repair"] and "updates flow" in report["repair"]
    # Missing rows sort before drifted rows.
    assert report["files"][0]["path"] == "tokenizer.model"


def test_cache_directory_is_not_reported_as_an_extra(tracked_copy: Path):
    cache = tracked_copy / ".cache" / "huggingface"
    cache.mkdir(parents=True)
    (cache / "metadata.json").write_text("{}", encoding="utf-8")

    report = verify_copy(tracked_copy)

    # config.json is legitimately untracked (in-not-in selection); the
    # cache directory contributes nothing.
    assert {entry["path"] for entry in report["extras_beyond_manifest"]} == {"config.json"}


def test_verify_requires_a_complete_tracked_copy(tmp_path: Path):
    nothing = tmp_path / "nothing"
    nothing.mkdir()
    with pytest.raises(VerifyError, match="no manifest"):
        verify_copy(nothing)

    root = tmp_path / "partial"
    root.mkdir()
    write_manifest(root, {"status": "downloading", "repo_id": "acme/tiny"})
    with pytest.raises(VerifyError, match="complete"):
        verify_copy(root)

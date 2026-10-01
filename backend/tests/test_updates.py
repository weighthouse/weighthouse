"""Tracked updates: diff artifact, apply validation, and manifest merge."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.manifests import MANIFEST_FILENAME, read_manifest, write_manifest
from app.updates import (
    STATUS_ADDED,
    STATUS_CHANGED,
    STATUS_MISSING,
    STATUS_REMOVED,
    STATUS_UNCHANGED,
    UPDATE_DIFF_VERSION,
    UpdateApplyError,
    UpdateDiffError,
    apply_paths,
    applyable_paths,
    build_update_diff,
    exact_patterns,
    merge_update_manifest,
)

from test_planning import FakeDryRunFile


def _manifest(**overrides):
    document = {
        "status": "complete",
        "schema_version": 2,
        "repo_id": "acme/tiny",
        "revision": "main",
        "sha": "sha-old",
        "license": "mit",
        "pipeline_tag": "text-generation",
        "source_url": "https://huggingface.co/acme/tiny",
        "selection": {
            "files": [
                {
                    "path": "model.safetensors",
                    "size": 110,
                    "is_cached": False,
                    "will_download": True,
                    "blob": "blob-w-old",
                },
                {
                    "path": "tokenizer.model",
                    "size": 20,
                    "is_cached": True,
                    "will_download": False,
                    "blob": "blob-t-old",
                },
            ],
            "file_count": 2,
            "selected_bytes": 130,
            "transfer_bytes": 110,
            "cached_bytes": 20,
            "patterns": {"mode": "full", "allow": [], "ignore": []},
        },
    }
    document.update(overrides)
    return document


def _upstream(files, sha="sha-new"):
    return {"sha": sha, "files": files}


def _statuses(diff):
    return {row["path"]: row["status"] for row in diff["files"]}


def test_diff_classifies_per_file_statuses_against_blob_ids():
    diff = build_update_diff(
        _manifest(),
        _upstream(
            [
                {"path": "model.safetensors", "size": 110, "blob_id": "blob-w-old"},
                {"path": "tokenizer.model", "size": 20, "blob_id": "3dff055a0e10"},
                {"path": "config.json", "size": 5, "blob_id": "blob-c-new"},
                {"path": "chat_template.jinja", "size": 7, "blob_id": "blob-j"},
                {"path": "extra.bin", "size": 999, "blob_id": "blob-x"},
            ]
        ),
    )

    statuses = _statuses(diff)
    assert statuses["model.safetensors"] == STATUS_UNCHANGED
    assert statuses["tokenizer.model"] == STATUS_CHANGED
    assert statuses["config.json"] == STATUS_ADDED  # not recorded locally yet
    assert statuses["chat_template.jinja"] == STATUS_ADDED
    assert statuses["extra.bin"] == STATUS_ADDED  # mode full covers additions

    assert diff["schema_version"] == UPDATE_DIFF_VERSION == 1
    assert diff["repo_id"] == "acme/tiny"
    assert diff["local"]["sha"] == "sha-old"
    assert diff["upstream"]["sha"] == "sha-new"
    assert diff["updates_available"] is True
    assert diff["summary"][STATUS_UNCHANGED] == 1
    assert diff["summary"][STATUS_CHANGED] == 1
    assert diff["summary"][STATUS_ADDED] == 3
    assert diff["results_in"]["transfer_bytes"] == 5 + 7 + 999 + 20


def test_diff_reports_upstream_removals_without_action():
    diff = build_update_diff(_manifest(), _upstream([{"path": "model.safetensors", "size": 110, "blob_id": "blob-w-old"}]))
    assert _statuses(diff)["tokenizer.model"] == STATUS_REMOVED
    assert diff["summary"][STATUS_REMOVED] == 1
    assert diff["results_in"]["transfer_bytes"] == 0


def test_diff_flags_downstream_missing_files_and_repairable_transfer():
    root = Path("/nonexistent-demo-root")
    diff = build_update_diff(
        _manifest(),
        _upstream([{"path": "model.safetensors", "size": 110, "blob_id": "blob-w-old"}]),
        root=root,
    )
    assert _statuses(diff)["model.safetensors"] == STATUS_MISSING
    assert diff["updates_available"] is True
    assert diff["results_in"]["missing"] == 1
    assert diff["results_in"]["transfer_bytes"] == 110


def test_diff_additions_obey_recorded_selection_patterns():
    manifest = _manifest()
    manifest["selection"]["patterns"] = {
        "mode": "safetensors",
        "allow": ["*.safetensors", "*.json"],
        "ignore": [],
    }
    diff = build_update_diff(
        manifest,
        _upstream(
            [
                {"path": "new-weights.safetensors", "size": 400, "blob_id": "blob-n"},
                {"path": "samples/data.bin", "size": 800, "blob_id": "blob-b"},
                {"path": "chat_template.jinja", "size": 7, "blob_id": "blob-j"},
            ]
        ),
    )
    statuses = _statuses(diff)
    assert statuses["new-weights.safetensors"] == STATUS_ADDED
    # .bin is not part of a safetensors-mode selection: additions exclude it.
    assert "samples/data.bin" not in statuses
    # metadata-style additions belong to a safetensors-mode copy
    assert statuses["chat_template.jinja"] == STATUS_ADDED


def test_diff_without_recorded_patterns_falls_back_to_metadata_only():
    manifest = _manifest()
    del manifest["selection"]["patterns"]
    diff = build_update_diff(
        manifest,
        _upstream(
            [
                {"path": "new-weights.safetensors", "size": 400, "blob_id": "blob-n"},
                {"path": "chat_template.jinja", "size": 7, "blob_id": "blob-j"},
            ]
        ),
    )
    statuses = _statuses(diff)
    assert "new-weights.safetensors" not in statuses  # no surprise weight fetches
    assert statuses["chat_template.jinja"] == STATUS_ADDED
    assert diff["added_reporting_limited"] is True


def test_diff_shows_nothing_to_update_when_identical():
    diff = build_update_diff(
        _manifest(),
        _upstream([{"path": "model.safetensors", "size": 110, "blob_id": "blob-w-old"}, {"path": "tokenizer.model", "size": 20, "blob_id": "blob-t-old"}]),
    )
    assert diff["updates_available"] is False
    assert diff["results_in"]["transfer_bytes"] == 0
    assert apply_paths(diff, None) == []


def test_diff_ignores_a_library_file_missing_on_disk_without_root():
    diff = build_update_diff(
        _manifest(),
        _upstream([{"path": "model.safetensors", "size": 110, "blob_id": "blob-w-old"}]),
    )
    assert _statuses(diff)["model.safetensors"] == STATUS_UNCHANGED


def test_diff_requires_a_complete_tracked_manifest():
    with pytest.raises(UpdateDiffError, match="no manifest"):
        build_update_diff({}, {"sha": "sha", "files": []})
    with pytest.raises(UpdateDiffError, match="predates tracked manifests"):
        build_update_diff({"schema_version": 1, "status": "complete"}, {"sha": "sha", "files": []})
    with pytest.raises(UpdateDiffError, match="not in a complete state"):
        build_update_diff(_manifest(status="cancelled"), {"sha": "sha", "files": []})


def test_apply_paths_validates_against_the_diff():
    diff = build_update_diff(
        _manifest(),
        _upstream(
            [
                {"path": "model.safetensors", "size": 110, "blob_id": "blob-w-new"},
                {"path": "chat_template.jinja", "size": 7, "blob_id": "blob-j"},
            ]
        ),
    )
    assert applyable_paths(diff) == ["chat_template.jinja", "model.safetensors"]
    assert apply_paths(diff, []) == ["chat_template.jinja", "model.safetensors"]
    assert apply_paths(diff, ["model.safetensors"]) == ["model.safetensors"]
    with pytest.raises(UpdateApplyError, match="not an updatable file"):
        apply_paths(diff, ["tokenizer.model"])  # removed upstream
    with pytest.raises(UpdateApplyError, match="named no updatable files"):
        apply_paths(diff, ["", " "])


def test_apply_paths_covers_missing_files_for_repair():
    diff = build_update_diff(
        _manifest(),
        _upstream([{"path": "model.safetensors", "size": 110, "blob_id": "blob-w-old"}]),
        root=Path("/nonexistent-demo-root"),
    )
    assert apply_paths(diff, ["model.safetensors"]) == ["model.safetensors"]


def test_exact_patterns_survive_glob_metacharacters():
    paths = ["dir/weird[1]/model.safetensors", "plain.json"]
    patterns = exact_patterns(paths)
    from app.planning import Selection, selection_covers

    for path, pattern in zip(paths, patterns):
        assert selection_covers(Selection(mode="custom", allow_patterns=[pattern]), path)
    assert not selection_covers(
        Selection(mode="custom", allow_patterns=[patterns[0]]), "dir/weirdX/model.safetensors"
    )


def test_merge_folds_the_update_into_the_existing_manifest():
    existing = _manifest()
    plan_payload = {
        "schema_version": 1,
        "requested_revision": "sha-new",
        "resolved_sha": "sha-new",
        "mode": "custom",
        "allow_patterns": ["config.json"],
        "ignore_patterns": [],
        "selection": {
            "files": [
                {
                    "path": "model.safetensors",
                    "size": 110,
                    "is_cached": False,
                    "will_download": True,
                    "blob": "blob-w-new",
                }
            ],
            "file_count": 1,
            "selected_bytes": 110,
            "transfer_bytes": 110,
            "cached_bytes": 0,
        },
        "planned_at": "2026-09-26T00:00:00+00:00",
    }
    document = merge_update_manifest(
        existing,
        plan_payload,
        writer_version="9.9.9-test",
        written_at="2026-09-26T01:00:00+00:00",
        disk_file_count=2,
        disk_total_bytes=125,
    )

    assert document["status"] == "complete"
    assert document["sha"] == "sha-new"
    assert document["revision"] == "sha-new"
    assert document["license"] == "mit"  # card metadata preserved
    assert document["pipeline_tag"] == "text-generation"
    assert document["file_count"] == 2
    assert document["total_bytes"] == 125
    assert document["update"]["mode"] == "tracked-update"
    assert document["update"]["from_sha"] == "sha-old"
    assert document["update"]["to_sha"] == "sha-new"
    assert document["update"]["transfer_bytes"] == 110

    files = {entry["path"]: entry for entry in document["selection"]["files"]}
    assert files["model.safetensors"]["blob"] == "blob-w-new"
    assert files["tokenizer.model"]["blob"] == "blob-t-old"  # untouched entry kept
    assert document["selection"]["patterns"]["mode"] == "full"
    assert document["selection"]["file_count"] == 2
    assert document["selection"]["selected_bytes"] == 130
    assert document["selection"]["transfer_bytes"] == 110
    assert document["selection"]["cached_bytes"] == 20


def test_merge_requires_a_tracked_selection_and_a_nonempty_plan():
    with pytest.raises(UpdateDiffError, match="tracked selection"):
        merge_update_manifest(
            {"status": "complete", "selection": {}},
            {"selection": {"files": [{"path": "x", "size": 1}]}},
            writer_version="v",
            written_at="t",
            disk_file_count=1,
            disk_total_bytes=1,
        )
    with pytest.raises(UpdateDiffError, match="selected no files"):
        merge_update_manifest(
            _manifest(),
            {"selection": {"files": [], "transfer_bytes": 0, "requested_revision": "sha", "resolved_sha": "sha"}},
            writer_version="v",
            written_at="t",
            disk_file_count=1,
            disk_total_bytes=1,
        )


def _update_manager(tmp_path, files, sha="sha-new"):
    from app.config import Settings
    from app.database import Database
    from app.downloads import DownloadManager
    from app.indexer import LocalModelIndexer
    from app.storage import FilesystemModelStorage

    class Hub:
        settings = Settings(
            model_storage=(tmp_path / "models").resolve(),
            data_dir=(tmp_path / "data").resolve(),
        )

        @staticmethod
        def model_details(repo_id: str, revision: str):
            assert revision == sha
            return {
                "sha": sha,
                "pipeline_tag": "text-generation",
                "library_name": "transformers",
                "license": "mit",
                "files": files,
                "gated": False,
                "source_url": "https://huggingface.co/acme/tiny",
            }

    settings = Hub.settings
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.initialize()
    manager = DownloadManager(
        settings,
        database,
        Hub(),
        LocalModelIndexer(settings, database),
        FilesystemModelStorage(settings),
    )
    manager._retry_delay = lambda attempt: 0  # type: ignore[method-assign]

    import time as time_module

    def await_terminal(download_id: str, timeout: float = 2.0):
        deadline = time_module.monotonic() + timeout
        while time_module.monotonic() < deadline:
            row = database.get_download(download_id)
            if row and row["status"] in {"complete", "failed", "cancelled"}:
                return row
            time_module.sleep(0.01)
        raise AssertionError("download did not reach a terminal status")

    def stub_dry_run(paths):
        manager._dry_run = lambda *args, **kwargs: [  # type: ignore[method-assign]
            FakeDryRunFile(path, 110 if path.endswith("safetensors") else 10, sha, is_cached=False)
            for path in paths
        ]

    return manager, await_terminal, settings.model_storage / "acme" / "tiny", stub_dry_run


def test_update_download_merges_into_the_existing_library_manifest(tmp_path):
    manager, await_terminal, target, stub_dry_run = _update_manager(
        tmp_path,
        [
            {"path": "model.safetensors", "size": 110, "blob_id": "blob-w-new"},
            {"path": "tokenizer.model", "size": 20, "blob_id": "blob-t-old"},
        ],
    )
    stub_dry_run(["model.safetensors"])
    target.mkdir(parents=True)
    write_manifest(target, _manifest())
    (target / "tokenizer.model").write_bytes(b"tokenizer")

    def snapshot(download_id, download, target, cancel_event):
        (target / "model.safetensors").write_bytes(b"new weights")

    manager._download_snapshot = snapshot  # type: ignore[method-assign]

    job = manager.queue(
        "acme/tiny",
        "sha-new",
        "custom",
        custom_include=["model.safetensors"],
        update_merge=True,
    )
    finished = await_terminal(job["id"])

    assert finished["status"] == "complete"
    manifest = read_manifest(target)
    assert manifest["status"] == "complete"
    assert manifest["sha"] == "sha-new"
    assert manifest["revision"] == "sha-new"
    assert manifest["license"] == "mit"
    files = {entry["path"]: entry["blob"] for entry in manifest["selection"]["files"]}
    assert files["model.safetensors"] == "blob-w-new"
    assert files["tokenizer.model"] == "blob-t-old"
    assert manifest["selection"]["transfer_bytes"] == 110
    assert manifest["selection"]["file_count"] == 2
    assert manifest["selection"]["patterns"]["mode"] == "full"
    assert manifest["update"]["from_sha"] == "sha-old"
    assert manifest["update"]["to_sha"] == "sha-new"


def test_failed_update_download_leaves_the_library_manifest_intact(tmp_path):
    manager, await_terminal, target, stub_dry_run = _update_manager(
        tmp_path,
        [{"path": "model.safetensors", "size": 110, "blob_id": "blob-w-new"}],
    )
    stub_dry_run(["model.safetensors"])
    target.mkdir(parents=True)
    seeded = _manifest()
    write_manifest(target, seeded)

    def boom(download_id, download, target, cancel_event):
        raise RuntimeError("Connection error: unreachable")

    manager._download_snapshot = boom  # type: ignore[method-assign]

    job = manager.queue(
        "acme/tiny",
        "sha-new",
        "custom",
        custom_include=["model.safetensors"],
        update_merge=True,
    )
    finished = await_terminal(job["id"])

    assert finished["status"] == "failed"
    # The failed update must not have touched the copy's manifest at all.
    assert json.loads((target / MANIFEST_FILENAME).read_text(encoding="utf-8")) == seeded


def test_cancel_preserves_a_complete_manifest(tmp_path):
    from app.downloads import DownloadManager

    empty = tmp_path / "empty"
    empty.mkdir()
    complete = tmp_path / "complete"
    complete.mkdir()
    write_manifest(complete, {"status": "complete", "sha": "x"})
    partial = tmp_path / "partial"
    partial.mkdir()
    write_manifest(partial, {"status": "downloading", "sha": "x"})

    assert DownloadManager._should_write_cancelled_manifest(partial) is True
    assert DownloadManager._should_write_cancelled_manifest(empty) is True
    assert DownloadManager._should_write_cancelled_manifest(complete) is False

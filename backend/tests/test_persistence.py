"""Persistence-contract attestation (Pillar 7).

The library — model files plus their manifests — is the durable truth; the
database and the application are disposable. These tests pin that contract:

* Nuking the database and rescanning restores an equivalent index.
* Manifest schemas have versioned literal fixtures; the reader keeps
  accepting the legacy upstream shape (decision D9) and the current v2
  shape must never regress.
* A frozen plan stored in a job payload (a literal fixture) still
  rebuilds into the planner's object model.

The fixtures below are written on disk by hand (not through the writing
helpers) so they represent manifests as a previous writer would leave
them. Future schema changes must extend these fixtures deliberately
rather than silently drifting.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.brand import BRAND
from app.config import Settings
from app.database import Database
from app.indexer import LocalModelIndexer
from app.manifests import (
    LEGACY_MANIFEST_FILENAME,
    MANIFEST_FILENAME,
)
from app.planning import PLANNER_VERSION, plan_from_payload


MANIFEST_V1_LEGACY = {
    "status": "complete",
    "repo_id": "legacy-org/legacy-model",
    "revision": "main",
    "sha": "0000000000000000000000000000000000000000",
    "downloaded_at": "2026-05-01T12:00:00+00:00",
    "source_url": "https://huggingface.co/legacy-org/legacy-model",
    "pipeline_tag": "text-generation",
    "library_name": "transformers",
    "license": "apache-2.0",
    "tags": ["base"],
    "gated": False,
    "total_bytes": 128,
    "file_count": 3,
}

MANIFEST_V2_CURRENT = {
    "schema_version": 2,
    "status": "complete",
    "repo_id": "acme/planned-model",
    "revision": "main",
    "sha": "1111111111111111111111111111111111111111",
    "downloaded_at": "2026-09-26T12:00:00+00:00",
    "source_url": "https://huggingface.co/acme/planned-model",
    "pipeline_tag": "text-generation",
    "library_name": "transformers",
    "license": "mit",
    "tags": ["planned"],
    "gated": False,
    "total_bytes": 256,
    "file_count": 3,
    "writer": {
        "app_version": "1.2.0",
        "schema_version": 2,
        "written_at": "2026-09-26T12:00:00+00:00",
    },
    "requested_revision": "main",
    "selection": {
        "files": [{"path": "model.safetensors", "size": 200, "is_cached": False, "will_download": True, "blob": "lfs-sha-1"}],
        "file_count": 1,
        "selected_bytes": 200,
        "transfer_bytes": 200,
        "cached_bytes": 0,
    },
}

FROZEN_PLAN_PAYLOAD_V1 = {
    "schema_version": 1,
    "requested_revision": "main",
    "resolved_sha": "1111111111111111111111111111111111111111",
    "resolved_at": "2026-09-26T10:00:00+00:00",
    "mode": "safetensors",
    "allow_patterns": ["*.safetensors", "*.json", "LICENSE*", "tokenizer*"],
    "ignore_patterns": [],
    "metadata": {
        "pipeline_tag": "text-generation",
        "library_name": "transformers",
        "license": "mit",
        "gated": False,
        "source_url": "https://huggingface.co/acme/planned-model",
        "card_file_count": 2,
    },
    "selection": {
        "files": [
            {"path": "config.json", "size": 10, "is_cached": True, "will_download": False, "blob": "blob-json"},
            {"path": "model.safetensors", "size": 200, "is_cached": False, "will_download": True, "blob": "blob-safetensors"},
        ],
        "file_count": 2,
        "selected_bytes": 210,
        "transfer_bytes": 200,
        "cached_bytes": 10,
    },
    "planned_at": "2026-09-26T10:00:01+00:00",
}


def _settings(tmp_path: Path) -> Settings:
    settings = Settings(
        model_storage=(tmp_path / "models").resolve(),
        data_dir=(tmp_path / "data").resolve(),
    )
    settings.ensure_directories()
    return settings


def _write_plain_manifest(directory: Path, filename: str, payload: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / filename).write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _seed_library(storage: Path) -> None:
    planned = storage / "acme" / "planned-model"
    _write_plain_manifest(planned, MANIFEST_FILENAME, MANIFEST_V2_CURRENT)
    (planned / "config.json").write_text("{}", encoding="utf-8")
    (planned / "model.safetensors").write_bytes(b"weights-256-bytes-padded-payload" * 8)

    legacy = storage / "legacy-org" / "legacy-model"
    _write_plain_manifest(legacy, LEGACY_MANIFEST_FILENAME, MANIFEST_V1_LEGACY)
    (legacy / "config.json").write_text("{}", encoding="utf-8")
    (legacy / "pytorch_model.bin").write_bytes(b"legacy" * 21)

    unmanaged = storage / "acme" / "hand-copied"
    unmanaged.mkdir(parents=True, exist_ok=True)
    (unmanaged / "config.json").write_text("{}", encoding="utf-8")


def _index_map(settings: Settings) -> dict[str, dict]:
    database = Database(settings.database_path)
    database.initialize()
    result = LocalModelIndexer(settings, database).scan()
    return {model["repo_id"]: model for model in result["models"]}


def test_database_nuke_rescan_restores_equivalent_index(tmp_path):
    settings = _settings(tmp_path)
    _seed_library(settings.model_storage)

    before = _index_map(settings)

    settings.database_path.unlink()  # nuke: the database is disposable

    after = _index_map(settings)

    assert set(before) == set(after) == {
        "acme/planned-model",
        "legacy-org/legacy-model",
        "acme/hand-copied",
    }
    for repo_id, record in before.items():
        assert after[repo_id] == record, f"record for {repo_id} differs after rescan"


def test_current_manifest_fixture_is_indexed_with_provenance(tmp_path):
    settings = _settings(tmp_path)
    _seed_library(settings.model_storage)

    records = _index_map(settings)
    record = records["acme/planned-model"]

    assert record["managed"] is True
    assert record["sha"] == "1111111111111111111111111111111111111111"
    assert record["revision"] == "main"
    assert record["license"] == "mit"


def test_legacy_manifest_fixture_is_indexed(tmp_path):
    settings = _settings(tmp_path)
    _seed_library(settings.model_storage)

    records = _index_map(settings)
    record = records["legacy-org/legacy-model"]

    # D9: upstream-written directories keep working, provenance intact.
    assert record["managed"] is True
    assert record["sha"] == "0000000000000000000000000000000000000000"
    assert record["downloaded_at"] == "2026-05-01T12:00:00+00:00"


def test_hand_copied_directory_indexed_unmanaged(tmp_path):
    settings = _settings(tmp_path)
    _seed_library(settings.model_storage)

    records = _index_map(settings)

    assert records["acme/hand-copied"]["managed"] is False


def test_frozen_plan_literal_payload_round_trips():
    plan = plan_from_payload(json.loads(json.dumps(FROZEN_PLAN_PAYLOAD_V1)))

    assert plan.sha == FROZEN_PLAN_PAYLOAD_V1["resolved_sha"]
    assert plan.requested_revision == "main"
    assert plan.selected_bytes == 210
    assert plan.transfer_bytes == 200
    assert plan.cached_bytes == 10
    assert [item["path"] for item in plan.files] == [
        FROZEN_PLAN_PAYLOAD_V1["selection"]["files"][0]["path"],
        FROZEN_PLAN_PAYLOAD_V1["selection"]["files"][1]["path"],
    ]


@pytest.mark.parametrize(
    ("fixture", "filename"),
    [
        (MANIFEST_V2_CURRENT, MANIFEST_FILENAME),
        (MANIFEST_V1_LEGACY, LEGACY_MANIFEST_FILENAME),
    ],
    ids=["v2", "v1-legacy"],
)
def test_manifest_reader_accepts_versioned_fixture_shapes(
    tmp_path, fixture: dict, filename: str
):
    from app import manifests as manifests_module

    directory = tmp_path / "repo"
    _write_plain_manifest(directory, filename, fixture)

    payload = manifests_module.read_manifest(directory)
    assert payload == fixture


def test_planner_run_writes_versioned_manifest_with_selection(tmp_path):
    from test_planning import _retry_manager

    manager, database, await_terminal = _retry_manager(tmp_path)

    def snapshot(download_id, download, target, cancel_event):
        (target / "model.safetensors").write_bytes(b"weights")

    manager._download_snapshot = snapshot  # type: ignore[method-assign]

    job = manager.queue("acme/tiny", "main", "safetensors")
    finished = await_terminal(job["id"])

    assert finished["status"] == "complete"
    manifest = json.loads(
        (manager.settings.model_storage / "acme" / "tiny" / MANIFEST_FILENAME).read_text(
            encoding="utf-8"
        )
    )
    assert manifest["schema_version"] == 2
    assert manifest["sha"] == "sha-abc"
    assert manifest["requested_revision"] == "main"
    assert manifest["writer"]["schema_version"] == 2
    assert manifest["writer"]["app_version"] == manager.settings.app_version
    assert manifest["writer"]["written_at"]
    assert manifest["selection"]["file_count"] == 1
    assert manifest["selection"]["transfer_bytes"] == 110
    assert manifest["status"] == "complete"


def test_interrupted_manifest_write_preserves_previous_content(tmp_path, monkeypatch):
    from app import manifests as manifests_module

    directory = tmp_path / "acme" / "tiny"
    manifests_module.write_manifest(directory, {"status": "downloading", "repo_id": "acme/tiny"})
    original = manifests_module.read_manifest(directory)
    assert original["status"] == "downloading"

    def explode(*args, **kwargs):
        raise OSError("simulated disk full")

    monkeypatch.setattr(manifests_module.json, "dump", explode)

    with pytest.raises(OSError):
        manifests_module.write_manifest(directory, {"status": "complete"})

    assert manifests_module.read_manifest(directory) == original
    assert not listed_staged_files(directory)


def listed_staged_files(directory: Path) -> list[str]:
    return [path.name for path in directory.glob("*.staged")]


def test_download_max_attempts_knob_is_respected(tmp_path):
    from dataclasses import replace

    from test_planning import _retry_manager

    manager, database, await_terminal = _retry_manager(tmp_path)
    manager.settings = replace(manager.settings, download_max_attempts=2)
    calls = {"count": 0}

    def always_fails(download_id, download, target, cancel_event):
        calls["count"] += 1
        raise RuntimeError("connection error while fetching blob")

    manager._download_snapshot = always_fails  # type: ignore[method-assign]

    job = manager.queue("acme/tiny", "main", "full")
    finished = await_terminal(job["id"])

    assert finished["status"] == "failed"
    assert calls["count"] == 2
    assert finished["error"].startswith("failed after 2 attempts")

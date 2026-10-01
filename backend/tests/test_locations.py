"""Storage-location parsing and the local_models location column."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.config import Settings
from app.database import Database
from app.locations import (
    DEFAULT_LOCATION_NAME,
    ENV_NAME,
    LocationError,
    find_location,
    parse_locations,
)


def _parse(raw, default_root):
    return parse_locations(raw, default_root=default_root)


def test_default_root_is_always_the_implicit_primary(tmp_path: Path):
    locations, primary = _parse(None, tmp_path / "models")

    assert primary == DEFAULT_LOCATION_NAME == "primary"
    assert [loc.name for loc in locations] == ["primary"]
    assert locations[0].root == (tmp_path / "models").resolve()


def test_declared_locations_join_the_primary(tmp_path: Path):
    default_root = tmp_path / "models"
    raw = json.dumps(
        [
            {"name": "Nas-Archive", "root": str(tmp_path / "nas")},
            {"name": "nvme-rig", "root": str(tmp_path / "nvme")},
        ]
    )

    locations, primary = _parse(raw, default_root)

    names = [loc.name for loc in locations]
    assert names[0] == "primary"
    assert set(names[1:]) == {"nas-archive", "nvme-rig"}
    by_name = {loc.name: loc.root for loc in locations}
    assert by_name["nvme-rig"] == (tmp_path / "nvme").resolve()
    assert all(loc.root.is_absolute() for loc in locations)


def test_user_home_expansion_is_resolved(tmp_path: Path):
    raw = json.dumps([{"name": "home", "root": "~/models-from-home"}])
    locations, _ = _parse(raw, tmp_path / "models")
    assert str(locations[-1].root).startswith(str(Path.home())[: len(str(Path.home()))])
    assert locations[-1].root.is_absolute()


@pytest.mark.parametrize(
    "raw",
    [
        "{not json",
        '"just a string"',
        '{"name": "x"}',
        '[{"name": "okloc", "root": "/tmp/x"}, {"name": "okloc", "root": "/tmp/y"}]',
        '[{"name": "primary", "root": "/tmp/other"}]',
        '[{"name": "a_loc", "root": "/tmp/a"}, {"name": "b_loc", "root": "/tmp/a"}]',
        '[{"name": "no root"}]',
        '[{"name": "", "root": "/tmp/x"}]',
        '[{"name": "has space", "root": "/tmp/x"}]',
        '[{"name": "okloc", "root": "relative/path"}]',
        json.dumps([{"name": f"loc{i:02d}", "root": f"/tmp/l{i:02d}"} for i in range(17)]),
    ],
)
def test_invalid_configs_fail_with_precise_errors(raw, tmp_path: Path):
    with pytest.raises(LocationError):
        _parse(raw, tmp_path / "models")


def test_declaring_the_default_root_is_an_error_not_a_rename(tmp_path: Path):
    raw = json.dumps([{"name": "same", "root": str(tmp_path / "models")}])
    with pytest.raises(LocationError, match="already registered as 'primary'"):
        _parse(raw, tmp_path / "models")


def test_oversized_json_is_rejected(tmp_path: Path):
    big = "[" + " " * 100_001 + "]"
    with pytest.raises(LocationError, match="too large"):
        _parse(big, tmp_path / "models")


def test_find_location_is_case_insensitive(tmp_path: Path):
    locations, _ = _parse(
        json.dumps([{"name": "nvme-rig", "root": str(tmp_path / "nvme")}]),
        tmp_path / "models",
    )
    assert find_location(locations, "NVME-RIG") is not None
    assert find_location(locations, "nope") is None
    assert find_location(locations, None) is None


def test_local_models_gain_a_location_column(tmp_path: Path):
    settings = Settings(
        model_storage=(tmp_path / "models").resolve(),
        data_dir=(tmp_path / "data").resolve(),
    )
    database = Database(settings.database_path)
    database.initialize()

    legacy = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(legacy) as connection:
        connection.execute(
            """
            CREATE TABLE local_models (
                repo_id TEXT PRIMARY KEY,
                relative_path TEXT NOT NULL UNIQUE,
                size_bytes INTEGER NOT NULL DEFAULT 0,
                file_count INTEGER NOT NULL DEFAULT 0,
                modified_at TEXT NOT NULL,
                downloaded_at TEXT,
                revision TEXT,
                sha TEXT,
                pipeline_tag TEXT,
                library_name TEXT,
                license TEXT,
                tags_json TEXT NOT NULL DEFAULT '[]',
                config_json TEXT NOT NULL DEFAULT '{}',
                source_url TEXT,
                managed INTEGER NOT NULL DEFAULT 0,
                storage_backend TEXT NOT NULL DEFAULT 'filesystem',
                cached INTEGER NOT NULL DEFAULT 1,
                remote_uri TEXT
            )
            """
        )
        connection.execute(
            """
            INSERT INTO local_models (
                repo_id, relative_path, modified_at
            ) VALUES ('acme/old', 'acme/old', 'now')
            """
        )

    legacy_database = Database(legacy)
    legacy_database.initialize()
    model = legacy_database.get_local_model("acme/old")
    assert model is not None
    assert model["location"] == "primary"  # pre-location rows live on MODEL_STORAGE

    # Records without a location key default to primary on upsert.
    database.upsert_local_model(
        {
            "repo_id": "acme/new",
            "relative_path": "acme/new",
            "size_bytes": 5,
            "file_count": 1,
            "modified_at": "now",
            "downloaded_at": None,
            "revision": "main",
            "sha": "sha-x",
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
    stored = database.get_local_model("acme/new")
    assert stored is not None
    assert stored["location"] == "primary"

    with database.connect() as connection:
        connection.execute(
            "UPDATE local_models SET location = 'nas-archive' WHERE repo_id = 'acme/new'"
        )
    assert database.get_local_model("acme/new")["location"] == "nas-archive"


def _write_model_copy(root: Path, repo_relative: str, files: dict[str, bytes]) -> Path:
    directory = root / repo_relative
    directory.mkdir(parents=True)
    for name, payload in files.items():
        (directory / name).write_bytes(payload)
    return directory


def _indexer_with_locations(tmp_path: Path, declared: list[dict[str, str]]):
    from app.indexer import LocalModelIndexer
    from app.locations import parse_locations

    primary = tmp_path / "primary"
    primary.mkdir(exist_ok=True)
    locations, _ = parse_locations(
        json.dumps(declared), default_root=primary
    )
    settings = Settings(
        model_storage=primary.resolve(),
        data_dir=(tmp_path / "data").resolve(),
    )
    database = Database(settings.database_path)
    database.initialize()
    indexer = LocalModelIndexer(settings, database)
    indexer.set_locations(locations)
    return indexer, database, locations


def test_scan_indexes_copies_across_declared_locations(tmp_path: Path):
    import json as json_module

    other = tmp_path / "nas"
    other.mkdir()
    _write_model_copy(tmp_path / "primary", "acme/home", {"config.json": b"{}"})
    _write_model_copy(other, "acme/archived", {"config.json": b"{}"})
    indexer, database, _ = _indexer_with_locations(
        tmp_path,
        [{"name": "nas-archive", "root": str(other)}],
    )
    json_module  # imported for config parity with other modules

    result = indexer.scan()

    assert result["count"] == 2
    by_repo = database.list_local_models()
    locations = {model["relative_path"]: model["location"] for model in by_repo}
    assert locations["acme/home"] == "primary"
    assert locations["acme/archived"] == "nas-archive"


def test_files_for_model_resolves_through_the_record_location(tmp_path: Path):
    other = tmp_path / "nas"
    other.mkdir()
    _write_model_copy(tmp_path / "primary", "acme/home", {"config.json": b"{}"})
    _write_model_copy(other, "acme/archived", {"model.safetensors": b"w" * 40})
    indexer, _, _ = _indexer_with_locations(
        tmp_path,
        [{"name": "nas-archive", "root": str(other)}],
    )
    indexer.scan()

    details = indexer.files_for_model("acme/archived")
    assert details is not None
    assert {entry["path"] for entry in details["files"]} == {"model.safetensors"}


def test_index_path_refuses_paths_outside_every_declared_location(tmp_path: Path):
    stranger = tmp_path / "undeclared"
    stranger.mkdir()
    _write_model_copy(stranger, "acme/py", {"config.json": b"{}"})
    indexer, _, _ = _indexer_with_locations(
        tmp_path,
        [{"name": "nas-archive", "root": str(tmp_path / "nas")}],
    )

    with pytest.raises(ValueError, match="escapes every configured"):
        indexer.index_path(stranger / "acme" / "py")


def test_same_repo_at_two_locations_resolves_deterministically(tmp_path: Path):
    other = tmp_path / "nas"
    other.mkdir()
    _write_model_copy(tmp_path / "primary", "acme/dual", {"config.json": b"{}"})
    _write_model_copy(other, "acme/dual", {"config.json": b"{}"})
    indexer, _, _ = _indexer_with_locations(
        tmp_path,
        [{"name": "nas-archive", "root": str(other)}],
    )

    result = indexer.scan()

    assert result["count"] == 1
    model = indexer.database.get_local_model("acme/dual")
    assert model["location"] == "primary"

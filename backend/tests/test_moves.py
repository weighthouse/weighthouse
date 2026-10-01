"""Storage moves: proposal artifact, copy-verify-swap, manifest travel."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config import Settings
from app.database import Database
from app.indexer import LocalModelIndexer
from app.locations import parse_locations
from app.manifests import read_manifest, write_manifest
from app.moves import MoveError, apply_move, build_move_plan


@pytest.fixture()
def world(tmp_path: Path):
    primary = tmp_path / "primary"
    nas = tmp_path / "nas"
    primary.mkdir()
    nas.mkdir()
    copy = primary / "acme" / "tiny"
    copy.mkdir(parents=True)
    (copy / "model.safetensors").write_bytes(b"w" * 1000)
    (copy / "config.json").write_text("{}", encoding="utf-8")
    write_manifest(
        copy,
        {
            "status": "complete",
            "schema_version": 2,
            "repo_id": "acme/tiny",
            "revision": "main",
            "sha": "sha-x",
            "selection": {
                "files": [{"path": "model.safetensors", "size": 1000, "blob": "blob-w"}],
                "file_count": 1,
                "selected_bytes": 1000,
                "transfer_bytes": 0,
                "cached_bytes": 1000,
                "patterns": {"mode": "full", "allow": [], "ignore": []},
            },
        },
    )
    locations, _ = parse_locations(
        json.dumps([{"name": "nas-archive", "root": str(nas)}]),
        default_root=primary,
    )
    settings = Settings(
        model_storage=primary.resolve(), data_dir=(tmp_path / "data").resolve()
    )
    database = Database(settings.database_path)
    database.initialize()
    indexer = LocalModelIndexer(settings, database)
    indexer.set_locations(locations)
    source = locations[0]
    destination = locations[1]
    return {
        "primary": primary,
        "nas": nas,
        "copy": copy,
        "source": source,
        "destination": destination,
        "indexer": indexer,
        "database": database,
    }


def test_plan_describes_the_move_without_touching_anything(world):
    plan = build_move_plan(
        "acme/tiny", world["source"], world["destination"], "acme/tiny"
    )

    assert plan["schema_version"] == 1
    assert plan["from"]["name"] == "primary"
    assert plan["to"]["name"] == "nas-archive"
    # Weights plus config plus the traveling manifest.
    assert plan["size_bytes"] > 1000
    assert plan["file_count"] == 3
    assert plan["strategy"] in ("rename", "copy-verify-swap")
    assert world["copy"].exists()  # nothing moved yet
    assert not (world["nas"] / "acme" / "tiny").exists()


def test_plan_rejects_same_location_incomplete_and_existing_destination(world):
    with pytest.raises(MoveError, match="the same"):
        build_move_plan(
            "acme/tiny", world["source"], world["source"], "acme/tiny"
        )

    incomplete = world["copy"]
    manifest = read_manifest(incomplete)
    manifest["status"] = "downloading"
    write_manifest(incomplete, manifest)
    with pytest.raises(MoveError, match="complete"):
        build_move_plan(
            "acme/tiny", world["source"], world["destination"], "acme/tiny"
        )

    manifest["status"] = "complete"
    write_manifest(incomplete, manifest)
    (world["nas"] / "acme" / "tiny").mkdir(parents=True)
    with pytest.raises(MoveError, match="already contains"):
        build_move_plan(
            "acme/tiny", world["source"], world["destination"], "acme/tiny"
        )


def test_plan_respects_declared_free_space(world):
    with pytest.raises(MoveError, match="Not enough free space"):
        build_move_plan(
            "acme/tiny",
            world["source"],
            world["destination"],
            "acme/tiny",
            destination_free_bytes=10,
        )


def test_move_carries_the_manifest_and_repoints_the_record(world):
    plan = build_move_plan(
        "acme/tiny", world["source"], world["destination"], "acme/tiny"
    )

    result = apply_move(plan, world["source"], world["destination"], indexer=world["indexer"])

    destination_tree = world["nas"] / "acme" / "tiny"
    assert result["moved_to"] == "nas-archive"
    assert (destination_tree / "model.safetensors").read_bytes() == b"w" * 1000
    assert not world["copy"].exists()  # source removed last

    manifest = read_manifest(destination_tree)
    assert manifest["location"] == "nas-archive"
    assert manifest["moved"][-1]["from"] == "primary"
    assert manifest["moved"][-1]["to"] == "nas-archive"
    assert manifest["sha"] == "sha-x"  # library record traveled intact

    record = world["database"].get_local_model("acme/tiny")
    assert record is not None
    assert record["location"] == "nas-archive"


def test_failed_verification_leaves_source_untouched(world, monkeypatch):
    calls = {"count": 0}
    original_stats = __import__("app.moves", fromlist=["directory_stats"]).directory_stats

    def lying_stats(path, include_cache=False):
        calls["count"] += 1
        if calls["count"] == 2:  # the staged verification pass
            return (1, 999, 0.0)
        return original_stats(path, include_cache=include_cache)

    import app.moves as moves_module

    monkeypatch.setattr(moves_module, "directory_stats", lying_stats)

    plan = build_move_plan(
        "acme/tiny", world["source"], world["destination"], "acme/tiny"
    )
    # This host's temp directories share a filesystem, so the honest
    # plan is a rename; force the copy strategy to exercise verification.
    plan["strategy"] = "copy-verify-swap"

    with pytest.raises(MoveError, match="Verification failed"):
        apply_move(plan, world["source"], world["destination"], indexer=world["indexer"])

    assert world["copy"].exists()
    assert (world["copy"] / "model.safetensors").read_bytes() == b"w" * 1000
    leftovers = [
        entry for entry in world["nas"].iterdir() if entry.name.startswith(".")
    ]
    assert leftovers == []  # staging cleaned


def test_move_refuses_an_unsupported_plan(world):
    plan = build_move_plan(
        "acme/tiny", world["source"], world["destination"], "acme/tiny"
    )
    plan["schema_version"] = 99
    with pytest.raises(MoveError, match="schema_version"):
        apply_move(plan, world["source"], world["destination"], indexer=world["indexer"])

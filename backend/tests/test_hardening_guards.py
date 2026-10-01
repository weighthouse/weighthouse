"""Hardening guards from the security audit (D8, increment B4).

Three small walls get pinned here: an unknown username costs the same
scrypt work as a wrong password, discovery ignores trees whose manifest
names an unmanageable repository id, and a resolved storage record may
never point outside its declared location root.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest


def test_unknown_usernames_pay_the_scrypt_verify_work(monkeypatch, tmp_path: Path):
    import app.auth as auth_module


    real_scrypt = hashlib.scrypt
    calls: list[int] = []

    def counting(*args, **kwargs):
        calls.append(1)
        return real_scrypt(*args, **kwargs)

    monkeypatch.setattr(auth_module.hashlib, "scrypt", counting)

    import app.config as config
    from app.auth import AuthService
    from app.database import Database

    database = Database(tmp_path / "auth.sqlite3")
    database.initialize()
    service = AuthService(config.settings, database)

    assert service.authenticate("nobody-here", "wrong-password", "tester:1") is None
    assert calls, "an unknown username must still run one scrypt verification"

    count_after_unknown = len(calls)
    service.authenticate("nobody-here", "wrong-password", "tester:2")
    assert len(calls) >= count_after_unknown + 1


def test_discovery_skips_manifests_with_unmanageable_ids(tmp_path: Path):
    import dataclasses
    import json

    import app.config as config
    from app.database import Database
    from app.indexer import LocalModelIndexer

    storage = tmp_path / "models"
    crafted = storage / "escape" / "evil"
    crafted.mkdir(parents=True)
    (crafted / "weights.bin").write_bytes(b"w")
    manifest = crafted / ".modelmanifest.json"
    manifest.write_text(
        json.dumps({"repo_id": "../escape", "status": "complete"}),
        encoding="utf-8",
    )

    settings = dataclasses.replace(config.settings, data_dir=tmp_path, model_storage=storage)
    database = Database(tmp_path / "library.sqlite3")
    database.initialize()
    indexer = LocalModelIndexer(settings, database)

    result = indexer.scan()
    assert result["count"] == 0
    assert database.get_local_model("../escape") is None


def test_a_storage_record_may_not_escape_its_location(monkeypatch, tmp_path: Path):
    import app.main as main_module
    from app.locations import parse_locations

    primary = tmp_path / "primary"
    primary.mkdir()
    # The default root auto-claims the name "primary"; no declaration needed.
    locations, _ = parse_locations(None, default_root=primary)
    monkeypatch.setattr(main_module, "locations", locations)

    within = main_module.model_root(
        {"location": "primary", "relative_path": "acme/tiny"}
    )
    assert within.is_absolute() and primary in within.parents

    with pytest.raises(ValueError, match="outside its declared location"):
        main_module.model_root(
            {"location": "primary", "relative_path": "../outside/evil"}
        )


def test_the_mcp_mirror_refuses_the_same_escape(monkeypatch, tmp_path: Path):
    import app.mcp_server as mcp_module
    from app.locations import parse_locations

    primary = tmp_path / "primary"
    primary.mkdir()
    locations, _ = parse_locations(None, default_root=primary)
    services = type(
        "S",
        (),
        {"locations": locations, "settings": type("T", (), {"model_storage": primary})()},
    )()
    with pytest.raises(ValueError, match="outside its declared location"):
        mcp_module._model_root(services, {"location": "primary", "relative_path": "../../etc"})

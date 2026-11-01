"""Model card snapshots: the stored card is keyed by the copy's locked
sha; nothing refetches while the copy hasn't moved, and an upstream
failure leaves the card absent rather than stale.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException

from app.database import Database


@pytest.fixture()
def database(tmp_path: Path) -> Database:
    database = Database(tmp_path / "library.sqlite3")
    database.initialize()
    return database


def _snapshot(repo_id: str, sha: str, fetched_at: str) -> dict[str, Any]:
    return {
        "repo_id": repo_id,
        "sha": sha,
        "card_text": f"card for {sha}",
        "fetched_at": fetched_at,
    }


def test_a_stored_snapshot_round_trips_by_sha(database: Database):
    database.store_card_snapshot(_snapshot("acme/rocket", "aaa111", "2026-09-27T00:00:00+00:00"))
    snap = database.get_card_snapshot("acme/rocket", "aaa111")
    assert snap is not None
    assert snap["card_text"] == "card for aaa111"
    assert snap["fetched_at"] == "2026-09-27T00:00:00+00:00"
    assert database.get_card_snapshot("acme/rocket", "bbb222") is None
    assert database.get_card_snapshot("acme/other", "aaa111") is None


def test_restoring_same_sha_replaces_text_and_timestamp(database: Database):
    database.store_card_snapshot(_snapshot("acme/rocket", "aaa111", "2026-09-27T00:00:00+00:00"))
    database.store_card_snapshot(
        _snapshot("acme/rocket", "aaa111", "2026-09-27T05:00:00+00:00")
    )
    snap = database.get_card_snapshot("acme/rocket", "aaa111")
    assert snap is not None
    assert snap["fetched_at"] == "2026-09-27T05:00:00+00:00"


def test_older_revisions_prune_to_a_recent_trio(database: Database):
    for index, sha in enumerate(("aaa", "bbb", "ccc", "ddd", "eee")):
        database.store_card_snapshot(
            _snapshot("acme/rocket", sha, f"2026-09-27T0{index}:00:00+00:00")
        )
    kept = database.get_card_snapshot("acme/rocket", "eee")
    assert kept is not None
    assert database.get_card_snapshot("acme/rocket", "aaa") is None
    assert database.get_card_snapshot("acme/rocket", "bbb") is None
    assert database.get_card_snapshot("acme/rocket", "ccc") is not None
    assert database.get_card_snapshot("acme/rocket", "ddd") is not None


# ---------------------------------------------------------------------
# Route wiring: cache hits never touch the hub; failures stay absent
# ---------------------------------------------------------------------

class FlakyHub:
    """Stands in for the hub service; records every read_model_card call."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.behavior: Any = None

    def read_model_card(self, repo_id: str, revision: str = "main") -> str | None:
        self.calls.append((repo_id, revision))
        if isinstance(self.behavior, Exception):
            raise self.behavior
        return self.behavior


def _upsert_copy(database: Database, repo_id: str, sha: str | None) -> None:
    database.upsert_local_model(
        {
            "repo_id": repo_id,
            "relative_path": repo_id,
            "size_bytes": 10,
            "file_count": 1,
            "modified_at": "2026-09-27T00:00:00+00:00",
            "downloaded_at": None,
            "revision": "main",
            "sha": sha,
            "pipeline_tag": "text-generation",
            "library_name": "transformers",
            "license": "mit",
            "tags_json": "[]",
            "config_json": "{}",
            "source_url": "https://huggingface.co/" + repo_id,
            "managed": 1,
            "storage_backend": "filesystem",
            "cached": 1,
            "remote_uri": None,
        }
    )


def test_card_flow_via_route(
    database: Database, monkeypatch: pytest.MonkeyPatch
):
    import asyncio
    from app import main as main_module

    monkeypatch.setattr(main_module, "database", database)
    _upsert_copy(database, "acme/rocket", "aaa111")
    flaky = FlakyHub()
    flaky.behavior = "# The card at aaa111\nfine weights"
    monkeypatch.setattr(main_module, "hub", flaky)

    first = asyncio.run(main_module.local_model_card("acme/rocket", {"id": "keeper"}))
    assert first == {
        "sha": "aaa111",
        "card": "# The card at aaa111\nfine weights",
        "fetched_at": first["fetched_at"],
    }
    assert first["fetched_at"] is not None
    # Cache hit: a second read never touches the hub.
    second = asyncio.run(main_module.local_model_card("acme/rocket", {"id": "keeper"}))
    assert second == first
    assert len(flaky.calls) == 1

    # The copy moves: a fresh sha refetches once, then caches.
    _upsert_copy(database, "acme/rocket", "bbb222")
    flaky.calls.clear()
    flaky.behavior = "# The card at bbb222"
    moved = asyncio.run(main_module.local_model_card("acme/rocket", {"id": "keeper"}))
    assert moved["sha"] == "bbb222" and "bbb222" in moved["card"]
    assert len(flaky.calls) == 1


def test_card_failure_is_absent_not_stale(
    database: Database, monkeypatch: pytest.MonkeyPatch
):
    import asyncio
    from app import main as main_module

    monkeypatch.setattr(main_module, "database", database)
    _upsert_copy(database, "acme/rocket", "aaa111")
    flaky = FlakyHub()
    flaky.behavior = RuntimeError("upstream unreachable")
    monkeypatch.setattr(main_module, "hub", flaky)
    result = asyncio.run(main_module.local_model_card("acme/rocket", {"id": "keeper"}))
    assert result == {"sha": "aaa111", "card": None, "fetched_at": None}
    # Nothing was cached under the sha — the next read tries again.
    flaky.behavior = "# recovered"
    result = asyncio.run(main_module.local_model_card("acme/rocket", {"id": "keeper"}))
    assert result["card"] == "# recovered"


def test_cardless_copies_report_no_pinned_revision(
    database: Database, monkeypatch: pytest.MonkeyPatch
):
    import asyncio
    from app import main as main_module

    monkeypatch.setattr(main_module, "database", database)
    _upsert_copy(database, "local-upload/thing", None)
    flaky = FlakyHub()
    flaky.behavior = "# should never be read"
    monkeypatch.setattr(main_module, "hub", flaky)
    result = asyncio.run(main_module.local_model_card("local-upload/thing", {"id": "keeper"}))
    assert result == {"sha": None, "card": None, "fetched_at": None}
    assert flaky.calls == []


def test_card_route_404s_for_invisible_models(
    database: Database, monkeypatch: pytest.MonkeyPatch
):
    import asyncio
    from app import main as main_module

    monkeypatch.setattr(main_module, "database", database)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(main_module.local_model_card("ghost/nothing", {"id": "keeper"}))
    assert caught.value.status_code == 404

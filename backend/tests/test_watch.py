"""Watch mechanics: subscribing captures a baseline (the copy's
revision, or the upstream revision at subscribe time for copy-less
watches), drift is what the poller records, and agreement heals the
indication.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.database import Database
from app.watch import (
    WATCH_METADATA_FIELDS,
    WatchError,
    build_watch_payload,
    ensure_watch,
    poll_observation,
)


@pytest.fixture()
def database(tmp_path: Path) -> Database:
    database = Database(tmp_path / "library.sqlite3")
    database.initialize()
    database.create_user(
        {
            "id": "owner-1",
            "username": "keeper",
            "display_name": "The Keeper",
            "password_hash": "x",
            "role": "admin",
            "created_at": "2026-09-26T00:00:00+00:00",
            "updated_at": "2026-09-26T00:00:00+00:00",
        }
    )
    return database


SNAPSHOT = {
    "sha": "aaa111",
    "author": "acme",
    "pipeline_tag": "text-generation",
    "library_name": "transformers",
    "license": "mit",
}


def _clock():
    ticks = iter(
        f"2026-09-26T0{i}:00:00+00:00" for i in range(1, 9)
    )
    return lambda: next(ticks)


def _watch_copy(database: Database, repo_id: str, sha: str | None,
                parameter_count: int | None = None) -> None:
    database.upsert_local_model(
        {
            "repo_id": repo_id,
            "relative_path": repo_id,
            "size_bytes": 10,
            "file_count": 1,
            "modified_at": "now",
            "downloaded_at": None,
            "revision": "main",
            "sha": sha,
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
            "parameter_count": parameter_count,
        }
    )


def test_watching_a_copy_bases_itself_on_the_copy(database: Database):
    _watch_copy(database, "acme/echo", "sha-copy")
    row = ensure_watch(
        database, None, "acme/echo", "owner-1",
        copy_sha="sha-copy", clock=_clock(),
    )
    assert row["last_seen_sha"] == "sha-copy"
    assert row["upstream_sha"] is None
    assert not row["metadata"]


def test_watching_without_a_copy_captures_upstream(database: Database):
    row = ensure_watch(
        database, SNAPSHOT, "acme/distant", "owner-1",
        copy_sha=None, clock=_clock(),
    )
    assert row["last_seen_sha"] == "aaa111"
    assert row["metadata"]["author"] == "acme"
    assert set(row["metadata"]) == set(WATCH_METADATA_FIELDS)


def test_unreachable_upstream_is_a_loud_refusal(database: Database):
    with pytest.raises(WatchError, match="Upstream revision could not be read"):
        ensure_watch(
            database, None, "acme/distant", "owner-1",
            copy_sha=None, clock=_clock(),
        )
    assert database.get_watched_model("owner-1", "acme/distant") is None


def test_ensure_is_idempotent_and_never_resets_the_baseline(database: Database):
    ensure_watch(
        database, SNAPSHOT, "acme/distant", "owner-1",
        copy_sha=None, clock=_clock(),
    )
    again = ensure_watch(
        database, {"sha": "zzz999", "author": "other"}, "acme/distant", "owner-1",
        copy_sha=None, clock=_clock(),
    )
    assert again["last_seen_sha"] == "aaa111"
    assert database.list_watched_models("owner-1") and len(
        database.list_watched_models("owner-1")
    ) == 1


def test_payload_reports_the_copy_and_no_drift_before_a_poll(database: Database):
    _watch_copy(database, "acme/echo", "sha-copy")
    row = ensure_watch(
        database, None, "acme/echo", "owner-1",
        copy_sha="sha-copy", clock=_clock(),
    )
    payload = build_watch_payload(row, database.get_local_model("acme/echo"))
    assert payload["state"] == "copy"
    assert payload["update_available"] is False
    assert payload["metadata"]["author"] == "acme"  # copy fields fill in


def test_payload_carries_the_copy_facts_the_rows_surface_needs(database: Database):
    _watch_copy(database, "acme/counted", "sha-copy", parameter_count=70_557_022_720)
    row = ensure_watch(
        database, None, "acme/counted", "owner-1",
        copy_sha="sha-copy", clock=_clock(),
    )
    payload = build_watch_payload(row, database.get_local_model("acme/counted"))
    assert payload["copy"] == {
        "size_bytes": 10,
        "location": "primary",
        "storage_backend": "filesystem",
        "cached": True,
        "parameter_count": 70_557_022_720,
    }

def test_payload_reports_no_copy_when_there_is_none(database: Database):
    row = ensure_watch(
        database, SNAPSHOT, "acme/distant", "owner-1",
        copy_sha=None, clock=_clock(),
    )
    payload = build_watch_payload(row, None)
    assert payload["copy"] is None
    assert payload["state"] == "no-copy"


def test_payload_reports_drifted_copy_poll(database: Database):
    _watch_copy(database, "acme/echo", "sha-copy")
    ensure_watch(
        database, None, "acme/echo", "owner-1",
        copy_sha="sha-copy", clock=_clock(),
    )
    row = database.get_watched_model("owner-1", "acme/echo")
    observation = poll_observation(row, database.get_local_model("acme/echo"), "sha-new", "2026-09-26T05:00:00+00:00")
    database.record_watch_check(**observation)
    updated = database.get_watched_model("owner-1", "acme/echo")
    payload = build_watch_payload(updated, database.get_local_model("acme/echo"))
    assert payload["update_available"] is True
    assert payload["change_detected_at"] == "2026-09-26T05:00:00+00:00"


def test_copy_update_heals_the_drift_indication(database: Database):
    _watch_copy(database, "acme/echo", "sha-copy")
    ensure_watch(database, None, "acme/echo", "owner-1", copy_sha="sha-copy", clock=_clock())
    row = database.get_watched_model("owner-1", "acme/echo")
    database.record_watch_check(
        **poll_observation(row, database.get_local_model("acme/echo"), "sha-new", "t1")
    )
    _watch_copy(database, "acme/echo", "sha-new")  # the copy caught up
    healed = database.get_watched_model("owner-1", "acme/echo")
    database.record_watch_check(
        **poll_observation(healed, database.get_local_model("acme/echo"), "sha-new", "t2")
    )
    final = database.get_watched_model("owner-1", "acme/echo")
    payload = build_watch_payload(final, database.get_local_model("acme/echo"))
    assert payload["update_available"] is False
    assert final["changed_at"] is None
    assert final["checked_at"] == "t2"


def test_copyless_watch_drifts_on_upstream_change_and_marks_seen(database: Database):
    ensure_watch(database, SNAPSHOT, "acme/distant", "owner-1", copy_sha=None, clock=_clock())
    row = database.get_watched_model("owner-1", "acme/distant")
    database.record_watch_check(
        **poll_observation(row, None, "bbb222", "t1")
    )
    payload = build_watch_payload(
        database.get_watched_model("owner-1", "acme/distant"), None
    )
    assert payload["state"] == "no-copy"
    assert payload["update_available"] is True

    seen = database.mark_watch_seen("owner-1", "acme/distant", "bbb222", "t2")
    payload = build_watch_payload(seen, None)
    assert payload["update_available"] is False
    assert seen["last_seen_sha"] == "bbb222"


def test_first_detection_stamp_survives_later_polls(database: Database):
    ensure_watch(database, SNAPSHOT, "acme/distant", "owner-1", copy_sha=None, clock=_clock())
    row = database.get_watched_model("owner-1", "acme/distant")
    database.record_watch_check(**poll_observation(row, None, "bbb222", "t1"))
    second = database.get_watched_model("owner-1", "acme/distant")
    database.record_watch_check(**poll_observation(second, None, "ccc333", "t2"))
    third = database.get_watched_model("owner-1", "acme/distant")
    assert third["changed_at"] == "t1"  # first detection, not re-stamped
    assert third["upstream_sha"] == "ccc333"


def test_poll_unreachable_upstream_records_nothing_drifted(database: Database):
    ensure_watch(database, SNAPSHOT, "acme/distant", "owner-1", copy_sha=None, clock=_clock())
    row = database.get_watched_model("owner-1", "acme/distant")
    observation = poll_observation(row, None, None, "t1")
    assert observation["changed_at"] is None
    assert observation["upstream_sha"] is None
    database.record_watch_check(**observation)
    fresh = database.get_watched_model("owner-1", "acme/distant")
    assert fresh["upstream_sha"] is None
    # A previously-drifted watch whose upstream vanishes keeps its stamp.
    database.record_watch_check(**poll_observation(fresh, None, "bbb222", "t2"))
    drifted = database.get_watched_model("owner-1", "acme/distant")
    database.record_watch_check(**poll_observation(drifted, None, None, "t3"))
    keep = database.get_watched_model("owner-1", "acme/distant")
    assert keep["changed_at"] is not None
    assert keep["upstream_sha"] is None


def test_delete_removes_only_that_watch(database: Database):
    ensure_watch(database, SNAPSHOT, "acme/one", "owner-1", copy_sha=None, clock=_clock())
    ensure_watch(database, {"sha": "b2", "author": "x"}, "acme/two", "owner-1", copy_sha=None, clock=_clock())
    assert database.delete_watched_model("owner-1", "acme/one") is True
    assert database.delete_watched_model("owner-1", "acme/one") is False
    remaining = database.list_watched_models("owner-1")
    assert [row["repo_id"] for row in remaining] == ["acme/two"]


# --- The poller ---


@pytest.fixture()
def fast_spacing():
    from app.watch import WatchPoller

    original = WatchPoller.CHECK_SPACING_SECONDS
    WatchPoller.CHECK_SPACING_SECONDS = 0
    yield WatchPoller
    WatchPoller.CHECK_SPACING_SECONDS = original


def test_poller_checks_due_watches_and_advances_their_slots(
    database: Database, fast_spacing
):
    ensure_watch(database, SNAPSHOT, "acme/distant", "owner-1", copy_sha=None, clock=_clock())
    fetches: list[str] = []

    def fetch(repo_id: str) -> str | None:
        fetches.append(repo_id)
        return "aaa111"

    from app.watch import WatchPoller

    poller = WatchPoller(
        SimpleNamespace(watch_poll_minutes=120), database, fetch
    )
    assert poller.work_pass() == 1
    assert fetches == ["acme/distant"]
    row = database.get_watched_model("owner-1", "acme/distant")
    assert row["upstream_sha"] == "aaa111"
    assert row["changed_at"] is None  # baseline agrees: no news
    # A watch checked moments ago is not due again within the cadence.
    assert poller.work_pass() == 0
    assert len(fetches) == 1


def test_poller_records_drift_through_its_own_hands(
    database: Database, fast_spacing
):
    _watch_copy(database, "acme/echo", "sha-copy")
    ensure_watch(database, None, "acme/echo", "owner-1", copy_sha="sha-copy", clock=_clock())
    from app.watch import WatchPoller

    poller = WatchPoller(
        SimpleNamespace(watch_poll_minutes=120),
        database,
        lambda repo_id: "sha-upstream",
    )
    assert poller.work_pass() == 1
    row = database.get_watched_model("owner-1", "acme/echo")
    assert row["upstream_sha"] == "sha-upstream"
    assert row["changed_at"] is not None
    payload = build_watch_payload(row, database.get_local_model("acme/echo"))
    assert payload["update_available"] is True


def test_poller_failure_burns_the_slot_but_touches_nothing(
    database: Database, fast_spacing
):
    ensure_watch(database, SNAPSHOT, "acme/distant", "owner-1", copy_sha=None, clock=_clock())
    row = database.get_watched_model("owner-1", "acme/distant")
    database.record_watch_check(
        **poll_observation(row, None, "bbb222", "2026-09-01T00:00:00+00:00")
    )
    drifted = database.get_watched_model("owner-1", "acme/distant")

    def broken(repo_id: str) -> str | None:
        raise RuntimeError("hub unreachable")

    from app.watch import WatchPoller

    poller = WatchPoller(
        SimpleNamespace(watch_poll_minutes=120), database, broken
    )
    assert poller.work_pass() == 1  # the slot was used
    kept = database.get_watched_model("owner-1", "acme/distant")
    assert kept["upstream_sha"] == "bbb222"  # unchanged
    assert kept["changed_at"] == drifted["changed_at"]  # unchanged
    assert kept["checked_at"] > drifted["checked_at"]  # slot advanced


def test_poller_unreadable_upstream_is_unknown_not_clean(
    database: Database, fast_spacing
):
    ensure_watch(database, SNAPSHOT, "acme/distant", "owner-1", copy_sha=None, clock=_clock())
    row = database.get_watched_model("owner-1", "acme/distant")
    database.record_watch_check(
        **poll_observation(row, None, "bbb222", "2026-09-01T00:00:00+00:00")
    )
    from app.watch import WatchPoller

    poller = WatchPoller(
        SimpleNamespace(watch_poll_minutes=120), database, lambda repo_id: None
    )
    assert poller.work_pass() == 1
    kept = database.get_watched_model("owner-1", "acme/distant")
    assert kept["upstream_sha"] is None
    assert kept["changed_at"] is not None  # drift indication carries


def test_poller_thread_starts_and_stops_cleanly(database: Database):
    from app.watch import WatchPoller

    poller = WatchPoller(
        SimpleNamespace(watch_poll_minutes=120),
        database,
        lambda repo_id: "sha",
    )
    poller.start()
    assert poller._thread is not None and poller._thread.is_alive()
    poller.start()  # idempotent
    poller.stop(timeout=5.0)
    assert poller._thread is None

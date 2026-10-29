"""Queue control: pause/resume/pause-all, restart, move-to-front,
record clearing, and the gated-error mapping.

Unit tests drive the DownloadManager directly with a stubbed worker
(``_run``) and a fake hub; route tests pin the HTTP boundary the UI
actually calls, including per-user access control.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from app.manifests import MANIFEST_FILENAME


def _wait_for(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def _make_manager(tmp_path, max_workers: int = 2, **details_overrides):
    from app.config import Settings
    from app.database import Database
    from app.downloads import DownloadManager
    from app.indexer import LocalModelIndexer
    from app.storage import FilesystemModelStorage

    class Hub:
        settings = Settings(
            model_storage=(tmp_path / "models").resolve(),
            data_dir=(tmp_path / "data").resolve(),
            max_concurrent_downloads=max_workers,
        )

        @staticmethod
        def model_details(repo_id: str, revision: str):
            details = {
                "sha": "sha-abc",
                "gated": False,
                "source_url": f"https://huggingface.co/{repo_id}",
                "files": [
                    {"path": "model.safetensors", "size": 110, "blob_id": "blob-110"}
                ],
            }
            details.update(details_overrides)
            return details

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
    started: list[str] = []
    # The worker is under test elsewhere; queue tests only record which
    # job the dispatcher chose.
    manager._run = lambda job_id: started.append(job_id)  # type: ignore[method-assign]
    return manager, database, started


def _seed_download(database, repo_id: str, status: str, **extra) -> dict:
    created = extra.pop("created_at", "2026-01-01T00:00:00+00:00")
    record = {
        "id": "job-" + repo_id.replace("/", "-"),
        "repo_id": repo_id,
        "revision": "main",
        "status": status,
        "total_bytes": 100,
        "downloaded_bytes": 40,
        "progress": 40,
        "speed_bps": 0,
        "error": None,
        "target_path": None,
        "payload_json": "{}",
        "metadata_json": "{}",
        "created_at": created,
        "updated_at": created,
        "completed_at": None,
        "user_id": None,
    }
    record.update(extra)
    return database.create_download(record)


class FakeProcess:
    def __init__(self, alive: bool = True):
        self.alive = alive
        self.terminated = False

    def poll(self):
        return None if self.alive else 0

    def terminate(self):
        self.terminated = True
        self.alive = False


# --- pause / resume -----------------------------------------------------

def test_pause_queued_job_is_a_record_flip(tmp_path):
    manager, database, started = _make_manager(tmp_path)
    job = _seed_download(database, "acme/tiny", "queued")

    paused = manager.pause(job["id"])

    assert paused["status"] == "paused"
    assert paused["speed_bps"] == 0
    assert started == []  # nothing running was touched
    manager.shutdown()


def test_pause_running_job_keeps_files_and_writes_no_manifest(tmp_path):
    storage = tmp_path / "models"
    target = storage / "acme" / "large-model"
    target.mkdir(parents=True)
    (target / "weights.safetensors.incomplete").write_bytes(b"partial-data")

    manager, database, _ = _make_manager(tmp_path)
    job = _seed_download(
        database, "acme/large-model", "downloading", target_path=str(target)
    )
    process = FakeProcess()
    manager._processes[job["id"]] = process  # type: ignore[assignment]

    paused = manager.pause(job["id"])

    assert paused["status"] == "paused"
    assert paused["downloaded_bytes"] >= len(b"partial-data")
    assert process.terminated is True
    # A paused copy is not a cancelled one: no cancelled manifest.
    assert not (target / MANIFEST_FILENAME).exists()
    assert (target / "weights.safetensors.incomplete").is_file()
    manager.shutdown()


def test_resume_paused_job_requeues_and_dispatches(tmp_path):
    manager, database, _ = _make_manager(tmp_path)
    job = _seed_download(database, "acme/tiny", "paused")

    resumed = manager.resume(job["id"])

    assert resumed["status"] == "queued"
    assert job["id"] in manager._submitted
    assert _wait_for(lambda: job["id"] in manager._submitted)
    manager.shutdown()


def test_resume_rejects_non_paused_jobs(tmp_path):
    manager, database, _ = _make_manager(tmp_path)
    job = _seed_download(database, "acme/tiny", "complete")

    result = manager.resume(job["id"])

    assert result is not None
    assert result["status"] == "complete"
    assert job["id"] not in manager._submitted
    manager.shutdown()


def test_pause_all_holds_queued_and_running_jobs(tmp_path):
    manager, database, _ = _make_manager(tmp_path)
    queued = _seed_download(database, "acme/a", "queued")
    queued_late = _seed_download(
        database, "acme/b", "queued", created_at="2026-02-01T00:00:00+00:00"
    )
    running = _seed_download(database, "acme/c", "downloading")
    process = FakeProcess()
    manager._processes[running["id"]] = process  # type: ignore[assignment]

    paused = manager.pause_all()
    paused_by_id = {job["id"]: job for job in paused}

    assert set(paused_by_id) == {queued["id"], queued_late["id"], running["id"]}
    assert all(job["status"] == "paused" for job in paused_by_id.values())
    assert process.terminated is True
    assert database.paused_downloads() and not database.unfinished_downloads()
    manager.shutdown()


def test_resume_all_requeues_every_paused_job(tmp_path):
    manager, database, _ = _make_manager(tmp_path)
    first = _seed_download(database, "acme/a", "paused")
    second = _seed_download(database, "acme/b", "paused")

    resumed = manager.resume_all()

    assert {job["id"] for job in resumed} == {first["id"], second["id"]}
    assert _wait_for(lambda: len(manager._submitted) == 2)
    assert [job["status"] for job in database.paused_downloads()] == []
    manager.shutdown()


def test_paused_jobs_survive_a_restart_without_auto_resuming(tmp_path):
    manager, database, _ = _make_manager(tmp_path)
    _seed_download(database, "acme/a", "downloading")
    paused = _seed_download(database, "acme/b", "paused")

    manager.resume_unfinished()

    assert _wait_for(lambda: len(manager._submitted) == 1)
    assert database.get_download(paused["id"])["status"] == "paused"
    manager.shutdown()


# --- restart / move-to-front / clear ------------------------------------

def test_restart_failed_job_requeues_in_place(tmp_path):
    manager, database, _ = _make_manager(tmp_path)
    job = _seed_download(
        database,
        "acme/failed",
        "failed",
        error="boom",
        downloaded_bytes=41,
        progress=55,
        completed_at="2026-01-02T00:00:00+00:00",
    )

    restarted = manager.restart(job["id"])

    assert restarted["status"] == "queued"
    assert restarted["error"] is None
    assert restarted["progress"] == 0
    assert restarted["downloaded_bytes"] == 41  # files on disk stay
    assert restarted["completed_at"] is None
    assert _wait_for(lambda: job["id"] in manager._submitted)
    manager.shutdown()


def test_restart_rejects_non_failed_jobs(tmp_path):
    manager, database, _ = _make_manager(tmp_path)
    job = _seed_download(database, "acme/done", "complete")

    result = manager.restart(job["id"])

    assert result is not None
    assert result["status"] == "complete"
    assert job["id"] not in manager._submitted
    manager.shutdown()


def test_move_to_front_reorders_queue_and_starts_first(tmp_path):
    manager, database, started = _make_manager(tmp_path, max_workers=2)
    earlier = _seed_download(database, "acme/a", "queued")
    later = _seed_download(
        database, "acme/b", "queued", created_at="2026-02-01T00:00:00+00:00"
    )

    moved = manager.move_to_front(later["id"])

    assert moved["priority"] > 0
    order = [job["id"] for job in database.queued_downloads()]
    assert order == [later["id"], earlier["id"]]
    assert _wait_for(lambda: len(started) == 2)
    assert started == [later["id"], earlier["id"]]
    manager.shutdown()


def test_dispatch_respects_concurrent_capacity(tmp_path):
    manager, database, _ = _make_manager(tmp_path, max_workers=1)
    _seed_download(database, "acme/a", "queued")
    _seed_download(database, "acme/b", "queued")

    manager._dispatch()

    assert len(manager._submitted) == 1
    manager.shutdown()


# --- queue dedupe and gated fail-fast -----------------------------------

def test_queue_resumes_a_paused_job_for_the_same_repository(tmp_path):
    manager, database, _ = _make_manager(tmp_path)
    paused = _seed_download(database, "acme/tiny", "paused")

    job = manager.queue("acme/tiny", "main", "full")

    assert job["id"] == paused["id"]
    assert database.get_download(paused["id"])["status"] == "queued"
    manager.shutdown()


def test_queue_gated_repository_with_auth_error_maps_to_friendly_error(tmp_path):
    manager, _, _ = _make_manager(
        tmp_path,
        gated=True,
        source_url="https://huggingface.co/acme/locked",
    )
    manager._dry_run = (  # type: ignore[method-assign]
        lambda *args, **kwargs: (_ for _ in ()).throw(
            Exception(
                "403 Client Error: Forbidden for url: "
                "https://huggingface.co/api/models/acme/locked"
            )
        )
    )

    with pytest.raises(ValueError) as excinfo:
        manager.queue("acme/locked", "main", "full")

    message = str(excinfo.value)
    assert "This repository is gated" in message
    assert "https://huggingface.co/acme/locked" in message
    assert "Settings / Hub access" in message
    manager.shutdown()


def test_queue_gated_repository_with_500_offers_it_as_a_possibility(tmp_path):
    manager, _, _ = _make_manager(
        tmp_path,
        gated=True,
        source_url="https://huggingface.co/acme/locked",
    )
    manager._dry_run = (  # type: ignore[method-assign]
        lambda *args, **kwargs: (_ for _ in ()).throw(
            Exception("Request failed with status 500")
        )
    )

    with pytest.raises(ValueError) as excinfo:
        manager.queue("acme/locked", "main", "full")

    message = str(excinfo.value)
    assert "The Hub returned HTTP 500" in message
    assert "If this model is gated" in message
    manager.shutdown()


def test_queue_open_repository_with_500_keeps_raw_error(tmp_path):
    manager, _, _ = _make_manager(tmp_path)  # gated=False
    manager._dry_run = (  # type: ignore[method-assign]
        lambda *args, **kwargs: (_ for _ in ()).throw(
            Exception("Request failed with status 500")
        )
    )

    with pytest.raises(Exception) as excinfo:
        manager.queue("acme/plain", "main", "full")

    # An open repository gets the raw Hub error, not the gated guess.
    assert "gated" not in str(excinfo.value).lower()
    manager.shutdown()


# --- failure message mapping ---------------------------------------------

def test_failure_message_confirms_gating_from_plan_metadata(tmp_path):
    manager, _, _ = _make_manager(tmp_path)
    current = {
        "repo_id": "acme/locked",
        "metadata": {
            "gated": True,
            "source_url": "https://huggingface.co/acme/locked",
        },
    }

    message, metadata = manager._failure_message(
        Exception("some refusal"), current, attempts_made=1
    )

    assert "This repository is gated" in message
    assert "use an approved account's read token" in message
    assert metadata == current["metadata"]


def test_failure_message_maps_500_to_gated_possibility(tmp_path):
    manager, _, _ = _make_manager(tmp_path)
    current = {
        "repo_id": "acme/plain",
        "metadata": {"source_url": "https://huggingface.co/acme/plain"},
    }

    message, _ = manager._failure_message(
        Exception("Request failed with status 500"), current, attempts_made=1
    )

    assert "The Hub returned HTTP 500" in message
    assert "If this model is gated" in message


def test_failure_message_keeps_raw_message_and_output_tail(tmp_path):
    from app.downloads import DownloadWorkerExit

    manager, _, _ = _make_manager(tmp_path)
    current = {"repo_id": "acme/plain", "metadata": {}}

    message, metadata = manager._failure_message(
        DownloadWorkerExit(2, "boom-tail"), current, attempts_made=1
    )

    assert message == "boom-tail"
    assert metadata["output_tail"] == "boom-tail"


# --- cancel of a paused job ----------------------------------------------

def test_cancel_of_paused_job_marks_cancelled_with_manifest(tmp_path):
    storage = tmp_path / "models"
    target = storage / "acme" / "half"
    target.mkdir(parents=True)
    (target / "weights.safetensors.incomplete").write_bytes(b"partial-data")

    manager, database, _ = _make_manager(tmp_path)
    job = _seed_download(
        database, "acme/half", "paused", target_path=str(target)
    )

    cancelled = manager.cancel(job["id"])

    assert cancelled["status"] == "cancelled"
    manifest = (target / MANIFEST_FILENAME).read_text(encoding="utf-8")
    assert "cancelled" in manifest
    manager.shutdown()


# --- HTTP boundary ---------------------------------------------------------

@pytest.fixture()
def booted_app(client):
    """Owner session plus a member account, matching the other
    boundary tests; download records are seeded per test."""
    import app.main as main_module

    sign_in = client.post(
        "/api/auth/setup",
        json={"username": "keeper", "display_name": "Keeper", "password": "longenough1"},
    ).json()["csrf_token"]
    client.post(
        "/api/users",
        json={"username": "member", "display_name": "Member", "password": "longenough1"},
        headers={"X-CSRF-Token": sign_in},
    )
    member_client = client.__class__(main_module.app)
    member_csrf = member_client.post(
        "/api/auth/login",
        json={"username": "member", "password": "longenough1"},
    ).json()["csrf_token"]
    return {
        "module": main_module,
        "csrf": sign_in,
        "member_client": member_client,
        "member_csrf": member_csrf,
    }


def _seed_route_download(booted: dict, repo_id: str, user_id: str, status: str, **extra) -> str:
    from datetime import datetime, timezone

    moment = datetime.now(timezone.utc).isoformat(timespec="seconds")
    record = booted["module"].database.create_download(
        {
            "id": "job-" + repo_id.replace("/", "-") + "-" + status,
            "repo_id": repo_id,
            "revision": "main",
            "status": status,
            "total_bytes": 100,
            "downloaded_bytes": 40,
            "progress": 40,
            "speed_bps": 0,
            "error": None,
            "target_path": None,
            "payload_json": "{}",
            "metadata_json": "{}",
            "created_at": moment,
            "updated_at": moment,
            "completed_at": None,
            "user_id": user_id,
            **extra,
        }
    )
    return record["id"]


def test_queue_control_routes(client, booted_app, monkeypatch):
    module = booted_app["module"]
    monkeypatch.setattr(module.downloads, "_run", lambda job_id: None)
    users = module.database.list_users()
    admin_id = next(u["id"] for u in users if u["role"] == "admin")
    member_id = next(u["id"] for u in users if u["role"] == "member")
    headers = {"X-CSRF-Token": booted_app["csrf"]}
    member = booted_app["member_client"]
    member_csrf = {"X-CSRF-Token": booted_app["member_csrf"]}

    queued = _seed_route_download(booted_app, "acme/q", admin_id, "queued")
    failed = _seed_route_download(booted_app, "acme/f", admin_id, "failed", error="boom")
    complete = _seed_route_download(booted_app, "acme/c", admin_id, "complete")
    paused = _seed_route_download(booted_app, "acme/p", admin_id, "paused")
    seeded = [queued, failed, complete, paused]

    pause = client.post(f"/api/downloads/{queued}/pause", headers=headers)
    assert pause.status_code == 200
    assert pause.json()["status"] == "paused"

    twice = client.post(f"/api/downloads/{queued}/pause", headers=headers)
    assert twice.status_code == 409
    assert "already paused" in twice.json()["detail"]

    resume = client.post(f"/api/downloads/{paused}/resume", headers=headers)
    assert resume.status_code == 200
    assert resume.json()["status"] == "queued"

    resume_wrong = client.post(f"/api/downloads/{complete}/resume", headers=headers)
    assert resume_wrong.status_code == 409
    assert "Only paused" in resume_wrong.json()["detail"]

    restart = client.post(f"/api/downloads/{failed}/restart", headers=headers)
    assert restart.status_code == 200
    assert restart.json()["status"] == "queued"
    assert restart.json()["error"] is None

    restart_wrong = client.post(f"/api/downloads/{complete}/restart", headers=headers)
    assert restart_wrong.status_code == 409
    assert "Only failed" in restart_wrong.json()["detail"]

    moved = client.post(f"/api/downloads/{queued}/move-to-front", headers=headers)
    assert moved.status_code in (200, 409)  # queued was paused above
    moved_fresh = _seed_route_download(booted_app, "acme/q2", admin_id, "queued")
    moved = client.post(f"/api/downloads/{moved_fresh}/move-to-front", headers=headers)
    assert moved.status_code == 200
    assert moved.json()["priority"] > 0

    move_wrong = client.post(f"/api/downloads/{complete}/move-to-front", headers=headers)
    assert move_wrong.status_code == 409
    assert "Only queued" in move_wrong.json()["detail"]

    pause_all = client.post("/api/downloads/pause-all", headers=headers)
    assert pause_all.status_code == 200
    assert len(pause_all.json()["paused"]) >= 2

    resume_all = client.post("/api/downloads/resume-all", headers=headers)
    assert resume_all.status_code == 200
    assert len(resume_all.json()["resumed"]) >= 2

    # Leave no unfinished job behind: the app's lifespan re-dispatches
    # those on the next start, and the test executor is dead by then.
    for job_id in (*seeded, moved_fresh):
        module.database.delete_download(job_id)


def test_delete_and_clear_history_records(client, booted_app, monkeypatch):
    module = booted_app["module"]
    monkeypatch.setattr(module.downloads, "_run", lambda job_id: None)
    users = module.database.list_users()
    admin_id = next(u["id"] for u in users if u["role"] == "admin")
    member_id = next(u["id"] for u in users if u["role"] == "member")
    headers = {"X-CSRF-Token": booted_app["csrf"]}
    member = booted_app["member_client"]
    member_csrf = {"X-CSRF-Token": booted_app["member_csrf"]}

    done = _seed_route_download(booted_app, "acme/done", admin_id, "complete")
    failed = _seed_route_download(booted_app, "acme/failed", admin_id, "failed")
    live = _seed_route_download(booted_app, "acme/live", admin_id, "downloading")
    others = _seed_route_download(booted_app, "acme/theirs", member_id, "complete")
    seeded = [done, failed, live, others]

    delete_live = client.delete(f"/api/downloads/{live}", headers=headers)
    assert delete_live.status_code == 409
    assert "still active" in delete_live.json()["detail"]

    clear = client.post(
        "/api/downloads/clear",
        json={"ids": [failed, others, live]},
        headers=headers,
    )
    assert clear.status_code == 200
    # Admin clears its own records; the member-owned record is invisible
    # to it (the access model is owner-or-unowned, never cross-member),
    # and the live job is skipped.
    assert clear.json()["deleted"] == 1
    assert module.database.get_download(failed) is None
    assert module.database.get_download(others) is not None
    assert module.database.get_download(live) is not None

    delete = client.delete(f"/api/downloads/{done}", headers=headers)
    assert delete.status_code == 200
    assert delete.json() == {"deleted": True}
    assert module.database.get_download(done) is None

    cross_delete = member.delete(f"/api/downloads/{done}", headers=member_csrf)
    assert cross_delete.status_code == 404

    member_clear = member.post(
        "/api/downloads/clear", json={"ids": [failed, live]}, headers=member_csrf
    )
    assert member_clear.json()["deleted"] == 0  # both invisible/protected

    for job_id in seeded:
        module.database.delete_download(job_id)

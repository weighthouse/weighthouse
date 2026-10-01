"""Deletion and download routes at the boundary (D8 coverage pass).

The service layer proves deletion is safe; these tests pin the wall the
owner's data actually passes through: exact-name confirmation via HTTP,
the active-download guard on every mutating door, and per-user access
control on download records.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from datetime import datetime, timezone

import pytest


@pytest.fixture()
def booted_app(client):
    """Owner session plus one seeded tracked copy on disk."""
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

    storage = Path(main_module.settings.model_storage)
    return {
        "module": main_module,
        "csrf": sign_in,
        "member_client": member_client,
        "member_csrf": member_csrf,
        "storage": storage,
    }


def _seed_copy(booted: dict, repo_id: str) -> Path:
    import app.main as main_module
    from app.manifests import write_manifest

    root = Path(main_module.settings.model_storage) / repo_id
    root.mkdir(parents=True, exist_ok=True)
    (root / "model.safetensors").write_bytes(b"w" * 100)
    (root / "tokenizer.model").write_bytes(b"t" * 24)
    write_manifest(
        root,
        {
            "status": "complete",
            "schema_version": 2,
            "repo_id": repo_id,
            "revision": "main",
            "sha": "sha-x",
            "total_bytes": 124,
            "file_count": 2,
            "selection": {
                "files": [
                    {"path": "model.safetensors", "size": 100, "is_cached": False, "will_download": True, "blob": "b1"},
                    {"path": "tokenizer.model", "size": 24, "is_cached": True, "will_download": False, "blob": "b2"},
                ],
                "file_count": 2,
                "selected_bytes": 124,
                "transfer_bytes": 100,
                "cached_bytes": 24,
                "patterns": {"mode": "full", "allow": [], "ignore": []},
            },
        },
    )
    from datetime import datetime, timezone

    moment = datetime.now(timezone.utc).isoformat(timespec="seconds")
    main_module.database.upsert_local_model(
        {
            "repo_id": repo_id,
            "relative_path": repo_id,
            "size_bytes": 124,
            "file_count": 2,
            "modified_at": moment,
            "downloaded_at": moment,
            "revision": "main",
            "sha": "sha-x",
            "pipeline_tag": None,
            "library_name": None,
            "license": None,
            "tags_json": "[]",
            "config_json": "{}",
            "source_url": "https://huggingface.co/" + repo_id,
            "managed": 1,
            "storage_backend": "filesystem",
            "cached": 1,
            "remote_uri": None,
            "location": "primary",
        }
    )
    return root


def _reset_download(booted: dict, repo_id: str, user_id: str, status: str) -> str:
    from datetime import datetime, timezone

    moment = datetime.now(timezone.utc).isoformat(timespec="seconds")
    record = booted["module"].database.create_download(
        {
            "id": "job-" + repo_id.replace("/", "-"),
            "repo_id": repo_id,
            "revision": "main",
            "status": status,
            "total_bytes": 100,
            "downloaded_bytes": 40,
            "progress": 40,
            "speed_bps": 0,
            "error": None,
            "target_path": str(booted["storage"] / repo_id),
            "payload_json": "{}",
            "metadata_json": "{}",
            "created_at": moment,
            "updated_at": moment,
            "completed_at": None,
            "user_id": user_id,
        }
    )
    return record["id"]


def test_whole_copy_delete_requires_exact_confirmation(client, booted_app):
    repo_id = "acme/goneone"
    seed = _seed_copy(booted_app, repo_id)
    try:
        wrong = client.request(
            "DELETE",
            f"/api/local-models/{repo_id}",
            json={"confirmation": "acme/other"},
            headers={"X-CSRF-Token": booted_app["csrf"]},
        )
        assert wrong.status_code == 409
        assert "acme/other" not in wrong.text or "does not match" in wrong.text
        assert seed.exists()

        right = client.request(
            "DELETE",
            f"/api/local-models/{repo_id}",
            json={"confirmation": repo_id},
            headers={"X-CSRF-Token": booted_app["csrf"]},
        )
        assert right.status_code == 200
        assert right.json() == {"status": "deleted"}
        assert not seed.exists()
        listing = client.get(f"/api/local-models/{repo_id}")
        assert listing.status_code == 404
    finally:
        shutil.rmtree(seed, ignore_errors=True)
        booted_app["module"].database.delete_local_model(repo_id)


def test_active_download_blocks_delete_updates_and_file_deletion(client, booted_app):
    repo_id = "acme/busyboi"
    seed = _seed_copy(booted_app, repo_id)
    try:
        user = booted_app["module"].database.list_users()[0]
        _reset_download(booted_app, repo_id, user["id"], status="downloading")
        headers = {"X-CSRF-Token": booted_app["csrf"]}

        whole = client.request(
            "DELETE",
            f"/api/local-models/{repo_id}",
            json={"confirmation": repo_id},
            headers=headers,
        )
        assert whole.status_code == 409
        assert "before deleting this model" in whole.json()["detail"]
        assert seed.exists()

        updates = client.get(f"/api/local-models/{repo_id}/updates")
        assert updates.status_code == 409
        assert "before checking for updates" in updates.json()["detail"]

        applied = client.post(
            f"/api/local-models/{repo_id}/updates/apply",
            json={"paths": ["model.safetensors"]},
            headers=headers,
        )
        assert applied.status_code == 409

        files = client.post(
            f"/api/local-models/{repo_id}/deletion/files",
            json={"paths": ["model.safetensors"]},
            headers=headers,
        )
        assert files.status_code == 409
        assert "before deleting files" in files.json()["detail"]
    finally:
        shutil.rmtree(seed, ignore_errors=True)
        booted_app["module"].database.delete_local_model(repo_id)


def test_unsafe_paths_map_to_409_on_file_deletion(client, booted_app):
    repo_id = "acme/rescue"
    seed = _seed_copy(booted_app, repo_id)
    try:
        headers = {"X-CSRF-Token": booted_app["csrf"]}
        escape = client.post(
            f"/api/local-models/{repo_id}/deletion/files",
            json={"paths": ["../outside.bin"]},
            headers=headers,
        )
        assert escape.status_code == 409

        manifest_target = client.post(
            f"/api/local-models/{repo_id}/deletion/files",
            json={"paths": [".modelmanifest.json"]},
            headers=headers,
        )
        assert manifest_target.status_code == 409
        assert seed.exists()
    finally:
        shutil.rmtree(seed, ignore_errors=True)
        booted_app["module"].database.delete_local_model(repo_id)


def test_download_access_control_and_cancel_state(client, booted_app):
    from datetime import datetime, timezone

    module = booted_app["module"]
    users = module.database.list_users()
    admin_id = next(u["id"] for u in users if u["role"] == "admin")
    member_id = next(u["id"] for u in users if u["role"] == "member")

    admin_job = _reset_download(booted_app, "acme/admins-job", admin_id, "queued")
    member_job = _reset_download(booted_app, "acme/members-job", member_id, "queued")

    member = booted_app["member_client"]
    member_csrf = {"X-CSRF-Token": booted_app["member_csrf"]}

    cross = member.get(f"/api/downloads/{admin_job}")
    assert cross.status_code == 404
    cross_cancel = member.post(f"/api/downloads/{admin_job}/cancel", headers=member_csrf)
    assert cross_cancel.status_code == 404

    visible = member.get("/api/downloads")
    assert [item["id"] for item in visible.json()["items"]] == [member_job]

    cancelled = member.post(f"/api/downloads/{member_job}/cancel", headers=member_csrf)
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"

    twice = member.post(f"/api/downloads/{member_job}/cancel", headers=member_csrf)
    assert twice.status_code == 409
    assert "already cancelled" in twice.json()["detail"]

    # The admin's list shows the admin's row, not the member's private one.
    admin_view = client.get("/api/downloads")
    listed = {item["id"] for item in admin_view.json()["items"]}
    assert admin_job in listed
    assert member_job not in listed

"""The immediate check: POST /api/watches/check bypasses the poller's
cadence so the button means "check now". It only ever touches the
caller's watches, and an unreachable upstream is counted, not hidden."""

from __future__ import annotations

import app.main as main_module
from app.watch import ensure_watch


def _seed_copy_watch(owner_id: str, repo_id: str, copy_sha: str, parameter_count: int | None = None):
    main_module.database.upsert_local_model(
        {
            "repo_id": repo_id,
            "relative_path": repo_id,
            "size_bytes": 100,
            "file_count": 1,
            "modified_at": "2026-09-26T00:00:00+00:00",
            "downloaded_at": None,
            "revision": "main",
            "sha": copy_sha,
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
            "location": "primary",
            "parameter_count": parameter_count,
        }
    )
    ensure_watch(
        main_module.database, None, repo_id, owner_id,
        copy_sha=copy_sha, clock=lambda: "2026-09-26T00:00:00+00:00",
    )


def _owner_id(client):
    return client.get("/api/auth/status").json()["user"]["id"]


def test_check_now_reports_checked_and_unreachable(client, sign_in, monkeypatch):
    owner_id = _owner_id(client)
    _seed_copy_watch(owner_id, "acme/steady", "base-1")
    _seed_copy_watch(owner_id, "acme/moving", "base-2")

    def fetch(repo_id: str):
        if repo_id == "acme/moving":
            raise RuntimeError("hub unreachable")
        return "base-1"  # steady: upstream agrees with the copy

    monkeypatch.setattr(main_module.watch_poller, "_fetch_upstream_sha", fetch)

    response = client.post(
        "/api/watches/check", headers={"X-CSRF-Token": sign_in}
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"checked": 1, "unreachable": 1}

    items = {item["repo_id"]: item for item in client.get("/api/watches").json()["items"]}
    assert items["acme/steady"]["update_available"] is False
    assert items["acme/moving"]["update_available"] is False  # untouched: unknown, not clean


def test_check_now_detects_drift_through_its_own_hands(client, sign_in, monkeypatch):
    owner_id = _owner_id(client)
    _seed_copy_watch(owner_id, "acme/behind", "base-1")
    monkeypatch.setattr(
        main_module.watch_poller, "_fetch_upstream_sha", lambda repo_id: "base-2"
    )

    response = client.post("/api/watches/check", headers={"X-CSRF-Token": sign_in})
    assert response.json() == {"checked": 1, "unreachable": 0}
    item = client.get("/api/watches").json()["items"][0]
    assert item["update_available"] is True
    assert item["change_detected_at"] is not None


def test_check_now_refuses_anonymous_and_tokenless_writes(client, sign_in):
    from fastapi.testclient import TestClient

    anonymous = TestClient(main_module.app).post("/api/watches/check")
    assert anonymous.status_code == 401
    wall = client.post("/api/watches/check")
    assert wall.status_code == 403
    assert "Security token" in wall.json()["detail"]


def test_check_now_touches_only_the_callers_watches(client, sign_in, monkeypatch):
    owner_id = _owner_id(client)
    _seed_copy_watch(owner_id, "acme/mine", "base-1")
    # A second account with its own watch must be untouched by the owner's pass.
    created = client.post(
        "/api/users",
        json={"username": "other", "display_name": "Other", "password": "longenough1"},
        headers={"X-CSRF-Token": sign_in},
    )
    other_id = created.json()["id"]
    _seed_copy_watch(other_id, "acme/theirs", "base-1")

    fetched: list[str] = []

    def fetch(repo_id: str):
        fetched.append(repo_id)
        return "base-1"

    monkeypatch.setattr(main_module.watch_poller, "_fetch_upstream_sha", fetch)
    response = client.post("/api/watches/check", headers={"X-CSRF-Token": sign_in})
    assert response.json() == {"checked": 1, "unreachable": 0}
    assert fetched == ["acme/mine"]  # never reached the other account's repo

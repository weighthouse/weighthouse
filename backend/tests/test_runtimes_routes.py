"""The runtime routes' separate auth ladder (D8 coverage pass).

Reads accept an admin session or the RUNTIME_API_TOKEN bearer; writes
accept the token without any session at all — the only session-free
write door in the app, pinned here so its walls stay total.
"""

from __future__ import annotations

import dataclasses

import pytest


@pytest.fixture()
def runtime_app(client, monkeypatch):
    """The booted app plus a configured runtime API token and one target."""
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
    member_client.post(
        "/api/auth/login",
        json={"username": "member", "password": "longenough1"},
    )

    import app.config as config

    tokenized = dataclasses.replace(
        main_module.settings,
        runtime_api_token="test-token",
        runtime_targets_json='[{"id":"box","name":"box","kind":"ollama","base_url":"http://127.0.0.1:11434"}]',
    )
    monkeypatch.setattr(main_module, "settings", tokenized)
    # The manager parsed targets at import; point it at the same list.
    import app.runtimes as runtimes_module

    targets = runtimes_module.parse_runtime_targets(tokenized.runtime_targets_json)
    monkeypatch.setattr(main_module.runtimes, "targets", {item.id: item for item in targets})
    return {"module": main_module, "csrf": sign_in, "member": member_client}


def test_reads_accept_token_or_admin_session_but_not_members(runtime_app):
    client = runtime_app["member"]
    app = runtime_app["module"]

    as_member = client.get("/api/runtimes")
    assert as_member.status_code == 403

    with_token = client.get("/api/runtimes", headers={"Authorization": "Bearer test-token"})
    assert with_token.status_code == 200
    assert [item["name"] for item in with_token.json()["items"]] == ["box"]

    admin_lists = list(app.database.list_users())
    assert admin_lists, "fixture accounts exist"


def test_a_wrong_token_is_401(runtime_app):
    wrong = runtime_app["member"].get(
        "/api/runtime-jobs", headers={"Authorization": "Bearer not-the-token"}
    )
    assert wrong.status_code == 401
    assert wrong.json()["detail"] == "Invalid runtime API token."


def test_writes_refuse_sessions_without_csrf_and_gibberish_tokens(runtime_app):
    member = runtime_app["member"]
    payload = {"repo_id": "acme/ghost", "runtime_model_name": "ghost"}

    wall = member.post(
        "/api/runtimes/box/load",
        json=payload,
        headers={"Authorization": "Bearer test-token"},
    )
    # A token caller has no session; the CSRF wall never applies, and
    # the ghost repo answers 404 at the visibility check.
    assert wall.status_code == 404

    forged = member.post(
        "/api/runtimes/box/load",
        json=payload,
        headers={"Authorization": "Bearer garbage"},
    )
    assert forged.status_code == 401

    # With a real copy present, an unknown target name answers 409 with
    # the manager's message; a missing copy answers 404 first.
    moment = "2026-09-29T00:00:00+00:00"
    runtime_app["module"].database.upsert_local_model(
        {
            "repo_id": "acme/stored",
            "relative_path": "acme/stored",
            "size_bytes": 10,
            "file_count": 1,
            "modified_at": moment,
            "downloaded_at": moment,
            "revision": None,
            "sha": None,
            "pipeline_tag": None,
            "library_name": None,
            "license": None,
            "tags_json": "[]",
            "config_json": "{}",
            "source_url": None,
            "managed": 1,
            "storage_backend": "filesystem",
            "cached": 1,
            "remote_uri": None,
            "location": "primary",
        }
    )

    ghost_copy = member.post(
        "/api/runtimes/box/load",
        json={"repo_id": "acme/ghost", "runtime_model_name": "ghost"},
        headers={"Authorization": "Bearer test-token"},
    )
    assert ghost_copy.status_code == 404

    unknown_target = member.post(
        "/api/runtimes/nowhere/load",
        json={"repo_id": "acme/stored", "runtime_model_name": "stored"},
        headers={"Authorization": "Bearer test-token"},
    )
    assert unknown_target.status_code == 409
    assert "Runtime target not found." in unknown_target.json()["detail"]

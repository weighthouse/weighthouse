"""The HTTP boundary, end to end (D8 coverage pass, first increment).

The service layer has deep coverage; these tests pin what a refactor
could silently drop at the doors themselves: the setup gate, the
anonymous wall, CSRF across the dependency flavors, the rate limiter,
the session lifecycle, and the agent-key scope ladder at real routes.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def test_setup_gate_428_then_anonymous_401_through_the_app(client):
    gated = client.get("/api/local-models")
    assert gated.status_code == 428
    assert "owner account" in gated.json()["detail"]

    setup = client.post(
        "/api/auth/setup",
        json={"username": "keeper", "display_name": "Keeper", "password": "longenough1"},
    )
    assert setup.status_code == 201

    # A setup call signs its caller in; a genuinely anonymous visitor is
    # a fresh client, not just the same jar after setup.
    import app.main as main_module
    from fastapi.testclient import TestClient

    stranger = TestClient(main_module.app)
    anonymous = TestClient(main_module.app).get("/api/local-models")
    assert anonymous.status_code == 401
    status = stranger.get("/api/auth/status")
    assert status.json()["setup_required"] is False
    assert status.json()["user"] is None


def test_csrf_is_demanded_for_session_writes_and_reads_write_freely(client, sign_in):
    wall = client.post("/api/watches", json={"repo_id": "acme/tiny"})
    assert wall.status_code == 403
    assert "Security token" in wall.json()["detail"]

    passed = client.post(
        "/api/watches",
        json={"repo_id": "acme/tiny"},
        headers={"X-CSRF-Token": sign_in},
    )
    assert passed.status_code != 403, "with a token the write passes the wall"

    reading = client.get("/api/preferences")
    assert reading.status_code == 200


def test_admin_writes_are_csrf_guarded_and_members_are_locked_out(client, sign_in):
    import app.main as main_module
    from fastapi.testclient import TestClient

    created = client.post(
        "/api/users",
        json={"username": "member", "display_name": "Member", "password": "longenough1"},
        headers={"X-CSRF-Token": sign_in},
    )
    assert created.status_code == 201

    member = TestClient(main_module.app)
    signed = member.post(
        "/api/auth/login",
        json={"username": "member", "password": "longenough1"},
    )
    assert signed.status_code == 200

    admin_read = member.get("/api/agent-keys")
    assert admin_read.status_code == 403
    admin_write = member.post(
        "/api/users",
        json={"username": "second", "display_name": "Second", "password": "longenough1"},
        headers={"X-CSRF-Token": signed.json()["csrf_token"]},
    )
    assert admin_write.status_code == 403

    scoped = member.get("/api/users")
    assert scoped.status_code == 200
    assert [item["username"] for item in scoped.json()["items"]] == ["member"]


def test_login_logout_rate_limit_and_session_lifecycle(client, sign_in):
    wrong = client.post(
        "/api/auth/login",
        json={"username": "keeper", "password": "not-the-password"},
    )
    assert wrong.status_code == 401

    statuses = []
    for attempt in range(10):
        attempt_response = client.post(
            "/api/auth/login",
            json={"username": "keeper", "password": "still-not-it"},
        )
        statuses.append(attempt_response.status_code)
    assert all(status in (401, 429) for status in statuses)
    assert statuses.count(429) >= 1, "the limiter must answer within ten tries"
    locked_message = client.post(
        "/api/auth/login",
        json={"username": "keeper", "password": "longenough1"},
    ).json()["detail"]
    assert locked_message == "Too many sign-in attempts. Try again in a few minutes."

    # A fresh app instance on the next test resets the limiter; within
    # this one, logout's CSRF wall is probed with the fixture session.
    header_wall = client.post("/api/auth/logout")
    assert header_wall.status_code == 403
    signed_out = client.post("/api/auth/logout", headers={"X-CSRF-Token": sign_in})
    assert signed_out.status_code == 200
    assert signed_out.json() == {"status": "signed_out"}
    assert client.get("/api/auth/status").json()["user"] is None


def test_agent_key_scope_ladder_per_route_family(client, sign_in):
    import app.main as main_module

    def mint(name: str, scopes: list[str]) -> dict:
        response = client.post(
            "/api/agent-keys",
            json={"name": name, "scopes": scopes},
            headers={"X-CSRF-Token": sign_in},
        )
        assert response.status_code == 201, response.text
        payload = response.json()
        return {"key": payload["key"], "id": payload["record"]["id"]}

    search_key = mint("search key", ["search"])
    read_key = mint("read key", ["read"])
    queue_key = mint("queue key", ["queue"])
    mutate_key = mint("mutate key", ["mutate"])

    def bearer(key: dict) -> dict:
        return {"Authorization": f"Bearer {key['key']}"}

    # read gates location facts; a search-only key has no business there.
    assert client.get("/api/locations", headers=bearer(read_key)).status_code == 200
    denied = client.get("/api/locations", headers=bearer(search_key))
    assert denied.status_code == 403
    assert "lacks the 'read' scope" in denied.json()["detail"]

    # queue opens the update-apply door, which answers 404 for a ghost
    # copy once the gate is passed; a read key never reaches it.
    ghost_payload = {"paths": ["model.zip"]}
    through_gate = client.post(
        "/api/local-models/ghost/repo/updates/apply",
        json=ghost_payload,
        headers=bearer(queue_key),
    )
    assert through_gate.status_code == 404
    behind_gate = client.post(
        "/api/local-models/ghost/repo/updates/apply",
        json=ghost_payload,
        headers=bearer(read_key),
    )
    assert behind_gate.status_code == 403

    # mutate owns the rescan; queue-only keys stay out.
    assert client.post("/api/local-models/scan", headers=bearer(mutate_key)).status_code == 200
    assert client.post("/api/local-models/scan", headers=bearer(queue_key)).status_code == 403

    # search drives the Hub listing; monkeypatched so no network runs.
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(main_module.hub, "search_models", lambda *args, **kwargs: [])
    try:
        hub_response = client.get("/api/hub/models", headers=bearer(search_key))
        assert hub_response.status_code == 200
        assert hub_response.json()["count"] == 0
        assert client.get("/api/hub/models", headers=bearer(queue_key)).status_code == 403
    finally:
        monkeypatch.undo()

    # Every bearer request left one audit row, method, path, status.
    events = client.get(
        f"/api/agent-keys/{queue_key['id']}/events", headers=bearer(read_key)
    )
    assert events.status_code == 200
    rows = events.json()["items"]
    assert any(
        row["path"] == "/api/local-models/ghost/repo/updates/apply"
        and row["method"] == "POST"
        and row["status"] == 404
        for row in rows
    )

    revoked = client.delete(
        f"/api/agent-keys/{queue_key['id']}", headers={"X-CSRF-Token": sign_in}
    )
    assert revoked.status_code == 200
    after = client.post(
        "/api/local-models/ghost/repo/updates/apply",
        json=ghost_payload,
        headers=bearer(queue_key),
    )
    assert after.status_code == 401
    assert "revoked" in after.json()["detail"]


def test_mcp_route_framing_and_auth(client, sign_in):
    def bearer(token: str) -> dict:
        return {"Authorization": f"Bearer {token}"}

    mint = client.post(
        "/api/agent-keys",
        json={"name": "mcp", "scopes": ["search", "read", "queue", "mutate"]},
        headers={"X-CSRF-Token": sign_in},
    )
    assert mint.status_code == 201
    raw = mint.json()["key"]

    garbage = client.post("/api/mcp", content=b"{not json", headers=bearer(raw))
    assert garbage.status_code == 400
    assert "error" in garbage.json()

    assert client.get("/api/mcp", headers=bearer(raw)).status_code == 405
    assert client.delete("/api/mcp", headers=bearer(raw)).status_code == 204

    # No session and no key: the wall answers before the endpoint exists.
    import app.main as main_module
    from fastapi.testclient import TestClient

    forged = TestClient(main_module.app).post(
        "/api/mcp",
        content=b'{"jsonrpc": "2.0"}',
        headers={"Authorization": "Bearer ak-not-a-key"},
    )
    assert forged.status_code == 401

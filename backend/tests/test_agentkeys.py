"""Agent API keys: named tokens with scopes and an audit trail.

These tests pin the bearer resolve path (the door agents knock on), the
scope ladder enforcement, revocation that leaves the audit trail
readable, and the per-key event retention cap.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from app.agentkeys import (
    AGENT_KEY_PREFIX,
    EVENT_RETENTION,
    AgentKeyDenied,
    AgentKeyError,
    generate_agent_key,
    key_hint,
    parse_scopes,
    resolve_agent_bearer,
)
from app.database import Database


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


def _mint(database: Database, scopes=("read",), name="homelab agent", key_id="key-1") -> tuple[str, dict]:
    raw, digest = generate_agent_key()
    record = {
        "id": key_id,
        "name": name,
        "key_hash": digest,
        "key_hint": key_hint(raw),
        "scopes_json": '["' + '","'.join(scopes) + '"]',
        "user_id": "owner-1",
        "created_at": "2026-09-26T00:00:00+00:00",
        "last_used_at": None,
        "revoked_at": None,
    }
    database.create_agent_key(record)
    return raw, record


def test_generated_keys_are_prefixed_and_hinted():
    raw, digest = generate_agent_key()
    assert raw.startswith(AGENT_KEY_PREFIX)
    assert len(digest) == 64
    assert digest == hashlib.sha256(raw.encode("utf-8")).hexdigest()
    assert key_hint(raw) == f"…{raw[-4:]}"


def test_scopes_are_validated_and_deduped():
    assert parse_scopes(["read", "search", "read"]) == ["read", "search"]
    with pytest.raises(AgentKeyError, match="Unknown scope"):
        parse_scopes(["read", "root"])
    with pytest.raises(AgentKeyError, match="at least one"):
        parse_scopes([])
    with pytest.raises(AgentKeyError):
        parse_scopes("read")  # type: ignore[arg-type]


def test_resolve_returns_owner_and_key(database: Database):
    raw, _ = _mint(database, scopes=("search", "read"))
    context = resolve_agent_bearer(raw, database, ("search",))
    assert context["user"]["username"] == "keeper"
    assert context["key"]["id"] == "key-1"
    assert set(context["key"]["scopes"]) == {"search", "read"}


@pytest.mark.parametrize(
    "raw",
    [None, "not-a-key", "ak_"],
)
def test_missing_or_unrecognized_tokens_are_denied(raw, database: Database):
    with pytest.raises(AgentKeyDenied) as denied:
        resolve_agent_bearer(raw, database, ("read",))
    assert denied.value.status == 401


def test_unknown_digest_is_denied(database: Database):
    with pytest.raises(AgentKeyDenied) as denied:
        resolve_agent_bearer(f"{AGENT_KEY_PREFIX}foreigntoken", database, ("read",))
    assert denied.value.status == 401


def test_missing_scope_names_the_ladder_step(database: Database):
    raw, _ = _mint(database, scopes=("search",))
    with pytest.raises(AgentKeyDenied) as denied:
        resolve_agent_bearer(raw, database, ("search", "read"))
    assert denied.value.status == 403
    assert "'read'" in denied.value.message


def test_revocation_is_a_stamp_not_an_erasure(database: Database):
    raw, record = _mint(database)
    assert database.revoke_agent_key("key-1", "2026-09-26T09:00:00+00:00") is True
    with pytest.raises(AgentKeyDenied, match="revoked"):
        resolve_agent_bearer(raw, database, ("read",))
    # Revocation is idempotent through the API path and leaves the record.
    assert database.revoke_agent_key("key-1", "2026-09-26T10:00:00+00:00") is False
    stored = database.get_agent_key("key-1")
    assert stored["revoked_at"] == "2026-09-26T09:00:00+00:00"
    assert stored["name"] == record["name"]


def test_listing_attaches_the_owner_without_the_digest(database: Database):
    _mint(database)
    keys = database.list_agent_keys()
    assert len(keys) == 1
    assert keys[0]["user"]["username"] == "keeper"
    assert "key_hash" not in keys[0]["user"]
    assert keys[0]["scopes"] == ["read"]


def test_touch_marks_last_used(database: Database):
    raw, _ = _mint(database)
    resolve_agent_bearer(raw, database, ("read",))
    database.touch_agent_key("key-1", "2026-09-26T12:00:00+00:00")
    assert database.get_agent_key("key-1")["last_used_at"] == "2026-09-26T12:00:00+00:00"


def test_audit_trail_is_capped_per_key(database: Database):
    _mint(database)
    for index in range(EVENT_RETENTION + 50):
        database.record_agent_key_event(
            {
                "id": f"event-{index:04}",
                "key_id": "key-1",
                "method": "GET",
                "path": f"/api/local-models?page={index}",
                "status": 200,
                "created_at": f"2026-09-26T00:{index // 60:02d}:{index % 60:02d}",
            }
        )
    events = database.list_agent_key_events("key-1", limit=EVENT_RETENTION)
    assert len(events) == EVENT_RETENTION
    assert events[0]["path"].endswith(str(EVENT_RETENTION + 49))
    # A second key keeps its own independent trail.
    raw, _ = _mint(database, scopes=("read",), name="second", key_id="key-2")
    second_id = database.get_agent_key_by_hash(hashlib.sha256(raw.encode()).hexdigest())["id"]
    assert database.list_agent_key_events(second_id) == []


def test_active_key_count_drives_the_cap(database: Database):
    assert database.count_active_agent_keys() == 0
    _mint(database)
    assert database.count_active_agent_keys() == 1
    database.revoke_agent_key("key-1", "2026-09-26T09:00:00+00:00")
    assert database.count_active_agent_keys() == 0


def test_purge_ends_a_key_and_its_trail(database: Database):
    raw, _ = _mint(database, scopes=("read",))
    context = resolve_agent_bearer(raw, database, ("read",))
    assert context["key"]["id"] == "key-1"
    database.record_agent_key_event(
        {
            "id": "event-1",
            "key_id": "key-1",
            "method": "GET",
            "path": "/api/local-models",
            "status": 200,
            "created_at": "2026-09-26T01:00:00+00:00",
        }
    )
    assert len(database.list_agent_key_events("key-1")) == 1
    assert database.purge_agent_key("key-1") is True
    assert database.get_agent_key("key-1") is None
    assert database.list_agent_key_events("key-1") == []
    assert database.count_active_agent_keys() == 0
    with pytest.raises(AgentKeyDenied) as denied:
        resolve_agent_bearer(raw, database, ("read",))
    assert denied.value.status == 401
    assert database.purge_agent_key("key-1") is False


# ---------------------------------------------------------------------
# Route-level regression: the admin dependency must CSRF-guard writes,
# never reads. It once chained through require_write_user, which put
# every admin GET behind a header browsers never send on GET — the key
# list and its scope catalog stayed silently empty for everyone while
# the mint flash succeeded and vanished. Reads open; writes keep the
# token requirement.

def test_admin_reads_open_without_csrf_and_writes_still_require_it(client):
    setup = client.post(
        "/api/auth/setup",
        json={"username": "keeper", "display_name": "Keeper", "password": "longenough1"},
    )
    assert setup.status_code == 201
    csrf = setup.json()["csrf_token"]

    listing = client.get("/api/agent-keys")
    assert listing.status_code == 200
    assert listing.json()["scopes"]  # the scope catalog reaches the page

    blocked = client.post("/api/agent-keys", json={"name": "no token", "scopes": ["read"]})
    assert blocked.status_code == 403

    mint = client.post(
        "/api/agent-keys",
        headers={"X-CSRF-Token": csrf},
        json={"name": "inventory", "scopes": ["search", "read"]},
    )
    assert mint.status_code == 201
    key_id = mint.json()["record"]["id"]

    refreshed = client.get("/api/agent-keys")
    assert [key["name"] for key in refreshed.json()["items"]] == ["inventory"]

    events = client.get(f"/api/agent-keys/{key_id}/events")
    assert events.status_code == 200


def test_mint_for_another_member_acts_as_that_member(client):
    setup = client.post(
        "/api/auth/setup",
        json={"username": "keeper", "display_name": "Keeper", "password": "longenough1"},
    )
    csrf = setup.json()["csrf_token"]

    member = client.post(
        "/api/users",
        headers={"X-CSRF-Token": csrf},
        json={"username": "mira", "display_name": "Mira", "password": "longenough2"},
    )
    assert member.status_code == 201
    member_id = member.json()["id"]

    # No user_id: the key belongs to the caller (back-compat default).
    own = client.post(
        "/api/agent-keys",
        headers={"X-CSRF-Token": csrf},
        json={"name": "own", "scopes": ["read"]},
    )
    assert own.json()["record"]["user"]["username"] == "keeper"

    # With user_id: the key acts as the chosen member's account.
    minted = client.post(
        "/api/agent-keys",
        headers={"X-CSRF-Token": csrf},
        json={"name": "mira-agent", "scopes": ["read"], "user_id": member_id},
    )
    assert minted.status_code == 201
    record = minted.json()["record"]
    assert record["user"] == {
        "id": member_id,
        "username": "mira",
        "display_name": "Mira",
    }

    # A key minted for mira resolves as mira on the bearer door.
    import app.main as main_module

    context = resolve_agent_bearer(minted.json()["key"], main_module.database, ("read",))
    assert context["user"]["username"] == "mira"

    # Unknown accounts are refused at the mint, not at first use.
    foreign = client.post(
        "/api/agent-keys",
        headers={"X-CSRF-Token": csrf},
        json={"name": "ghost", "scopes": ["read"], "user_id": "no-such-user"},
    )
    assert foreign.status_code == 400


def test_purge_route_totally_removes_a_revoked_key(client):
    setup = client.post(
        "/api/auth/setup",
        json={"username": "keeper", "display_name": "Keeper", "password": "longenough1"},
    )
    assert setup.status_code == 201
    csrf = setup.json()["csrf_token"]

    writes = client.delete("/api/agent-keys/whatever/purge")
    assert writes.status_code == 403  # the token requirement stays on writes

    mint = client.post(
        "/api/agent-keys",
        headers={"X-CSRF-Token": csrf},
        json={"name": "purgeme", "scopes": ["read"]},
    )
    assert mint.status_code == 201
    key_id = mint.json()["record"]["id"]
    revoke = client.delete(f"/api/agent-keys/{key_id}", headers={"X-CSRF-Token": csrf})
    assert revoke.status_code == 200

    purged = client.delete(f"/api/agent-keys/{key_id}/purge", headers={"X-CSRF-Token": csrf})
    assert purged.status_code == 200
    assert purged.json() == {"status": "purged"}

    again = client.delete(f"/api/agent-keys/{key_id}/purge", headers={"X-CSRF-Token": csrf})
    assert again.status_code == 404

    trail = client.get(f"/api/agent-keys/{key_id}/events")
    assert trail.status_code == 404
    assert client.get("/api/agent-keys").json()["items"] == []

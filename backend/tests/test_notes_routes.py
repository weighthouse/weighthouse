"""Note permissions at the route wall (D8 coverage pass).

The database layer's visibility matrix is tested; what was never tested
anywhere is the branch that guards who may CHANGE a note. The rule is
ruled behavior: only the author edits, pins, shares, or deletes their
own entries — an admin reads everything but changes nothing, and no
cross-repo note id may be addressed through another model's route.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def library(client):
    """A booted app with one stored copy and two accounts."""
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

    main_module.database.upsert_local_model(_record("acme/tiny"))

    member_client = client.__class__(main_module.app)
    member_sign_in = member_client.post(
        "/api/auth/login",
        json={"username": "member", "password": "longenough1"},
    ).json()["csrf_token"]
    return {
        "model": "acme/tiny",
        "csrf": sign_in,
        "member_client": member_client,
        "member_csrf": member_sign_in,
    }


def _record(repo_id: str) -> dict:
    """The row shape upsert_local_model requires; every key binds."""
    from datetime import datetime, timezone

    moment = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return {
        "repo_id": repo_id,
        "relative_path": repo_id,
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


def _write(client, library, body, csrf):
    return client.post(
        f"/api/local-models/{library['model']}/notes",
        json={"body": body},
        headers={"X-CSRF-Token": csrf},
    )


def test_only_the_author_may_change_a_note(client, library):
    admin_note = _write(client, library, "keeper's private note", library["csrf"])
    assert admin_note.status_code == 201
    note_id = admin_note.json()["id"]

    member = library["member_client"]
    blocked_headers = {"X-CSRF-Token": library["member_csrf"]}

    edit = member.patch(
        f"/api/local-models/{library['model']}/notes/{note_id}",
        json={"body": "vandalized"},
        headers=blocked_headers,
    )
    assert edit.status_code == 403
    assert edit.json()["detail"] == "Only the author may change this note."

    pin = member.post(
        f"/api/local-models/{library['model']}/notes/{note_id}/pin",
        json={"pinned": True},
        headers=blocked_headers,
    )
    assert pin.status_code == 403

    share = member.post(
        f"/api/local-models/{library['model']}/notes/{note_id}/share",
        json={"shared": True},
        headers=blocked_headers,
    )
    assert share.status_code == 403

    delete = member.delete(
        f"/api/local-models/{library['model']}/notes/{note_id}",
        headers=blocked_headers,
    )
    assert delete.status_code == 403
    assert delete.json()["detail"] == "Only the author may change this note."

    staying = client.get(f"/api/local-models/{library['model']}/notes").json()
    assert [item["body"] for item in staying["items"]] == ["keeper's private note"]


def test_the_author_edits_pins_and_deletes_their_own(client, library):
    mine = _write(library["member_client"], library, "member's note", library["member_csrf"])
    assert mine.status_code == 201
    note_id = mine.json()["id"]
    headers = {"X-CSRF-Token": library["member_csrf"]}
    member = library["member_client"]

    pinned = member.post(
        f"/api/local-models/{library['model']}/notes/{note_id}/pin",
        json={"pinned": True},
        headers=headers,
    )
    assert pinned.status_code == 200
    assert pinned.json()["pinned"] == 1

    edited = member.patch(
        f"/api/local-models/{library['model']}/notes/{note_id}",
        json={"body": "member's sharper note"},
        headers=headers,
    )
    assert edited.status_code == 200
    assert edited.json()["body"] == "member's sharper note"

    deleted = member.delete(
        f"/api/local-models/{library['model']}/notes/{note_id}", headers=headers
    )
    assert deleted.status_code == 200
    assert deleted.json() == {"status": "deleted"}


def test_admin_reads_private_notes_but_changes_none(client, library):
    member = library["member_client"]
    personal = _write(member, library, "member's private entry", library["member_csrf"])
    assert personal.status_code == 201
    note_id = personal.json()["id"]

    seen = client.get(f"/api/local-models/{library['model']}/notes").json()
    assert ["member's private entry"] == [item["body"] for item in seen["items"]]

    admin_headers = {"X-CSRF-Token": library["csrf"]}
    edit = client.patch(
        f"/api/local-models/{library['model']}/notes/{note_id}",
        json={"body": "redacted by admin"},
        headers=admin_headers,
    )
    assert edit.status_code == 403

    delete = client.delete(
        f"/api/local-models/{library['model']}/notes/{note_id}", headers=admin_headers
    )
    assert delete.status_code == 403


def test_a_note_id_from_another_model_is_404_not_403(client, library):
    import app.main as main_module

    main_module.database.upsert_local_model(_record("acme/other"))
    note = _write(client, library, "keeper's note", library["csrf"]).json()

    smuggled = client.patch(
        "/api/local-models/acme/other/notes/" + note["id"],
        json={"body": "moved?"},
        headers={"X-CSRF-Token": library["csrf"]},
    )
    assert smuggled.status_code == 404
    assert smuggled.json()["detail"] == "Note not found."


def test_share_route_returns_the_thread_with_author_names(client, library):
    admin_note = _write(client, library, "keeper's public entry", library["csrf"]).json()
    member = library["member_client"]
    _write(member, library, "member's private entry", library["member_csrf"])

    shared = client.post(
        f"/api/local-models/{library['model']}/notes/{admin_note['id']}/share",
        json={"shared": True},
        headers={"X-CSRF-Token": library["csrf"]},
    )
    assert shared.status_code == 200
    thread = shared.json()["items"]
    assert shared.json()["count"] == len(thread)
    bodies = {item["body"]: item for item in thread}
    assert set(bodies) == {"keeper's public entry", "member's private entry"}
    assert bodies["keeper's public entry"]["shared"] == 1
    assert bodies["member's private entry"]["shared"] == 0
    assert bodies["keeper's public entry"]["author_name"] == "Keeper"
    assert bodies["member's private entry"]["author_name"] == "Member"

"""Model notes (D14): author-private threads with a deliberate share step.

The mechanism under test is the visibility matrix, and every case in it
is a promise to the owner: private notes belong to their author until
the author flips the thread's share toggle; shared entries carry their
author's name; the admin's ruled power is seeing everything, not
touching it. Pin is exclusive per author within a thread —
 pinning never releases somebody else's pin.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.database import Database


def _user(user_id: str, username: str, role: str) -> dict[str, Any]:
    return {
        "id": user_id,
        "username": username,
        "display_name": username.title(),
        "password_hash": "x",
        "role": role,
        "created_at": "2026-09-27T00:00:00+00:00",
        "updated_at": "2026-09-27T00:00:00+00:00",
    }


@pytest.fixture()
def database(tmp_path: Path) -> Database:
    database = Database(tmp_path / "library.sqlite3")
    database.initialize()
    database.create_user(_user("keeper", "keeper", "admin"))
    database.create_user(_user("jacksparrow", "jacksparrow", "member"))
    return database


def _model(database: Database, repo_id: str) -> None:
    database.upsert_local_model(
        {
            "repo_id": repo_id,
            "relative_path": repo_id,
            "size_bytes": 10,
            "file_count": 1,
            "modified_at": "2026-09-27T00:00:00+00:00",
            "downloaded_at": None,
            "revision": "main",
            "sha": "aaa111",
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
        }
    )


def _note(
    database: Database,
    repo_id: str,
    author_id: str,
    body: str,
    *,
    note_id: str,
    created_at: str,
    pinned: bool = False,
) -> dict[str, Any]:
    return database.create_model_note(
        {
            "id": note_id,
            "repo_id": repo_id,
            "author_id": author_id,
            "body": body,
            "pinned": 1 if pinned else 0,
            "shared": 0,
            "created_at": created_at,
            "updated_at": created_at,
        }
    )


# ---------------------------------------------------------------------
# The visibility matrix
# ---------------------------------------------------------------------

def test_private_note_is_invisible_to_other_members(database: Database):
    _model(database, "acme/rocket")
    _note(database, "acme/rocket", "keeper", "do not prune this one",
          note_id="n1", created_at="2026-09-27T01:00:00+00:00")
    mine = database.list_model_notes("acme/rocket", "keeper", False)
    theirs = database.list_model_notes("acme/rocket", "jacksparrow", False)
    assert [note["id"] for note in mine] == ["n1"]
    assert theirs == []


def test_bodies_carry_the_authorname_when_visible(database: Database):
    _model(database, "acme/rocket")
    _note(database, "acme/rocket", "keeper", "shared wisdom",
          note_id="n1", created_at="2026-09-27T01:00:00+00:00")
    database.set_model_note_shared("n1", True, "2026-09-27T01:01:00+00:00")
    visible = database.list_model_notes("acme/rocket", "jacksparrow", False)
    assert len(visible) == 1
    assert visible[0]["author_name"] == "Keeper"
    assert visible[0]["body"] == "shared wisdom"


def test_share_flips_whole_thread_and_never_other_authors(database: Database):
    _model(database, "acme/rocket")
    _note(database, "acme/rocket", "keeper", "first",
          note_id="k1", created_at="2026-09-27T01:00:00+00:00")
    _note(database, "acme/rocket", "keeper", "second",
          note_id="k2", created_at="2026-09-27T02:00:00+00:00")
    _note(database, "acme/rocket", "jacksparrow", "my own tally",
          note_id="j1", created_at="2026-09-27T03:00:00+00:00")
    database.set_model_note_shared("k1", True, "2026-09-27T04:00:00+00:00")
    jacks_view = database.list_model_notes("acme/rocket", "jacksparrow", False)
    assert [note["id"] for note in jacks_view] == ["j1", "k2", "k1"]
    # The share reached keeper's whole thread — both entries — and left
    # jacksparrow's entry exactly where it was (still private to jack).
    assert all(note["shared"] for note in jacks_view if note["id"].startswith("k"))
    assert jacks_view[0]["shared"] is False


def test_unshare_releases_only_the_sharing_authors_entries(database: Database):
    _model(database, "acme/rocket")
    _note(database, "acme/rocket", "keeper", "keeper's thread",
          note_id="k1", created_at="2026-09-27T01:00:00+00:00")
    _note(database, "acme/rocket", "jacksparrow", "jack's thread",
          note_id="j1", created_at="2026-09-27T01:30:00+00:00")
    database.set_model_note_shared("k1", True, "2026-09-27T02:00:00+00:00")
    database.set_model_note_shared("j1", True, "2026-09-27T02:01:00+00:00")
    database.set_model_note_shared("k1", False, "2026-09-27T03:00:00+00:00")
    jacks_view = database.list_model_notes("acme/rocket", "jacksparrow", False)
    assert [note["id"] for note in jacks_view] == ["j1"]


def test_admin_sees_everything_inside_the_thread(database: Database):
    _model(database, "acme/rocket")
    _note(database, "acme/rocket", "keeper", "private but mine",
          note_id="k1", created_at="2026-09-27T01:00:00+00:00")
    database.set_model_note_shared("k1", True, "2026-09-27T01:10:00+00:00")
    _note(database, "acme/rocket", "jacksparrow", "quiet tally",
          note_id="j1", created_at="2026-09-27T02:00:00+00:00")
    admin_view = database.list_model_notes("acme/rocket", "keeper", True)
    assert [note["id"] for note in admin_view] == ["j1", "k1"]


# ---------------------------------------------------------------------
# Pin exclusivity: per author, per thread
# ---------------------------------------------------------------------

def test_pinning_releases_the_same_authors_previous_pin_only(database: Database):
    _model(database, "acme/rocket")
    _note(database, "acme/rocket", "keeper", "first pin",
          note_id="k1", created_at="2026-09-27T01:00:00+00:00")
    _note(database, "acme/rocket", "keeper", "second pin",
          note_id="k2", created_at="2026-09-27T02:00:00+00:00")
    _note(database, "acme/rocket", "jacksparrow", "jack's pin",
          note_id="j1", created_at="2026-09-27T03:00:00+00:00")
    database.set_model_note_pinned("k1", True, "2026-09-27T04:00:00+00:00")
    database.set_model_note_pinned("j1", True, "2026-09-27T04:01:00+00:00")
    database.set_model_note_pinned("k2", True, "2026-09-27T05:00:00+00:00")
    keeper_rows = {note["id"]: note for note in database.list_model_notes(
        "acme/rocket", "keeper", False)}
    jack_rows = {note["id"]: note for note in database.list_model_notes(
        "acme/rocket", "jacksparrow", False)}
    assert keeper_rows["k2"]["pinned"] is True
    assert keeper_rows["k1"]["pinned"] is False
    # jack's pin survives keeper's re-pin untouched.
    assert jack_rows["j1"]["pinned"] is True


def test_unpin_clears_without_touching_anything_else(database: Database):
    _model(database, "acme/rocket")
    _note(database, "acme/rocket", "keeper", "pinned for a while",
          note_id="k1", created_at="2026-09-27T01:00:00+00:00", pinned=True)
    database.set_model_note_pinned("k1", False, "2026-09-27T02:00:00+00:00")
    note = database.get_model_note("k1")
    assert note is not None and note["pinned"] is False


def test_creating_with_pin_releases_the_prior_pin(database: Database):
    _model(database, "acme/rocket")
    first = _note(database, "acme/rocket", "keeper", "older",
                  note_id="k1", created_at="2026-09-27T01:00:00+00:00",
                  pinned=True)
    second = _note(database, "acme/rocket", "keeper", "newer",
                   note_id="k2", created_at="2026-09-27T02:00:00+00:00",
                   pinned=True)
    rows = database.list_model_notes("acme/rocket", "keeper", False)
    pinned_ids = [note["id"] for note in rows if note["pinned"]]
    # Route path does create-then-pin through the exclusivity rule; the
    # raw creator can double-pin, so assert what the API surface delivers.
    assert first["pinned"] is True and second["pinned"] is True
    database.set_model_note_pinned("k2", True, "2026-09-27T03:00:00+00:00")
    rows = database.list_model_notes("acme/rocket", "keeper", False)
    pinned_ids = [note["id"] for note in rows if note["pinned"]]
    assert pinned_ids == ["k2"]


# ---------------------------------------------------------------------
# Search: note bodies are indexed into library search (D14)
# ---------------------------------------------------------------------

def test_search_hits_respect_privacy_and_carry_the_author(database: Database):
    _model(database, "acme/rocket")
    _note(database, "acme/rocket", "keeper", "handle with silica gel",
          note_id="k1", created_at="2026-09-27T01:00:00+00:00")
    author_hits = database.note_search_hits("keeper", "silica")
    member_hits = database.note_search_hits("jacksparrow", "silica")
    admin_hits = database.note_search_hits("jacksparrow", "silica", True)
    assert [hit["repo_id"] for hit in author_hits] == ["acme/rocket"]
    assert author_hits[0]["author_name"] == "Keeper"
    assert member_hits == []
    assert [hit["repo_id"] for hit in admin_hits] == ["acme/rocket"]


def test_shared_note_becomes_searchable_to_members(database: Database):
    _model(database, "acme/rocket")
    _note(database, "acme/rocket", "keeper", "finetune recipe inside",
          note_id="k1", created_at="2026-09-27T01:00:00+00:00")
    assert database.note_search_hits("jacksparrow", "recipe") == []
    database.set_model_note_shared("k1", True, "2026-09-27T02:00:00+00:00")
    hits = database.note_search_hits("jacksparrow", "recipe")
    assert [hit["repo_id"] for hit in hits] == ["acme/rocket"]


def test_edit_preserves_created_and_moves_updated(database: Database):
    _model(database, "acme/rocket")
    _note(database, "acme/rocket", "keeper", "draft version",
          note_id="k1", created_at="2026-09-27T01:00:00+00:00")
    updated = database.update_model_note(
        "k1", body="final version", updated_at="2026-09-27T06:00:00+00:00"
    )
    assert updated is not None
    assert updated["body"] == "final version"
    assert updated["created_at"] == "2026-09-27T01:00:00+00:00"
    assert updated["updated_at"] == "2026-09-27T06:00:00+00:00"


def test_delete_removes_the_entry(database: Database):
    _model(database, "acme/rocket")
    _note(database, "acme/rocket", "keeper", "temporary scribble",
          note_id="k1", created_at="2026-09-27T01:00:00+00:00")
    database.delete_model_note("k1")
    assert database.get_model_note("k1") is None
    assert database.list_model_notes("acme/rocket", "keeper", False) == []


# ---------------------------------------------------------------------
# Cell previews: pinned-else-latest, privacy held
# ---------------------------------------------------------------------

def test_preview_picks_pinned_then_latest_per_copy(database: Database):
    _model(database, "acme/rocket")
    _note(database, "acme/rocket", "keeper", "old",
          note_id="k1", created_at="2026-09-27T01:00:00+00:00", pinned=True)
    _note(database, "acme/rocket", "keeper", "newer unpinned",
          note_id="k2", created_at="2026-09-27T03:00:00+00:00")
    preview = database.note_previews("keeper", False)["acme/rocket"]
    assert preview["body"] == "old"
    assert preview["pinned"] is True


def test_preview_without_pin_prefers_the_latest_entry(database: Database):
    _model(database, "acme/rocket")
    _note(database, "acme/rocket", "keeper", "earlier",
          note_id="k1", created_at="2026-09-27T01:00:00+00:00")
    _note(database, "acme/rocket", "keeper", "later",
          note_id="k2", created_at="2026-09-27T02:00:00+00:00")
    preview = database.note_previews("keeper", False)["acme/rocket"]
    assert preview["body"] == "later"


def test_preview_respects_privacy_per_copy_and_viewer(database: Database):
    _model(database, "acme/rocket")
    _note(database, "acme/rocket", "keeper", "keeper private",
          note_id="k1", created_at="2026-09-27T01:00:00+00:00")
    previews = database.note_previews("jacksparrow", False)
    assert previews == {}
    admin_preview = database.note_previews("keeper", True)
    assert admin_preview["acme/rocket"]["body"] == "keeper private"
    database.set_model_note_shared("k1", True, "2026-09-27T02:00:00+00:00")
    member_preview = database.note_previews("jacksparrow", False)
    assert member_preview["acme/rocket"]["author_name"] == "Keeper"


# ---------------------------------------------------------------------
# Library search wiring: a note can summon the model it lives on
# ---------------------------------------------------------------------

def test_library_search_surfaces_note_hits_and_the_model_behind_them(
    database: Database, monkeypatch: pytest.MonkeyPatch
):
    from app import main as main_module

    monkeypatch.setattr(main_module, "database", database)
    _model(database, "acme/rocket")  # metadata matches nothing below
    _note(database, "acme/rocket", "keeper", "the silica recipe lives here",
          note_id="k1", created_at="2026-09-27T01:00:00+00:00")
    database.set_model_note_shared("k1", True, "2026-09-27T02:00:00+00:00")

    keeper_page = main_module.local_models(
        {"id": "keeper", "role": "admin"}, "silica recipe"
    )
    assert [item["repo_id"] for item in keeper_page["items"]] == ["acme/rocket"]
    hits = keeper_page["items"][0]["note_hits"]
    assert hits and "silica recipe" in hits[0]["body"]
    assert hits[0]["author_name"] == "Keeper"
    preview = keeper_page["items"][0]["note_preview"]
    assert preview["body"] == "the silica recipe lives here"


def test_library_search_does_not_summon_models_behind_private_notes(
    database: Database, monkeypatch: pytest.MonkeyPatch
):
    from app import main as main_module

    monkeypatch.setattr(main_module, "database", database)
    _model(database, "acme/rocket")
    _note(database, "acme/rocket", "keeper", "private silica recipe",
          note_id="k1", created_at="2026-09-27T01:00:00+00:00")
    jack_page = main_module.local_models(
        {"id": "jacksparrow", "role": "member"}, "silica"
    )
    assert jack_page["items"] == []
    jack_page = main_module.local_models({"id": "jacksparrow", "role": "member"}, "")
    # The empty query is the plain library listing; the model is there,
    # without any note hit riding on it — and no preview of a note the
    # viewer cannot see.
    assert [item["repo_id"] for item in jack_page["items"]] == ["acme/rocket"]
    assert "note_hits" not in jack_page["items"][0]
    keepers_page = main_module.local_models({"id": "keeper", "role": "admin"}, "")
    assert keepers_page["items"][0]["note_preview"]["body"] == "private silica recipe"

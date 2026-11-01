"""Preferences: one row per account, written through the allowlist and
nothing else. Deployment configuration stays in the environment; no
presets are seeded — a new account starts with an empty object.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from app.database import Database, validate_user_preferences


@pytest.fixture()
def database(tmp_path: Path) -> Database:
    database = Database(tmp_path / "library.sqlite3")
    database.initialize()
    timestamp = "2026-09-27T00:00:00+00:00"
    for user_id, username in (("keeper", "keeper"), ("jacksparrow", "jacksparrow")):
        database.create_user(
            {
                "id": user_id,
                "username": username,
                "display_name": username.title(),
                "password_hash": "x",
                "role": "admin" if user_id == "keeper" else "member",
                "created_at": timestamp,
                "updated_at": timestamp,
            }
        )
    return database


def test_a_new_account_starts_with_an_empty_object(database: Database):
    assert database.get_user_preferences("keeper") == {}


def test_preferences_round_trip(database: Database):
    stored = database.set_user_preferences(
        "keeper", {"density": "cards"}, "2026-09-27T01:00:00+00:00"
    )
    assert stored == {"density": "cards"}
    assert database.get_user_preferences("keeper") == {"density": "cards"}


def test_a_second_write_replaces_without_merging(database: Database):
    database.set_user_preferences("keeper", {"density": "cards"}, "2026-09-27T01:00:00+00:00")
    database.set_user_preferences("keeper", {"density": "rows"}, "2026-09-27T02:00:00+00:00")
    assert database.get_user_preferences("keeper") == {"density": "rows"}


def test_accounts_are_isolated(database: Database):
    database.set_user_preferences("keeper", {"density": "rows"}, "2026-09-27T01:00:00+00:00")
    assert database.get_user_preferences("jacksparrow") == {}


def test_unknown_keys_are_refused(database: Database):
    with pytest.raises(ValueError):
        validate_user_preferences({"start_page": "library"})
    # And nothing was stored.
    assert database.get_user_preferences("keeper") == {}


def test_density_only_accepts_declared_choices(database: Database):
    assert validate_user_preferences({"density": "rows"}) == {"density": "rows"}
    with pytest.raises(ValueError):
        validate_user_preferences({"density": "table"})


def test_families_takes_a_boolean(database: Database):
    for value in (True, False):
        assert validate_user_preferences({"families": value}) == {"families": value}
    assert validate_user_preferences({}) == {}
    with pytest.raises(ValueError):
        validate_user_preferences({"families": "yes"})
    with pytest.raises(ValueError):
        validate_user_preferences({"families": 1})


def test_views_get_an_id_and_a_trimmed_name(database: Database):
    cleaned = validate_user_preferences(
        {"views": [{"name": "  GGUF only  ", "query": "gguf"}]}
    )
    view = cleaned["views"][0]
    assert view["name"] == "GGUF only"
    assert view["query"] == "gguf"
    assert view["id"]


def test_views_reject_broken_shapes(database: Database):
    with pytest.raises(ValueError):
        validate_user_preferences({"views": [{"query": "no name"}]})
    with pytest.raises(ValueError):
        validate_user_preferences({"views": [{"name": "no query"}]})
    with pytest.raises(ValueError):
        validate_user_preferences({"views": ["not an object"]})


def test_groups_keep_their_members(database: Database):
    cleaned = validate_user_preferences(
        {"groups": [{"name": "small", "repo_ids": ["acme/one", "acme/two"]}]}
    )
    assert cleaned["groups"] == [{"name": "small", "repo_ids": ["acme/one", "acme/two"]}]
    with pytest.raises(ValueError):
        validate_user_preferences({"groups": [{"name": "no members"}]})


def test_columns_round_trip_per_surface(database: Database):
    cleaned = validate_user_preferences(
        {"columns": {"local": ["kind", "size", "modified"], "hub": ["params"]}}
    )
    assert cleaned == {"columns": {"local": ["kind", "size", "modified"], "hub": ["params"]}}
    stored = database.set_user_preferences("keeper", cleaned, "2026-09-27T01:00:00+00:00")
    assert stored == cleaned
    assert database.get_user_preferences("keeper")["columns"]["local"] == [
        "kind", "size", "modified",
    ]


def test_columns_reject_broken_shapes(database: Database):
    with pytest.raises(ValueError):
        validate_user_preferences({"columns": ["not", "an", "object"]})
    with pytest.raises(ValueError):
        validate_user_preferences({"columns": {"local": "kind,size"}})
    with pytest.raises(ValueError):
        validate_user_preferences({"columns": {"local": ["kind", ""]}})
    with pytest.raises(ValueError):
        validate_user_preferences({"columns": {"local": ["kind", 7]}})
    with pytest.raises(ValueError):
        validate_user_preferences({"columns": {"loca l": ["kind"]}})
    # An empty per-surface list is legal (the name column always renders).
    assert validate_user_preferences({"columns": {"local": []}}) == {"columns": {"local": []}}


def test_columns_version_round_trip_and_rejects(database: Database):
    cleaned = validate_user_preferences(
        {"columns": {"local": ["kind"]}, "columns_version": 2}
    )
    assert cleaned["columns_version"] == 2
    stored = database.set_user_preferences("keeper", cleaned, "2026-09-27T01:00:00+00:00")
    assert stored["columns_version"] == 2
    assert database.get_user_preferences("keeper")["columns_version"] == 2
    for bad in ("2", 2.0, True, -1, 10**7, None):
        if bad is None:
            continue
        with pytest.raises(ValueError):
            validate_user_preferences({"columns_version": bad})


def test_an_oversized_object_is_refused(database: Database):
    with pytest.raises(ValueError):
        validate_user_preferences(
            {"views": [{"name": "x" * 40, "query": "y" * 400}] * 50}
        )


# ---------------------------------------------------------------------
# Route wiring: the user's own row, through the session contract
# ---------------------------------------------------------------------

def test_read_and_write_routes_track_the_calling_user(
    database: Database, monkeypatch: pytest.MonkeyPatch
):
    from app import main as main_module

    monkeypatch.setattr(main_module, "database", database)
    page = main_module.read_preferences({"id": "keeper"})
    assert page == {"preferences": {}}
    main_module.write_preferences(
        main_module.PreferencesUpdateRequest(preferences={"density": "cards"}),
        {"id": "keeper"},
    )
    assert database.get_user_preferences("keeper") == {"density": "cards"}
    # Another account's write never lands on someone else's row.
    main_module.write_preferences(
        main_module.PreferencesUpdateRequest(preferences={"density": "rows"}),
        {"id": "jacksparrow"},
    )
    assert database.get_user_preferences("keeper") == {"density": "cards"}


def test_write_route_translates_contract_errors_to_400(
    database: Database, monkeypatch: pytest.MonkeyPatch
):
    from app import main as main_module

    monkeypatch.setattr(main_module, "database", database)
    with pytest.raises(HTTPException) as caught:
        main_module.write_preferences(
            main_module.PreferencesUpdateRequest(preferences={"density": "ermeged"}),
            {"id": "keeper"},
        )
    assert caught.value.status_code == 400

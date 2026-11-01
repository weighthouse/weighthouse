"""Library query: the facet rail counts over what this viewer can see,
narrowed by the search text; favorited and watching are the only
built-in filter concepts. Sorting is server-side over a declared column
whitelist; nothing user-controlled reaches the SQL as text.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.database import Database


@pytest.fixture()
def database(tmp_path: Path) -> Database:
    database = Database(tmp_path / "library.sqlite3")
    database.initialize()
    timestamp = "2026-09-27T00:00:00+00:00"
    for user_id, username, role in (
        ("keeper", "keeper", "admin"),
        ("jacksparrow", "jacksparrow", "member"),
    ):
        database.create_user(
            {
                "id": user_id,
                "username": username,
                "display_name": username.title(),
                "password_hash": "x",
                "role": role,
                "created_at": timestamp,
                "updated_at": timestamp,
            }
        )
    return database


def _model(
    database: Database,
    repo_id: str,
    *,
    size_bytes: int = 100,
    pipeline_tag: str = "text-generation",
    library_name: str = "transformers",
    location: str | None = None,
    storage_backend: str = "filesystem",
) -> None:
    database.upsert_local_model(
        {
            "repo_id": repo_id,
            "relative_path": repo_id,
            "size_bytes": size_bytes,
            "file_count": 2,
            "modified_at": "2026-09-27T00:00:00+00:00",
            "downloaded_at": None,
            "revision": "main",
            "sha": "aaa111",
            "pipeline_tag": pipeline_tag,
            "library_name": library_name,
            "license": "mit",
            "tags_json": "[]",
            "config_json": "{}",
            "source_url": None,
            "managed": 1,
            "storage_backend": storage_backend,
            "cached": 1,
            "remote_uri": None,
            **({"location": location} if location else {}),
        }
    )


def _seed(database: Database) -> None:
    _model(database, "acme/large", size_bytes=900, pipeline_tag="text-generation")
    _model(database, "acme/small", size_bytes=100, pipeline_tag="text-generation")
    _model(database, "acme/vision", size_bytes=500, pipeline_tag="image-classification",
           library_name="timm")
    _model(database, "acme/offsite", size_bytes=300, storage_backend="s3")
    database.save_model(
        {
            "id": "saved-1",
            "user_id": "keeper",
            "repo_id": "acme/small",
            "note": "",
            "metadata_json": "{}",
            "created_at": "2026-09-27T00:00:00+00:00",
            "updated_at": "2026-09-27T00:00:00+00:00",
        },
        [],
    )
    database.create_watched_model(
        {
            "id": "watch-1",
            "user_id": "keeper",
            "repo_id": "acme/offsite",
            "metadata_json": "{}",
            "last_seen_sha": "aaa111",
            "upstream_sha": "aaa111",
            "changed_at": None,
            "checked_at": None,
            "created_at": "2026-09-27T00:00:00+00:00",
            "updated_at": "2026-09-27T00:00:00+00:00",
        }
    )


def test_sort_orders_over_a_declared_whitelist(database: Database):
    _seed(database)
    ascending = [m["repo_id"] for m in database.query_visible_local_models(
        "keeper", sort="size_bytes", order="asc")]
    descending = [m["repo_id"] for m in database.query_visible_local_models(
        "keeper", sort="size_bytes", order="desc")]
    assert ascending == ["acme/small", "acme/offsite", "acme/vision", "acme/large"]
    assert descending == list(reversed(ascending))
    # The default stays the one the library has always had: modified_at
    # first (all equal here), repo_id breaking each tie in ascending order.
    default = database.query_visible_local_models("keeper")
    assert [m["repo_id"] for m in default] == [
        "acme/large", "acme/offsite", "acme/small", "acme/vision",
    ]


def test_unknown_sort_or_order_is_refused(database: Database):
    _seed(database)
    with pytest.raises(ValueError):
        database.query_visible_local_models("keeper", sort="rank")
    with pytest.raises(ValueError):
        database.query_visible_local_models("keeper", order="sideways")
    with pytest.raises(ValueError):
        database.query_visible_local_models("keeper", order="")


def test_favorited_and_watching_are_the_built_in_filters(database: Database):
    _seed(database)
    favorited = [m["repo_id"] for m in database.query_visible_local_models(
        "keeper", favorited=True)]
    watching = [m["repo_id"] for m in database.query_visible_local_models(
        "keeper", watching=True)]
    assert favorited == ["acme/small"]
    assert watching == ["acme/offsite"]
    # Another viewer's marks are their own.
    assert database.query_visible_local_models(
        "jacksparrow", favorited=True) == []


def test_facet_rail_is_computed_from_the_library(database: Database):
    _seed(database)
    facets = database.visible_local_model_facets("keeper")
    assert facets["total"] == 4
    assert facets["favorited"] == 1
    assert facets["watching"] == 1
    task_counts = {entry["value"]: entry["count"] for entry in facets["tasks"]}
    assert task_counts == {"text-generation": 3, "image-classification": 1}
    library_counts = {entry["value"]: entry["count"] for entry in facets["libraries"]}
    assert library_counts == {"transformers": 3, "timm": 1}
    storage_counts = {entry["value"]: entry["count"] for entry in facets["storage"]}
    assert storage_counts == {"filesystem": 3, "s3": 1}
    location_counts = {entry["value"]: entry["count"] for entry in facets["locations"]}
    assert location_counts == {"primary": 4}


def test_facet_counts_follow_the_query_not_the_filters(database: Database):
    _seed(database)
    facets = database.visible_local_model_facets("keeper", "acme/small")
    assert facets["total"] == 1
    assert facets["favorited"] == 1
    assert facets["watching"] == 0

def test_filters_combine_with_each_other_and_the_query(database: Database):
    _seed(database)
    combined = [m["repo_id"] for m in database.query_visible_local_models(
        "keeper", query="small", favorited=True)]
    assert combined == ["acme/small"]
    # Case-insensitive task facet picks.
    vision = [m["repo_id"] for m in database.query_visible_local_models(
        "keeper", task="Image-Classification")]
    assert vision == ["acme/vision"]
    storage = [m["repo_id"] for m in database.query_visible_local_models(
        "keeper", storage="s3")]
    assert storage == ["acme/offsite"]


def test_missing_location_counts_as_primary_for_filtering(database: Database):
    _seed(database)
    _model(database, "acme/shelf", location="shelf")
    primary = [m["repo_id"] for m in database.query_visible_local_models(
        "keeper", location="primary")]
    shelf = [m["repo_id"] for m in database.query_visible_local_models(
        "keeper", location="shelf")]
    assert "acme/shelf" not in primary
    assert shelf == ["acme/shelf"]


# ---------------------------------------------------------------------
# Route wiring: facets ride the same response as the rows
# ---------------------------------------------------------------------

def test_route_returns_facets_and_rejects_unknown_sorts(
    database: Database, monkeypatch: pytest.MonkeyPatch
):
    from app import main as main_module

    monkeypatch.setattr(main_module, "database", database)
    _seed(database)
    page = main_module.local_models(
        {"id": "keeper", "role": "admin"}, query="", sort="size_bytes", order="asc"
    )
    assert [item["repo_id"] for item in page["items"]] == [
        "acme/small", "acme/offsite", "acme/vision", "acme/large",
    ]
    assert page["facets"]["favorited"] == 1
    with pytest.raises(HTTPException) as caught:
        main_module.local_models(
            {"id": "keeper", "role": "admin"}, query="", sort="rank", order="asc"
        )
    assert caught.value.status_code == 400

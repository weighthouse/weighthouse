"""Route-wall coverage for the endpoints no test had ever touched.

A first pass over the attested route table (test_routes.py) and the rest of
the suite showed fifteen endpoints with zero route-level tests: the saved
model and collection surface (views and groups), the hub detail and GGUF
range proxy, the single-copy fetch plus its S3-only lifecycle actions
(restore, cache eviction, moves), the manifest verify and sha-pinned card
reads, the watch acknowledge, and the runtime job fetch. The logic behind
most of these was tested at its module, but the branch that turns a module
error into the right status and detail — and the visibility and ownership
rules around it — was never exercised through the app. That wall is what
this file pins.
"""

from __future__ import annotations

import dataclasses

import app.main as main_module
from app.manifests import write_manifest
from app.watch import ensure_watch

MOMENT = "2026-09-26T00:00:00+00:00"


def _record(repo_id: str, **overrides) -> dict:
    """The row shape upsert_local_model requires; overrides win."""
    record = {
        "repo_id": repo_id,
        "relative_path": repo_id,
        "size_bytes": 100,
        "file_count": 1,
        "modified_at": MOMENT,
        "downloaded_at": MOMENT,
        "revision": "main",
        "sha": "base-1",
        "pipeline_tag": "text-generation",
        "library_name": "transformers",
        "license": "mit",
        "tags_json": "[]",
        "config_json": "{}",
        "source_url": None,
        "managed": 1,
        "storage_backend": "filesystem",
        "cached": 1,
        "remote_uri": None,
        "location": "primary",
    }
    record.update(overrides)
    return record


def _seed(client, repo_id: str, **overrides) -> None:
    main_module.database.upsert_local_model(_record(repo_id, **overrides))


def _primary_root() -> "object":
    return main_module.locations[0].root


# ---------------------------------------------------------------------------
# Saved models: the per-user Saved fact and its collection membership.

def test_saved_models_round_trip_with_note_and_metadata_allowlist(client, sign_in):
    saved = client.post(
        "/api/saved-models",
        json={
            "repo_id": "acme/tiny",
            "note": "eval baseline",
            "metadata": {
                "author": "acme",
                "pipeline_tag": "text-generation",
                "parameter_count": 8_000_000_000,
                "injected": "must not survive",
            },
        },
        headers={"X-CSRF-Token": sign_in},
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["repo_id"] == "acme/tiny"
    assert body["note"] == "eval baseline"
    assert body["metadata"]["parameter_count"] == 8_000_000_000
    assert "injected" not in body["metadata"]

    listed = client.get("/api/saved-models").json()
    assert listed["count"] == 1
    assert listed["items"][0]["repo_id"] == "acme/tiny"

    # Saving the same repo again updates in place, not a second row.
    again = client.post(
        "/api/saved-models",
        json={"repo_id": "acme/tiny", "note": "re-evaluated"},
        headers={"X-CSRF-Token": sign_in},
    )
    assert again.status_code == 200
    assert client.get("/api/saved-models").json()["count"] == 1
    assert again.json()["note"] == "re-evaluated"


def test_saved_models_query_filters_and_unsave_is_404_when_absent(client, sign_in):
    headers = {"X-CSRF-Token": sign_in}
    for repo_id in ("acme/alpha", "acme/beta"):
        client.post("/api/saved-models", json={"repo_id": repo_id}, headers=headers)

    assert client.get("/api/saved-models?query=alpha").json()["count"] == 1
    assert client.get("/api/saved-models?query=gamma").json()["count"] == 0

    gone = client.delete("/api/saved-models/acme/alpha", headers=headers)
    assert gone.status_code == 200
    assert gone.json() == {"status": "removed"}
    assert client.delete("/api/saved-models/acme/alpha", headers=headers).status_code == 404


def test_saved_model_metadata_over_the_cap_is_rejected(client, sign_in):
    big = client.post(
        "/api/saved-models",
        json={
            "repo_id": "acme/big",
            "metadata": {"author": "acme", "pipeline_tag": "x" * 20_000},
        },
        headers={"X-CSRF-Token": sign_in},
    )
    assert big.status_code == 400
    assert big.json()["detail"] == "Saved model metadata is too large."


def test_saved_models_are_per_user(client, sign_in):
    created = client.post(
        "/api/users",
        json={"username": "member", "display_name": "Member", "password": "longenough1"},
        headers={"X-CSRF-Token": sign_in},
    )
    member = client.__class__(main_module.app)
    member_csrf = member.post(
        "/api/auth/login",
        json={"username": "member", "password": "longenough1"},
    ).json()["csrf_token"]

    client.post(
        "/api/saved-models",
        json={"repo_id": "acme/keeper-only"},
        headers={"X-CSRF-Token": sign_in},
    )
    assert member.get("/api/saved-models").json()["count"] == 0
    assert client.get("/api/saved-models").json()["count"] == 1


# ---------------------------------------------------------------------------
# Collections (views and groups): name rules, ownership, membership.

def test_collection_lifecycle_and_duplicate_name_is_409(client, sign_in):
    headers = {"X-CSRF-Token": sign_in}

    blank = client.post("/api/collections", json={"name": "   "}, headers=headers)
    assert blank.status_code == 400

    first = client.post(
        "/api/collections",
        json={"name": "eval set", "description": "the good ones"},
        headers=headers,
    )
    assert first.status_code == 201, first.text
    collection_id = first.json()["id"]

    duplicate = client.post(
        "/api/collections", json={"name": "eval set"}, headers=headers
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "You already have a collection with that name."

    listed = client.get("/api/collections").json()["items"]
    assert [item["name"] for item in listed] == ["eval set"]

    # A saved model joins and leaves the collection.
    client.post(
        "/api/saved-models",
        json={"repo_id": "acme/tiny", "collection_ids": [collection_id]},
        headers=headers,
    )
    in_collection = client.get(
        f"/api/saved-models?collection_id={collection_id}"
    ).json()
    assert in_collection["count"] == 1
    assert in_collection["items"][0]["collections"] == [collection_id]

    deleted = client.delete(f"/api/collections/{collection_id}", headers=headers)
    assert deleted.status_code == 200
    assert client.get("/api/collections").json()["items"] == []
    assert client.delete(f"/api/collections/{collection_id}", headers=headers).status_code == 404


def test_collections_are_per_user(client, sign_in):
    client.post(
        "/api/users",
        json={"username": "member", "display_name": "Member", "password": "longenough1"},
        headers={"X-CSRF-Token": sign_in},
    )
    member = client.__class__(main_module.app)
    member_csrf = member.post(
        "/api/auth/login",
        json={"username": "member", "password": "longenough1"},
    ).json()["csrf_token"]

    client.post(
        "/api/collections", json={"name": "keeper view"}, headers={"X-CSRF-Token": sign_in}
    )
    assert member.get("/api/collections").json()["items"] == []
    assert len(client.get("/api/collections").json()["items"]) == 1


# ---------------------------------------------------------------------------
# Hub detail + GGUF range: upstream proxying with its error ladder.

def test_hub_model_detail_merges_local_and_saved_flags(client, sign_in, monkeypatch):
    _seed(client, "acme/stored")
    monkeypatch.setattr(
        main_module.hub,
        "model_details",
        lambda repo_id, revision="main": {
            "id": repo_id,
            "author": "acme",
            "revision": revision,
            "files": [{"path": "model.safetensors", "size": 100, "blob_id": "b"}],
            "total_bytes": 100,
        },
    )
    monkeypatch.setattr(main_module.hub, "read_model_card", lambda repo_id, revision="main": "a card")

    detail = client.get("/api/hub/models/acme/stored")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["id"] == "acme/stored"
    assert body["model_card"] == "a card"
    assert body["local"] is True   # a copy exists
    assert body["saved"] is False  # nobody saved it yet

    client.post(
        "/api/saved-models", json={"repo_id": "acme/stored"},
        headers={"X-CSRF-Token": sign_in},
    )
    assert client.get("/api/hub/models/acme/stored").json()["saved"] is True


def test_hub_model_detail_validation_and_upstream_errors(client, sign_in, monkeypatch):
    # Path traversal in the repo id is rejected at validation, before any
    # upstream call.
    bad = client.get("/api/hub/models/acme/../etc")
    assert bad.status_code == 400
    assert "owner/model-name" in bad.json()["detail"]

    # Upstream failures answer with a stable, house-written message. The
    # library's raw exception text (endpoint URL, provider request id) is
    # for the server log, not the client.
    def boom(repo_id, revision="main"):
        raise RuntimeError(
            "404, url='https://huggingface.co/api/models/acme/stored' "
            "(Request ID: 0a1b2c3d-4e5f)"
        )

    monkeypatch.setattr(main_module.hub, "model_details", boom)
    down = client.get("/api/hub/models/acme/stored")
    assert down.status_code == 502
    assert down.json()["detail"] == "Unable to load model."
    assert "huggingface.co" not in down.text
    assert "Request ID" not in down.text


def test_hub_search_upstream_error_is_stable(client, sign_in, monkeypatch):
    def boom(*args):
        raise RuntimeError("503, url='https://huggingface.co/api/models' (Request ID: 9f8e7d6c)")

    monkeypatch.setattr(main_module.hub, "search_models", boom)
    down = client.get("/api/hub/models", params={"search": "llama"})
    assert down.status_code == 502
    assert down.json()["detail"] == "Hugging Face Hub request failed."
    assert "huggingface.co" not in down.text
    assert "Request ID" not in down.text


def test_hub_gguf_range_proxies_the_byte_window(client, sign_in, monkeypatch):
    monkeypatch.setattr(
        main_module.hub,
        "read_gguf_range",
        lambda repo_id, filename, revision, range_header: {
            "content": b"gguf-bytes",
            "status_code": 206,
            "headers": {"Content-Range": "bytes 0-9/100"},
        },
    )
    window = client.get(
        "/api/hub/gguf-range",
        params={"repo_id": "acme/tiny", "filename": "model.gguf", "range": "bytes=0-9"},
    )
    assert window.status_code == 206
    assert window.content == b"gguf-bytes"
    assert window.headers["content-range"] == "bytes 0-9/100"

    # A bad range header is a 416, not a 500.
    monkeypatch.setattr(
        main_module.hub,
        "read_gguf_range",
        lambda repo_id, filename, revision, range_header: (_ for _ in ()).throw(
            ValueError("bad range")
        ),
    )
    bad_range = client.get(
        "/api/hub/gguf-range",
        params={"repo_id": "acme/tiny", "filename": "model.gguf"},
    )
    assert bad_range.status_code == 416

    # Upstream failures answer with the stable message, not the exception.
    monkeypatch.setattr(
        main_module.hub,
        "read_gguf_range",
        lambda repo_id, filename, revision, range_header: (_ for _ in ()).throw(
            RuntimeError("502, url='https://huggingface.co/acme/tiny/resolve/model.gguf'")
        ),
    )
    upstream_down = client.get(
        "/api/hub/gguf-range",
        params={"repo_id": "acme/tiny", "filename": "model.gguf", "range": "bytes=0-9"},
    )
    assert upstream_down.status_code == 502
    assert upstream_down.json()["detail"] == "Unable to read GGUF metadata."
    assert "huggingface.co" not in upstream_down.text


# ---------------------------------------------------------------------------
# S3-backed lifecycle: restore, cache eviction, and their guards.

def _s3_app(client, monkeypatch):
    s3 = dataclasses.replace(main_module.settings, model_storage_backend="s3", s3_bucket="model-bucket")
    monkeypatch.setattr(main_module, "settings", s3)
    monkeypatch.setattr(main_module.model_storage, "restore_repository", lambda repo_id: _primary_root() / repo_id)
    monkeypatch.setattr(main_module.model_storage, "evict_repository_cache", lambda repo_id: None)
    monkeypatch.setattr(main_module.model_storage, "list_repository_files", lambda repo_id: {"files": [], "total_bytes": 0})
    monkeypatch.setattr(main_module.indexer, "index_path", lambda root: None)
    monkeypatch.setattr(main_module.indexer, "files_for_model", lambda repo_id: {"files": [], "model": main_module.database.get_local_model(repo_id)})
    return client


def test_restore_refuses_non_s3_copies(client, sign_in, monkeypatch):
    _s3_app(client, monkeypatch)
    _seed(client, "acme/fs")
    reply = client.post(
        "/api/local-models/acme/fs/restore", headers={"X-CSRF-Token": sign_in}
    )
    assert reply.status_code == 409
    assert reply.json()["detail"] == "This model is not backed by S3."


def test_restore_reindexes_and_reports_the_copy(client, sign_in, monkeypatch):
    _s3_app(client, monkeypatch)
    _seed(client, "acme/s3", storage_backend="s3", cached=0)
    reply = client.post(
        "/api/local-models/acme/s3/restore", headers={"X-CSRF-Token": sign_in}
    )
    assert reply.status_code == 200, reply.text
    assert reply.json()["model"]["repo_id"] == "acme/s3"


def test_restore_refuses_while_a_download_is_active(client, sign_in, monkeypatch):
    _s3_app(client, monkeypatch)
    _seed(client, "acme/busy", storage_backend="s3", cached=0)
    main_module.database.create_download(
        {
            "id": "d1",
            "repo_id": "acme/busy",
            "revision": "main",
            "status": "queued",
            "total_bytes": 10,
            "downloaded_bytes": 0,
            "progress": 0,
            "speed_bps": 0,
            "error": None,
            "target_path": "acme/busy",
            "payload_json": "{}",
            "metadata_json": "{}",
            "created_at": MOMENT,
            "updated_at": MOMENT,
            "completed_at": None,
            "user_id": None,
        }
    )
    reply = client.post(
        "/api/local-models/acme/busy/restore", headers={"X-CSRF-Token": sign_in}
    )
    assert reply.status_code == 409
    assert "active download" in reply.json()["detail"]


def test_cache_eviction_refuses_non_s3_and_reports_evicted(client, sign_in, monkeypatch):
    _s3_app(client, monkeypatch)
    _seed(client, "acme/fs", cached=1)
    refused = client.delete(
        "/api/local-models/acme/fs/cache", headers={"X-CSRF-Token": sign_in}
    )
    assert refused.status_code == 409
    assert "S3" in refused.json()["detail"]

    _seed(client, "acme/s3", storage_backend="s3", cached=1)
    reply = client.delete(
        "/api/local-models/acme/s3/cache", headers={"X-CSRF-Token": sign_in}
    )
    assert reply.status_code == 200, reply.text
    assert reply.json()["status"] == "evicted"
    assert reply.json()["model"]["cached"] is False


# ---------------------------------------------------------------------------
# Moves: filesystem copies move between locations; S3 copies do not.

def test_move_refuses_s3_backed_copies(client, sign_in, monkeypatch):
    _seed(client, "acme/s3", storage_backend="s3")
    reply = client.post(
        "/api/local-models/acme/s3/moves",
        json={"destination": "overflow"},
        headers={"X-CSRF-Token": sign_in},
    )
    assert reply.status_code == 409
    assert "S3" in reply.json()["detail"]


def test_move_refuses_an_unknown_destination(client, sign_in, monkeypatch):
    _seed(client, "acme/tiny")
    reply = client.post(
        "/api/local-models/acme/tiny/moves",
        json={"destination": "nowhere"},
        headers={"X-CSRF-Token": sign_in},
    )
    assert reply.status_code == 404


# ---------------------------------------------------------------------------
# Verify + card at the route wall.

def _seed_tracked_copy(repo_id: str, sha: str = "sha-x") -> None:
    """A real on-disk copy with a complete manifest, plus its library row."""
    root = _primary_root() / repo_id
    root.mkdir(parents=True, exist_ok=True)
    (root / "model.safetensors").write_bytes(b"w" * 100)
    write_manifest(
        root,
        {
            "status": "complete",
            "schema_version": 2,
            "repo_id": repo_id,
            "sha": sha,
            "location": "primary",
            "selection": {
                "files": [{"path": "model.safetensors", "size": 100, "blob": "blob-w"}],
                "file_count": 1,
                "selected_bytes": 100,
                "transfer_bytes": 0,
                "cached_bytes": 100,
                "patterns": {"mode": "full", "allow": [], "ignore": []},
            },
        },
    )
    main_module.database.upsert_local_model(_record(repo_id, sha=sha, size_bytes=100, file_count=1))


def test_verify_route_returns_the_drift_artifact(client, sign_in):
    _seed_tracked_copy("acme/verified")
    reply = client.get("/api/local-models/acme/verified/verify")
    assert reply.status_code == 200, reply.text
    body = reply.json()
    assert body["drifted"] is False
    assert body["summary"]["present"] == 1


def test_verify_route_reports_409_for_an_unverifiable_copy(client, sign_in):
    _seed(client, "acme/untracked")  # a row, but no manifest on disk
    reply = client.get("/api/local-models/acme/untracked/verify")
    assert reply.status_code == 409
    assert "no manifest" in reply.json()["detail"]


def test_card_route_pins_to_the_copy_sha_and_reuses_the_snapshot(
    client, sign_in, monkeypatch
):
    _seed(client, "acme/carded", sha="sha-pinned")
    calls: list[str] = []

    def fake_card(repo_id, revision="main"):
        calls.append(revision)
        return "# card text"

    monkeypatch.setattr(main_module.hub, "read_model_card", fake_card)

    first = client.get("/api/local-models/acme/carded/card")
    assert first.status_code == 200, first.text
    assert first.json() == {"sha": "sha-pinned", "card": "# card text", "fetched_at": first.json()["fetched_at"]}
    assert calls == ["sha-pinned"]  # fetched at the pinned sha, never "main"

    second = client.get("/api/local-models/acme/carded/card")
    assert second.json()["card"] == "# card text"
    assert calls == ["sha-pinned"]  # served from the snapshot; no second fetch

    # A copy with no recorded revision has nothing to pin to.
    _seed(client, "acme/unsha", sha=None)
    unsha = client.get("/api/local-models/acme/unsha/card")
    assert unsha.status_code == 200
    assert unsha.json() == {"sha": None, "card": None, "fetched_at": None}


# ---------------------------------------------------------------------------
# Watch acknowledge.

def test_watch_seen_acknowledges_the_recorded_drift(client, sign_in):
    owner_id = client.get("/api/auth/status").json()["user"]["id"]
    _seed(client, "acme/drifted", sha="base-1")
    ensure_watch(
        main_module.database, None, "acme/drifted", owner_id,
        copy_sha="base-1", clock=lambda: MOMENT,
    )
    # The poller observed an upstream move.
    watch_row = main_module.database.get_watched_model(owner_id, "acme/drifted")
    main_module.database.record_watch_check(
        watch_row["id"],
        checked_at=MOMENT,
        upstream_sha="base-2",
        changed_at=MOMENT,
    )

    before = client.get("/api/watches").json()["items"][0]
    assert before["update_available"] is True

    reply = client.post(
        "/api/watches/acme/drifted/seen", headers={"X-CSRF-Token": sign_in}
    )
    assert reply.status_code == 200, reply.text
    after = reply.json()
    assert after["last_seen_sha"] == "base-2"
    assert after["update_available"] is False


def test_watch_seen_refuses_unknown_and_clean_watches(client, sign_in):
    owner_id = client.get("/api/auth/status").json()["user"]["id"]
    _seed(client, "acme/clean", sha="base-1")
    ensure_watch(
        main_module.database, None, "acme/clean", owner_id,
        copy_sha="base-1", clock=lambda: MOMENT,
    )
    clean = client.post(
        "/api/watches/acme/clean/seen", headers={"X-CSRF-Token": sign_in}
    )
    assert clean.status_code == 409
    assert clean.json()["detail"] == (
        "Nothing to acknowledge: no upstream change has been recorded."
    )

    absent = client.post(
        "/api/watches/acme/ghost/seen", headers={"X-CSRF-Token": sign_in}
    )
    assert absent.status_code == 404


# ---------------------------------------------------------------------------
# Runtime job fetch: the read side of the token ladder for one job.

def test_runtime_job_fetch_returns_the_row_and_404s_when_absent(client, monkeypatch):
    import uuid

    main_module.database.create_runtime_job(
        {
            "id": uuid.uuid4().hex,
            "target_id": "box",
            "target_name": "box",
            "target_kind": "ollama",
            "repo_id": "acme/stored",
            "runtime_model_name": "stored",
            "source_file": None,
            "status": "complete",
            "total_bytes": 100,
            "processed_bytes": 100,
            "progress": 100,
            "message": "Loaded",
            "error": None,
            "created_at": MOMENT,
            "updated_at": MOMENT,
            "completed_at": MOMENT,
            "user_id": None,
        }
    )
    job_id = main_module.database.list_runtime_jobs(limit=1)[0]["id"]

    tokenized = dataclasses.replace(main_module.settings, runtime_api_token="test-token")
    monkeypatch.setattr(main_module, "settings", tokenized)
    headers = {"Authorization": "Bearer test-token"}

    job = client.get(f"/api/runtime-jobs/{job_id}", headers=headers)
    assert job.status_code == 200, job.text
    assert job.json()["status"] == "complete"

    missing = client.get("/api/runtime-jobs/ghost", headers=headers)
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Runtime job not found."

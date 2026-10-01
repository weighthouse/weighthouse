"""The chunked-upload protocol at the HTTP wall (D8 coverage pass).

The frontend resume logic trusts this endpoint's statuses and offsets,
so the mistake classes live here: header parsing, cap enforcement on
both the declared length and the streamed body, offset continuity,
completed-file dedupe, path validation, and cross-user isolation.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest


@pytest.fixture()
def uploader(client):
    """Owner session and a fresh, still-uploading repository."""
    import app.main as main_module

    sign_in = client.post(
        "/api/auth/setup",
        json={"username": "keeper", "display_name": "Keeper", "password": "longenough1"},
    ).json()["csrf_token"]
    # The model storage root outlives each fixture-fresh database, so a
    # leftover slot from an earlier test must go before creating again.
    slot = Path(main_module.settings.model_storage) / "keeper" / "handmade"
    shutil.rmtree(slot, ignore_errors=True)
    created = client.post(
        "/api/uploads/repositories",
        json={"slug": "handmade", "description": "local weights"},
        headers={"X-CSRF-Token": sign_in},
    )
    assert created.status_code == 201, created.text
    return {
        "module": main_module,
        "csrf": sign_in,
        "repo_id": created.json()["repo_id"],
        "storage": Path(main_module.settings.model_storage),
    }


def _chunk(client, repo_id, path, offset, total, payload, csrf, extra_headers=None):
    headers = {
        "X-CSRF-Token": csrf,
        "Upload-Offset": str(offset),
        "Upload-Length": str(total),
        "Content-Type": "application/octet-stream",
    }
    if extra_headers:
        headers.update(extra_headers)
    response = client.put(
        "/api/uploads/repositories/files",
        params={"repo_id": repo_id, "path": path},
        content=payload,
        headers=headers,
    )
    return response


def test_bad_headers_and_paths_answer_400(client, uploader):
    mistake = client.put(
        "/api/uploads/repositories/files",
        params={"repo_id": uploader["repo_id"], "path": "weights.bin"},
        content=b"x",
        headers={
            "X-CSRF-Token": uploader["csrf"],
            "Upload-Offset": "not-a-number",
            "Upload-Length": "1",
        },
    )
    assert mistake.status_code == 400
    assert mistake.json()["detail"] == "Upload headers are invalid."

    escape = _chunk(
        client,
        uploader["repo_id"],
        "../secret.bin",
        0,
        1,
        b"x",
        uploader["csrf"],
    )
    assert escape.status_code == 400

    reserved = _chunk(
        client,
        uploader["repo_id"],
        ".modelmanifest.json",
        0,
        1,
        b"x",
        uploader["csrf"],
    )
    assert reserved.status_code == 400


def test_the_chunk_cap_is_enforced_twice(client, uploader):
    huge_total = uploader["module"].settings.upload_chunk_mb * 1024**2 * 4
    declared = _chunk(
        client,
        uploader["repo_id"],
        "weights.bin",
        0,
        huge_total,
        b"payload",
        uploader["csrf"],
        extra_headers={"Content-Length": str(huge_total)},
    )
    assert declared.status_code == 413
    assert declared.json()["detail"] == "Upload chunk is too large."

    # A lying Content-Length gets the streamed body through the second
    # check: declare small, ship beyond the cap.
    smuggled = client.put(
        "/api/uploads/repositories/files",
        params={"repo_id": uploader["repo_id"], "path": "weights.bin"},
        content=b"x" * (int(uploader["module"].settings.upload_chunk_mb * 1024**2) + 16),
        headers={
            "X-CSRF-Token": uploader["csrf"],
            "Upload-Offset": "0",
            "Upload-Length": "9223372036854775807",
        },
    )
    assert smuggled.status_code == 413


def test_offset_protocol_and_completed_dedupe(client, uploader):
    repo_id = uploader["repo_id"]
    # Bad offset arithmetic is a 400; a mismatch against an existing
    # partial is the resumable-upload 409.
    imath = _chunk(client, repo_id, "weights.bin", 3, 4, b"abcd", uploader["csrf"])
    assert imath.status_code == 400

    first = _chunk(client, repo_id, "weights.bin", 0, 4, b"abc", uploader["csrf"])
    assert first.status_code == 200
    partial = client.get(
        "/api/uploads/repositories/files/status",
        params={"repo_id": repo_id, "path": "weights.bin"},
    )
    assert partial.json() == {"offset": 3, "complete": False}

    # A partial in place demands continuity: a fresh chunk claiming an
    # offset other than the server's byte count is a resumable 409.
    discontinuity = _chunk(client, repo_id, "weights.bin", 1, 4, b"bc", uploader["csrf"])
    assert discontinuity.status_code == 409
    assert "mismatch" in discontinuity.json()["detail"]

    resumed = _chunk(client, repo_id, "weights.bin", 3, 4, b"d", uploader["csrf"])
    assert resumed.status_code == 200
    status = client.get(
        "/api/uploads/repositories/files/status",
        params={"repo_id": repo_id, "path": "weights.bin"},
    )
    assert status.json() == {"offset": 4, "complete": True}

    # The completed file answers idempotently while size and total agree;
    # a different total is the resumable conflict.
    again = _chunk(client, repo_id, "weights.bin", 0, 4, b"abcd", uploader["csrf"])
    assert again.status_code == 200
    assert again.json() == {"offset": 4, "complete": True, "path": "weights.bin"}

    same_size_lie = _chunk(client, repo_id, "weights.bin", 0, 4, b"zzzz", uploader["csrf"])
    assert same_size_lie.status_code == 200

    different_total = _chunk(client, repo_id, "weights.bin", 0, 5, b"zzz", uploader["csrf"])
    assert different_total.status_code == 409


def test_cross_user_status_reads_404(client, uploader):
    import app.main as main_module

    client.post(
        "/api/users",
        json={"username": "member", "display_name": "Member", "password": "longenough1"},
        headers={"X-CSRF-Token": uploader["csrf"]},
    )
    member = client.__class__(main_module.app)
    member.post(
        "/api/auth/login",
        json={"username": "member", "password": "longenough1"},
    )
    stranger = member.get(
        "/api/uploads/repositories/files/status",
        params={"repo_id": uploader["repo_id"], "path": "weights.bin"},
    )
    assert stranger.status_code == 404


def test_finalize_requires_complete_files_then_becomes_ready(client, uploader):
    repo_id = uploader["repo_id"]

    empty = client.post(
        "/api/uploads/repositories/finalize",
        params={"repo_id": repo_id},
        headers={"X-CSRF-Token": uploader["csrf"]},
    )
    assert empty.status_code == 409

    part_of_total = 1024
    landed = _chunk(
        client,
        repo_id,
        "weights.bin",
        0,
        part_of_total,
        b"a" * 16,
        uploader["csrf"],
    )
    assert landed.status_code == 200

    unfinished = client.post(
        "/api/uploads/repositories/finalize",
        params={"repo_id": repo_id},
        headers={"X-CSRF-Token": uploader["csrf"]},
    )
    assert unfinished.status_code == 409
    assert "Finish all file uploads" in unfinished.json()["detail"]

    rest = _chunk(
        client,
        repo_id,
        "weights.bin",
        16,
        part_of_total,
        b"b" * (part_of_total - 16),
        uploader["csrf"],
    )
    assert rest.status_code == 200

    index = _chunk(client, repo_id, "tokenizer.model", 0, 2, b"ab", uploader["csrf"])
    assert index.status_code == 200

    finalized = client.post(
        "/api/uploads/repositories/finalize",
        params={"repo_id": repo_id},
        headers={"X-CSRF-Token": uploader["csrf"]},
    )
    assert finalized.status_code == 200
    assert finalized.json()["status"] == "ready"

    sealed = _chunk(client, repo_id, "extra.bin", 0, 1, b"x", uploader["csrf"])
    assert sealed.status_code == 400
    assert "finalized" in sealed.json()["detail"]


def test_repository_delete_needs_exact_confirmation(client, uploader):
    repo_id = uploader["repo_id"]
    wrong = client.request(
        "DELETE",
        "/api/uploads/repositories",
        params={"repo_id": repo_id},
        json={"confirmation": "somebody/else"},
        headers={"X-CSRF-Token": uploader["csrf"]},
    )
    assert wrong.status_code == 409

    right = client.request(
        "DELETE",
        "/api/uploads/repositories",
        params={"repo_id": repo_id},
        json={"confirmation": repo_id},
        headers={"X-CSRF-Token": uploader["csrf"]},
    )
    assert right.status_code == 200
    assert not (uploader["storage"] / repo_id).exists()

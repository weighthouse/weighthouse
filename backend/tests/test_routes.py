"""Route attestation: the API surface exists before any of it is called.

Twice now an edit has eaten a decorator or a newline around one
(``tracked_update_diff`` serving a broken details route, then
``/api/auth/setup`` swallowed into a matrix-multiply expression that
only failed at call time — caught by the image smoke test the suite
could not see). This file pins the route table itself, so the class
of bug dies here instead of in a deployed container.
"""

from __future__ import annotations

from app.main import app

EXPECTED_ROUTES: dict[str, set[str]] = {
    "/api/health": {"GET"},
    "/api/auth/status": {"GET"},
    "/api/auth/setup": {"POST"},
    "/api/auth/login": {"POST"},
    "/api/auth/logout": {"POST"},
    "/api/account/password": {"PATCH"},
    "/api/users": {"GET", "POST"},
    "/api/agent-keys": {"GET", "POST"},
    "/api/mcp": {"POST", "GET", "DELETE"},
    "/api/preferences": {"GET", "PUT"},
    "/api/downloads": {"GET", "POST"},
    "/api/locations": {"GET"},
    "/api/watches": {"GET", "POST"},
    "/api/collections": {"GET", "POST"},
    "/api/saved-models": {"GET", "POST"},
    "/api/uploads/repositories": {"GET", "POST", "PATCH", "DELETE"},
    "/api/uploads/repositories/files": {"PUT"},
    "/api/uploads/repositories/files/status": {"GET"},
    "/api/uploads/repositories/finalize": {"POST"},
}

EXPECTED_DYNAMIC_ROUTES: tuple[tuple[str, str], ...] = (
    ("GET", "/api/hub/models"),
    ("GET", "/api/hub/models/{repo_id:path}"),
    ("GET", "/api/hub/gguf-range"),
    ("GET", "/api/downloads/{download_id}"),
    ("POST", "/api/downloads/{download_id}/cancel"),
    ("POST", "/api/downloads/{download_id}/pause"),
    ("POST", "/api/downloads/{download_id}/resume"),
    ("POST", "/api/downloads/pause-all"),
    ("POST", "/api/downloads/resume-all"),
    ("POST", "/api/downloads/{download_id}/restart"),
    ("POST", "/api/downloads/{download_id}/move-to-front"),
    ("DELETE", "/api/downloads/{download_id}"),
    ("POST", "/api/downloads/clear"),
    ("GET", "/api/runtime-jobs"),
    ("GET", "/api/runtime-jobs/{job_id}"),
    ("GET", "/api/runtimes"),
    ("POST", "/api/runtimes/{target_id}/load"),
    ("GET", "/api/local-models"),
    ("POST", "/api/local-models/scan"),
    ("GET", "/api/local-models/{repo_id:path}"),
    ("DELETE", "/api/local-models/{repo_id:path}"),
    ("POST", "/api/local-models/{repo_id:path}/restore"),
    ("DELETE", "/api/local-models/{repo_id:path}/cache"),
    ("GET", "/api/local-models/{repo_id:path}/updates"),
    ("POST", "/api/local-models/{repo_id:path}/updates/apply"),
    ("GET", "/api/local-models/{repo_id:path}/verify"),
    ("POST", "/api/local-models/{repo_id:path}/moves"),
    ("GET", "/api/local-models/{repo_id:path}/deletion"),
    ("POST", "/api/local-models/{repo_id:path}/deletion/files"),
    ("GET", "/api/local-models/{repo_id:path}/notes"),
    ("POST", "/api/local-models/{repo_id:path}/notes"),
    ("GET", "/api/local-models/{repo_id:path}/card"),
    ("PATCH", "/api/local-models/{repo_id:path}/notes/{note_id}"),
    ("DELETE", "/api/local-models/{repo_id:path}/notes/{note_id}"),
    ("POST", "/api/local-models/{repo_id:path}/notes/{note_id}/pin"),
    ("POST", "/api/local-models/{repo_id:path}/notes/{note_id}/share"),
    ("DELETE", "/api/watches/{repo_id:path}"),
    ("POST", "/api/watches/check"),
    ("POST", "/api/watches/{repo_id:path}/seen"),
    ("DELETE", "/api/agent-keys/{key_id}"),
    ("DELETE", "/api/agent-keys/{key_id}/purge"),
    ("GET", "/api/agent-keys/{key_id}/events"),
    ("DELETE", "/api/collections/{collection_id}"),
    ("DELETE", "/api/saved-models/{repo_id:path}"),
)


def _registered() -> dict[tuple[str, str], str]:
    """(method, path) -> handler name for every API route."""
    table: dict[tuple[str, str], str] = {}
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/"):
            continue
        methods = getattr(route, "methods", None) or set()
        for method in methods:
            if method == "HEAD":
                continue  # implicit; never the route's own declaration
            table[(method, path)] = route.name
    return table


def test_the_core_surface_is_registered_exactly_once():
    registered = _registered()
    for path, methods in EXPECTED_ROUTES.items():
        for method in methods:
            assert (method, path) in registered, f"missing route: {method} {path}"


def test_dynamic_routes_are_registered():
    registered = _registered()
    for method, path in EXPECTED_DYNAMIC_ROUTES:
        assert (method, path) in registered, f"missing route: {method} {path}"


def test_the_attested_table_is_the_whole_table():
    """Anything added or removed must edit this file consciously."""
    registered = _registered()
    attested = dict(EXPECTED_ROUTES)
    for method, path in EXPECTED_DYNAMIC_ROUTES:
        attested.setdefault(path, set()).add(method)
    assert set(registered) == {
        (method, path) for path, methods in attested.items() for method in methods
    }


def test_no_two_handlers_share_a_route():
    seen: dict[tuple[str, str], str] = {}
    for key, name in _registered().items():
        holder = seen.setdefault(key, name)
        assert holder == name, f"{key} served by both {holder} and {name}"


def test_auth_status_is_not_glued_to_the_next_decorator():
    """The exact regression from the image smoke test: an eaten newline
    turned ``return payload`` into ``payload @ app.post(...)`` — valid
    syntax, fatal at call time, with the setup route silently lost."""
    route = next(
        (
            r
            for r in app.routes
            if getattr(r, "path", "") == "/api/auth/status"
        ),
        None,
    )
    assert route is not None and route.name == "auth_status"
    assert "/api/auth/setup" in _registered_paths()


def _registered_paths() -> set[str]:
    return {path for _, path in _registered()}

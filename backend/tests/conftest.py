"""Test-process environment: the suite's Database-fixture tests build
their own isolated sqlite handles, but route-level tests boot the real
app, whose module-level services bind their directories once at import.
This file runs before any test module import, so the defaults below
point every import-time binding at throwaway directories instead of
the deployment paths. Individual fixtures can still replace settings
afterwards for narrower behavior."""

import os
import tempfile
from pathlib import Path

_CACHE_ROOT = Path(tempfile.gettempdir()) / "mh-test-space"
os.environ.setdefault("DATA_DIR", str(_CACHE_ROOT / "data"))
os.environ.setdefault("MODEL_STORAGE", str(_CACHE_ROOT / "models"))

# Route-level test infra, promoted from test_agentkeys.py so every
# through-the-app test shares one fixture (D8 coverage pass).
import dataclasses
from pathlib import Path

import pytest

import app.config as config
import app.main as main_module
import app.mcp_server as mcp_module


@pytest.fixture()
def client(monkeypatch):
    """A booted app with a fresh database. conftest has already pointed
    the import-time service bindings at throwaway directories; this
    fixture starts every test's app on a blank database and relaxes the
    cookie flags so the plain-HTTP test client can hold the session."""
    if main_module.database.path is not None:
        Path(main_module.database.path).unlink(missing_ok=True)

    # The auth service is a module singleton; its in-memory rate limiter
    # outlives any fixture-fresh database, so this fixture clears it
    # exactly like the disk state.
    with main_module.auth._attempt_lock:
        main_module.auth._attempts.clear()

    isolated = dataclasses.replace(config.settings, allow_insecure_cookies=True)
    monkeypatch.setattr(config, "settings", isolated)
    monkeypatch.setattr(main_module, "settings", isolated)
    monkeypatch.setattr(mcp_module, "app_settings", isolated)
    # The auth service captured the real settings at import; cookies get
    # their flags from it at request time.
    monkeypatch.setattr(main_module.auth, "settings", isolated)

    from fastapi.testclient import TestClient

    with TestClient(main_module.app) as test_client:
        yield test_client


@pytest.fixture()
def sign_in(client):
    """Create the owner account on the fresh app; the session cookie
    stays in the test client, and the CSRF token comes back for writes."""
    response = client.post(
        "/api/auth/setup",
        json={"username": "keeper", "display_name": "Keeper", "password": "longenough1"},
    )
    assert response.status_code == 201, response.text
    return response.json()["csrf_token"]

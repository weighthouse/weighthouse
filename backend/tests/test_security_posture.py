"""Deployment posture at the HTTP boundary (security audit D8).

Pins the pieces a refactor could silently drop: security headers and
the Content-Security-Policy travel on every response, the interactive
API explorer stays dark, the storage path is an operator fact and not
a pre-authentication fact, and a state-changing request that carries
our session cookie under a foreign Origin is refused outright.
"""

from __future__ import annotations


def test_security_headers_and_csp_travel_on_every_response(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "same-origin"
    policy = response.headers["Content-Security-Policy"]
    assert "default-src 'self'" in policy
    assert "script-src 'self'" in policy
    assert "frame-ancestors 'none'" in policy


def test_the_interactive_api_explorer_stays_dark(client):
    assert client.get("/api/docs").status_code == 404
    assert client.get("/api/redoc").status_code == 404


def test_health_hides_the_storage_path_until_someone_signs_in(client):
    anonymous = client.get("/api/health")
    assert anonymous.status_code == 200
    storage = anonymous.json()["storage"]
    assert "path" not in storage
    assert storage["free_bytes"] > 0

    setup = client.post(
        "/api/auth/setup",
        json={"username": "keeper", "display_name": "Keeper", "password": "longenough1"},
    )
    assert setup.status_code == 201
    authed = client.get("/api/health")
    assert authed.json()["storage"]["path"]
    assert authed.json()["storage"]["free_bytes"] > 0


def test_a_foreign_origin_cannot_wield_a_signed_in_session(client, sign_in):
    response = client.post(
        "/api/watches",
        json={"repo_id": "acme/tiny"},
        headers={"X-CSRF-Token": sign_in, "Origin": "https://evil.example"},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Cross-origin writes are refused."


def test_same_origin_writes_answer_the_csrf_wall_not_the_origin_wall(
    client, sign_in
):
    response = client.post(
        "/api/watches",
        json={"repo_id": "acme/tiny"},
        headers={"X-CSRF-Token": sign_in, "Origin": "http://testserver"},
    )
    assert response.status_code != 403 or "Cross-origin" not in response.text


def test_writes_without_a_session_cookie_skip_the_origin_gate(client):
    # No cookie yet: the origin middleware has nothing to protect, so a
    # foreign Origin reaches the ordinary auth answer rather than the
    # origin refusal.
    response = client.post(
        "/api/auth/login",
        json={"username": "keeper", "password": "wrong-password"},
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] != "Cross-origin writes are refused."


def test_reads_are_never_origin_gated(client, sign_in):
    response = client.get(
        "/api/local-models",
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 200

"""Deployment posture: acknowledgment-gated defaults, honestly surfaced.

The enterprise-first posture is made of refusals and hints, not
wizards: cookies are marked Secure by default (plain HTTP needs an
explicit escape hatch), disabling accounts needs its own
acknowledgment at startup, and a server whose cookies would not stick
says so at the exact place a person looks — the sign-in screen.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.config import Settings
from app.main import (
    TRANSPORT_WARNING,
    ensure_deployment_posture,
    transport_warning_for,
)


def _settings(**overrides) -> SimpleNamespace:
    base = Settings()
    values: dict[str, object] = {
        "accounts_enabled": base.accounts_enabled,
        "allow_accountless": base.allow_accountless,
        "secure_cookies": base.secure_cookies,
        "allow_insecure_cookies": base.allow_insecure_cookies,
    }
    values.update(overrides)
    values["cookies_secure"] = bool(
        values["secure_cookies"]
    ) and not bool(values["allow_insecure_cookies"])
    return SimpleNamespace(**values)


def test_default_posture_is_secure_and_accounted():
    posture = _settings()
    assert posture.accounts_enabled is True
    assert posture.cookies_secure is True


def test_disabling_accounts_without_acknowledgment_refuses_startup():
    with pytest.raises(RuntimeError, match="ALLOW_ACCOUNTLESS=true"):
        ensure_deployment_posture(_settings(accounts_enabled=False))


def test_disabling_accounts_with_acknowledgment_starts():
    ensure_deployment_posture(
        _settings(accounts_enabled=False, allow_accountless=True)
    )


def test_insecure_cookie_acknowledgment_relaxes_secure_flag():
    assert _settings(allow_insecure_cookies=True).cookies_secure is False
    assert _settings(secure_cookies=False).cookies_secure is False
    assert _settings().cookies_secure is True


def test_plain_http_gets_the_warning_https_does_not():
    secure = _settings()
    http_request = SimpleNamespace(url=SimpleNamespace(scheme="http"))
    https_request = SimpleNamespace(url=SimpleNamespace(scheme="https"))
    assert transport_warning_for(http_request, settings_snapshot=secure) == TRANSPORT_WARNING
    assert transport_warning_for(https_request, settings_snapshot=secure) is None


def test_acknowledged_insecure_deployment_never_warns():
    relaxed = _settings(allow_insecure_cookies=True)
    http_request = SimpleNamespace(url=SimpleNamespace(scheme="http"))
    assert transport_warning_for(http_request, settings_snapshot=relaxed) is None

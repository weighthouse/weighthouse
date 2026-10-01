"""Agent API keys: named credentials that let headless callers in.

The library is driven from a browser today; its rooms should be open to
honest agents too. Agent keys are the door they knock on: a named token
created by an administrator, held by an automation, and constrained to
explicit scopes. The bearer acts with the identity of the account that
minted it — the same visibility rules as any signed-in user — so the
permission question stays exactly as answerable as the web app's.

Scopes are a short ladder, ordered from observing to acting:

- ``search`` — discover repositories on the Hub.
- ``read`` — read the library: stored copies, files, update diffs,
  verify reports, storage locations, and download state.
- ``queue`` — ask the library to fetch: downloads, update application,
  cache restores, and runtime dispatch.
- ``mutate`` — change stored content: delete files or whole copies,
  move between locations, evict caches, rescan storage.

Keys are stored the way session tokens are: only a SHA-256 digest never
the raw secret, which is shown exactly once at creation. Revocation is
a stamp, not an erasure — a revoked key stays listed past its death so
its audit trail remains readable.
"""

from __future__ import annotations

import hashlib
import secrets
from typing import Any

AGENT_KEY_PREFIX = "ak_"

AGENT_KEY_SCOPES: dict[str, str] = {
    "search": "Discover repositories on the Hub.",
    "read": "Read the library: copies, files, update diffs, verify reports, locations, download state.",
    "queue": "Ask the library to fetch: downloads, updates, restores, runtime dispatch.",
    "mutate": "Change stored content: deletions, moves, cache eviction, storage scans.",
}

MAX_AGENT_KEYS = 64
EVENT_RETENTION = 200


class AgentKeyError(ValueError):
    """Raised when a requested key name or scope list is not acceptable."""


class AgentKeyDenied(Exception):
    """Raised when a bearer token does not open the requested door."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def parse_scopes(raw: Any) -> list[str]:
    """Validate and normalize a requested scope list, preserving order."""
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise AgentKeyError("Scopes must be a list of scope names.")
    cleaned: list[str] = []
    for item in raw:
        scope = item.strip().lower()
        if scope not in AGENT_KEY_SCOPES:
            raise AgentKeyError(
                f"Unknown scope '{scope}'. Known scopes: {', '.join(AGENT_KEY_SCOPES)}."
            )
        if scope not in cleaned:
            cleaned.append(scope)
    if not cleaned:
        raise AgentKeyError("Choose at least one scope for this key.")
    return cleaned


def generate_agent_key() -> tuple[str, str]:
    """Return (raw token with its prefix, stored SHA-256 hex digest)."""
    raw = f"{AGENT_KEY_PREFIX}{secrets.token_urlsafe(32)}"
    return raw, hashlib.sha256(raw.encode("utf-8")).hexdigest()


def key_hint(raw: str) -> str:
    """A display fragment for listing pages; useless for authentication."""
    return f"…{raw[-4:]}"


def resolve_agent_bearer(
    raw_token: str | None,
    database: Any,
    required_scopes: tuple[str, ...],
) -> dict[str, Any]:
    """Authenticate a bearer token and enforce the requested scopes.

    Returns a context dict with the acting ``user`` and the ``key``
    record (scopes included) so the caller can stamp request state.
    Raises :class:`AgentKeyDenied` with an HTTP status when the token
    does not open this door.
    """
    if not raw_token or not raw_token.startswith(AGENT_KEY_PREFIX):
        raise AgentKeyDenied(401, "Sign in or present an agent API key.")
    key = database.get_agent_key_by_hash(
        hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    )
    if not key:
        raise AgentKeyDenied(401, "Agent API key is not valid.")
    if key.get("revoked_at"):
        raise AgentKeyDenied(401, "Agent API key was revoked by its owner.")
    user = database.get_user(key["user_id"], include_secret=False)
    if not user:
        raise AgentKeyDenied(401, "Agent API key's owner no longer exists.")
    scopes = key.get("scopes") or []
    missing = [scope for scope in required_scopes if scope not in scopes]
    if missing:
        raise AgentKeyDenied(
            403,
            f"This agent key lacks the '{missing[0]}' scope.",
        )
    return {"user": user, "key": key}

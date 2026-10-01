"""Watching a repository for upstream movement.

Locked design (charter D13): three independent facts — Saved, Watch,
Copy — with one deliberate coupling, **downloading turns Watch on**.
A watch is a subscription at repository level; notifying is simply
what a watch does. There is no separate notify flag, no threshold,
no name-pattern rule engine, and no frozen state — a downloaded copy
that is not watched is a *quiet copy*, which is a legitimate
long-term residence.

Watches are per-user, mirroring the saved-models pattern: the
owner's watches are the owner's. Each row remembers:

- the upstream revision last acknowledged by the watcher
  (``last_seen_sha``) — taken at watch time for copy-less watches, so
  a change *after* subscribing is the notification;
- the upstream revision the poller last observed (``upstream_sha``)
  and when drift was first detected (``changed_at``), self-healing
  back to clean when baseline and upstream agree again.

Every request that would silently miss a change is refused instead:
subscribing to something with no copy and no readable upstream
fails loudly at watch time rather than discoverably a month later.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

WATCH_METADATA_FIELDS = ("author", "pipeline_tag", "library_name", "license")
METADATA_MAX_BYTES = 4_000


class WatchError(ValueError):
    """Raised when a watch cannot be created as requested."""


def _metadata_snapshot(info: dict[str, Any]) -> dict[str, Any]:
    """The tiny hub snapshot a copy-less watch needs to be legible."""
    snapshot = {field: info.get(field) for field in WATCH_METADATA_FIELDS}
    if len(json.dumps(snapshot)) > METADATA_MAX_BYTES:
        raise WatchError("Upstream metadata is unexpectedly large; refusing to store it.")
    return snapshot


def ensure_watch(
    database: Any,
    hub_snapshot: dict[str, Any] | None,
    repo_id: str,
    user_id: str,
    *,
    copy_sha: str | None,
    clock: Any,
) -> dict[str, Any]:
    """Subscribe a user to a repository, capturing the honest baseline.

    With a copy present, the copy's revision is the baseline — no hub
    call is needed and drift means "your copy is behind". Without one,
    the caller supplies a fresh upstream snapshot; watching a distant
    repository whose revision cannot be read fails rather than
    silently starting with an empty baseline.
    """
    existing = database.get_watched_model(user_id, repo_id)
    if existing:
        return existing
    if copy_sha:
        last_seen = copy_sha
        metadata: dict[str, Any] = {}
    else:
        if not hub_snapshot or not str(hub_snapshot.get("sha") or "").strip():
            raise WatchError(
                "Upstream revision could not be read, so the watch would not know "
                "what it had last seen. Try again when the Hub is reachable."
            )
        last_seen = hub_snapshot["sha"]
        metadata = _metadata_snapshot(hub_snapshot)
    timestamp = clock()
    record = {
        "id": uuid.uuid4().hex,
        "user_id": user_id,
        "repo_id": repo_id,
        "metadata_json": json.dumps(metadata),
        "last_seen_sha": last_seen,
        "upstream_sha": None,
        "changed_at": None,
        "checked_at": None,
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    database.create_watched_model(record)
    return database.get_watched_model(user_id, repo_id)


def build_watch_payload(
    row: dict[str, Any],
    copy: dict[str, Any] | None,
) -> dict[str, Any]:
    """The API view of one watch row plus its derived state.

    Derived purely from stored facts — never an upstream call. For a
    watch with a copy, the copy's revision is the baseline; for a
    copy-less watch it is the revision last acknowledged. Drift is
    what the poller recorded in ``changed_at``; where the poller has
    never run (fresh list, no check yet), nothing is claimed.
    """
    has_copy = bool(copy)
    baseline = ((copy or {}).get("sha") or "").strip() if has_copy else (
        str(row.get("last_seen_sha") or "").strip() or None
    )
    baseline = baseline or None
    upstream = str(row.get("upstream_sha") or "").strip() or None
    update_available = bool(
        upstream and row.get("changed_at") and (not baseline or baseline != upstream)
    )
    metadata = row.get("metadata") or {}
    if not metadata and copy:
        metadata = {
            field: copy.get(field) for field in WATCH_METADATA_FIELDS
        }
        # The library record never stores the author separately; the
        # repository id carries it.
        if not metadata.get("author"):
            metadata["author"] = (row["repo_id"].split("/", 1) or [""])[0] or None
    return {
        "repo_id": row["repo_id"],
        "watched_since": row["created_at"],
        "checked_at": row.get("checked_at"),
        "change_detected_at": row.get("changed_at"),
        "has_copy": has_copy,
        "update_available": update_available,
        "state": "copy" if has_copy else "no-copy",
        "copy_sha": (copy or {}).get("sha"),
        "copy": (
            {
                "size_bytes": copy.get("size_bytes"),
                "location": copy.get("location"),
                "storage_backend": copy.get("storage_backend"),
                "cached": bool(copy.get("cached")),
                "parameter_count": copy.get("parameter_count"),
            }
            if copy
            else None
        ),
        "upstream_sha": upstream,
        "last_seen_sha": row.get("last_seen_sha"),
        "metadata": metadata,
    }


def poll_observation(
    row: dict[str, Any],
    copy: dict[str, Any] | None,
    upstream_sha: str | None,
    now: str,
) -> dict[str, Any]:
    """Fold one poller observation into the row's bookkeeping.

    The baseline is the copy's revision when a copy exists, otherwise
    the revision last acknowledged by the watcher. Agreement heals any
    drift indication (the flag clears itself once the copy is updated
    or the watcher acknowledges); disagreement stamps ``changed_at``
    only on first detection, so the timestamp means something. An
    unreadable upstream (``upstream_sha`` null) is *unknown*, not
    clean: an existing stamp carries forward untouched.
    """
    baseline = None
    if copy:
        baseline = str(copy.get("sha") or "").strip() or None
    if not baseline:
        baseline = str(row.get("last_seen_sha") or "").strip() or None
    if not upstream_sha:
        return {
            "watch_id": row["id"],
            "checked_at": now,
            "upstream_sha": None,
            "changed_at": row.get("changed_at"),
        }
    drifted = bool(upstream_sha and (not baseline or upstream_sha != baseline))
    return {
        "watch_id": row["id"],
        "checked_at": now,
        "upstream_sha": upstream_sha,
        "changed_at": (row.get("changed_at") or now) if drifted else None,
    }


class WatchPoller:
    """The background loop that lets a watch mean something.

    A watched repository moves when it moves; the notification only
    exists if something looks. This does, quietly: it walks the
    watches whose check slot is due, reads the light upstream record
    (one cheap revision lookup per item), and folds what it saw into
    the row. Spacing between checks keeps upstream politeness; an
    unreachable upstream burns its slot without touching any recorded
    observation. Detection is honest about its cadence — the UI says
    "checked" for exactly this reason.
    """

    SCAN_INTERVAL_SECONDS = 60.0
    CHECK_SPACING_SECONDS = 6.0

    def __init__(
        self,
        settings: Any,
        database: Any,
        fetch_upstream_sha: Callable[[str], str | None],
    ):
        import threading

        self.settings = settings
        self.database = database
        self._fetch_upstream_sha = fetch_upstream_sha
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        import threading

        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run, name="watch-poller", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)
            self._thread = None

    def _run(self) -> None:
        import time as time_module

        while not self._stop.is_set():
            try:
                self.work_pass()
            except Exception:  # the loop itself must not die
                pass
            self._stop.wait(self.SCAN_INTERVAL_SECONDS)

    def work_pass(self, clock: Callable[..., str] | None = None) -> int:
        """Check every due watch once. Returns how many were checked.

        Testable directly; the thread calls it with the wall clock.
        """
        from datetime import datetime, timedelta, timezone

        now_fn = clock or (lambda: datetime.now(timezone.utc).isoformat())
        cadence = timedelta(minutes=max(1, self.settings.watch_poll_minutes))
        cutoff = (datetime.now(timezone.utc) - cadence).isoformat()
        checked = 0
        for row in self.database.list_watches_for_scan():
            if self._stop.is_set():
                break
            if row.get("checked_at") and str(row["checked_at"]) > cutoff:
                continue
            self.check_one(row, now_fn())
            checked += 1
            self._stop.wait(self.CHECK_SPACING_SECONDS)
        return checked

    def check_one(self, row: dict[str, Any], now: str) -> bool:
        """One upstream lookup for one watch. True when reachable."""
        try:
            sha = self._fetch_upstream_sha(row["repo_id"])
        except Exception:
            self.database.record_watch_failure(row["id"], now)
            return False
        copy = self.database.get_local_model(row["repo_id"])
        observation = poll_observation(row, copy, str(sha or "").strip() or None, now)
        self.database.record_watch_check(**observation)
        return True

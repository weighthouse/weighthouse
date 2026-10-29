from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from huggingface_hub import snapshot_download
from pathlib import Path
from typing import Any

from .brand import BRAND
from .config import Settings, repository_path, validate_repo_id
from .database import Database
from .hub_service import HubService
from .indexer import LocalModelIndexer, directory_stats
from .manifests import read_manifest, write_manifest
from .planning import (
    DOWNLOAD_MODES,
    DownloadPlan,
    PlanError,
    SelectionError,
    plan_download,
    plan_from_payload,
    resolve_selection,
)
from .storage import FilesystemModelStorage
from .updates import merge_update_manifest

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

class DownloadCancelled(Exception):
    pass

class DownloadWorkerExit(RuntimeError):
    """The download worker subprocess exited non-zero.

    The message is the client's final words — the tail of its combined
    stdout/stderr — when it printed any. A process killed by SIGKILL
    cannot print anything, so that specific case gets a message that
    says what it means (usually an out-of-memory kill); other silent
    exits keep a line naming the exit code. The job record also keeps
    the tail whenever one exists.
    """

    _SIGKILL_MESSAGE = (
        "The download process was killed by the operating system (SIGKILL) "
        "before it could report an error. This usually means the server or "
        "container ran out of memory while transferring. See \"Performance "
        "and resources\" in the Downloads documentation."
    )

    def __init__(self, returncode: int, output: str | None):
        self.returncode = returncode
        self.output = output
        if output:
            super().__init__(output)
        elif returncode == -9:
            super().__init__(self._SIGKILL_MESSAGE)
        else:
            super().__init__(f"Download worker exited with code {returncode}.")

class DownloadManager:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        hub: HubService,
        indexer: LocalModelIndexer,
        model_storage: FilesystemModelStorage | None = None,
    ):
        self.settings = settings
        self.database = database
        self.hub = hub
        self.indexer = indexer
        self.model_storage = model_storage or FilesystemModelStorage(settings)
        self.executor = ThreadPoolExecutor(
            max_workers=settings.max_concurrent_downloads,
            thread_name_prefix=f"{BRAND.slug}-download",
        )
        self._submitted: set[str] = set()
        self._cancel_events: dict[str, threading.Event] = {}
        # Jobs whose stop was a pause, not a cancel: the worker teardown
        # writes "paused" state instead of "cancelled" and leaves no
        # cancelled manifest on the target directory.
        self._pause_requested: set[str] = set()
        self._processes: dict[str, subprocess.Popen[str]] = {}
        self._lock = threading.RLock()
        # The completion write and the cancellation write both do a
        # check-then-write on the same manifest file; serializing them
        # means a cancel racing a finish cannot flip a completed copy's
        # record (or vice versa) until the next rescan.
        self._manifest_lock = threading.Lock()

    def queue(
        self,
        repo_id: str,
        revision: str = "main",
        mode: str = "full",
        user_id: str | None = None,
        gguf_path: str | None = None,
        exclude_unsafe: bool = False,
        custom_include: list[str] | None = None,
        custom_exclude: list[str] | None = None,
        update_merge: bool = False,
    ) -> dict[str, Any]:
        validated = validate_repo_id(repo_id)
        active = self.database.find_active_download(validated)
        if active:
            if active.get("user_id") != user_id:
                raise ValueError("This model is already being downloaded by another account.")
            if active["status"] == "paused":
                # Re-queueing a model with a paused job resumes that job
                # instead of stacking a second one on the same target.
                return self.resume(active["id"])
            return active
        try:
            selection = resolve_selection(
                mode,
                gguf_path=gguf_path,
                custom_include=custom_include,
                custom_exclude=custom_exclude,
                exclude_unsafe=exclude_unsafe,
            )
        except SelectionError as error:
            raise ValueError(str(error)) from error
        target = repository_path(validated, self.settings.model_storage)
        created = now_iso()
        requested_revision = revision.strip() or "main"
        try:
            details = self.hub.model_details(validated, requested_revision)
        except Exception as error:
            raise ValueError(str(error)) from error
        try:
            plan, metadata = self._plan(validated, requested_revision, selection, target, details)
        except PlanError as error:
            raise ValueError(str(error)) from error
        except Exception as error:
            # Fail fast on gated repositories: the card says so, so the
            # error can say so too instead of leaking a raw HTTP status.
            if details.get("gated") and self._is_auth_error(error):
                raise ValueError(self._gated_access_message(validated, details)) from error
            code = self._http_error_code(str(error))
            if details.get("gated") and code and code.startswith("5"):
                raise ValueError(
                    self._gated_possible_message(validated, details.get("source_url"), code)
                ) from error
            raise
        payload = {
            "mode": selection.mode,
            "allow_patterns": selection.allow_patterns,
            "ignore_patterns": selection.ignore_patterns,
            "metadata": metadata,
            "governance": {
                "max_attempts": max(1, self.settings.download_max_attempts),
                "workers_per_job": self.settings.download_workers_per_job,
                "max_parallel_jobs": self.settings.max_concurrent_downloads,
            },
            "plan": plan.to_payload(),
        }
        if update_merge:
            # Tracked-update job: runs against an existing library copy,
            # keeps its manifest in place during the transfer and merges
            # the plan into it on completion.
            payload["update_merge"] = True
        record = {
            "id": uuid.uuid4().hex,
            "repo_id": validated,
            "revision": revision.strip() or "main",
            "status": "queued",
            "total_bytes": plan.selected_bytes,
            "downloaded_bytes": 0,
            "progress": 0,
            "speed_bps": 0,
            "error": None,
            "target_path": str(target),
            "payload_json": json.dumps(payload),
            "metadata_json": "{}",
            "created_at": created,
            "updated_at": created,
            "completed_at": None,
            "user_id": user_id,
        }
        download = self.database.create_download(record)
        self._dispatch()
        return download

    def _plan(
        self,
        repo_id: str,
        revision: str,
        selection: Any,
        target: Path,
        details: dict[str, Any],
    ) -> tuple[DownloadPlan, dict[str, Any]]:
        """Resolve the revision and freeze the dry-run into a plan.

        Returns the plan plus the card metadata captured at the same
        moment, so the worker and the final manifest work from a single
        frozen snapshot of the revision. The caller fetches the details
        once so a planning failure can still read them (e.g. the
        ``gated`` flag) when it maps the error.
        """
        sha = str(details.get("sha") or "").strip()
        if not sha:
            raise PlanError("The repository revision could not be resolved to a commit.")
        metadata = {
            "pipeline_tag": details.get("pipeline_tag"),
            "library_name": details.get("library_name"),
            "license": details.get("license"),
            "gated": details.get("gated"),
            "source_url": details.get("source_url"),
            "card_file_count": len(details.get("files") or []),
        }
        dry_run_files = self._dry_run(repo_id, sha, selection, target)
        blobs = {
            str(item.get("path") or ""): str(item.get("blob_id") or "")
            for item in details.get("files") or []
            if item.get("blob_id")
        }
        plan = plan_download(
            requested_revision=revision,
            resolved_sha=sha,
            selection=selection,
            dry_run_files=dry_run_files,
            blob_by_path=blobs,
        )
        return plan, metadata

    def _dry_run(
        self,
        repo_id: str,
        sha: str,
        selection: Any,
        target: Path,
    ) -> list[Any]:
        headers = {"user-agent": f"{BRAND.slug}/{self.settings.app_version}"}
        return snapshot_download(
            repo_id=repo_id,
            revision=sha,
            local_dir=target,
            token=self.hub.settings.hf_token or False,
            endpoint=self.hub.settings.hf_endpoint,
            allow_patterns=selection.allow_patterns or None,
            ignore_patterns=selection.ignore_patterns or None,
            max_workers=1,
            headers=headers,
            dry_run=True,
        )

    def _submit(self, download_id: str) -> bool:
        with self._lock:
            if download_id in self._submitted:
                return True
            self._submitted.add(download_id)
            self._cancel_events[download_id] = threading.Event()
        try:
            self.executor.submit(self._run, download_id)
        except RuntimeError:
            # The pool is shutting down (server stop): the job stays
            # queued in the database and the next start re-dispatches it.
            with self._lock:
                self._submitted.discard(download_id)
                self._cancel_events.pop(download_id, None)
            return False
        return True

    def _dispatch(self) -> None:
        """Start queued jobs while transfer slots are free.

        Jobs wait in the database as ``queued`` (not in the pool), so the
        queue has a real, visible order: moved-to-front first, then
        first-come. Every completion, pause, cancel, and new queue entry
        funnels through here, which is what makes a pause backfill the
        freed slot.
        """
        capacity = max(1, self.settings.max_concurrent_downloads)
        while True:
            with self._lock:
                running = len(self._submitted)
            if running >= capacity:
                return
            chosen = None
            with self._lock:
                for job in self.database.queued_downloads():
                    if job["id"] not in self._submitted:
                        chosen = job
                        break
            if chosen is None:
                return
            if not self._submit(chosen["id"]):
                # The pool is gone (shutdown in progress): no further
                # dispatch can succeed until the next start.
                return

    def pause(self, download_id: str) -> dict[str, Any] | None:
        download = self.database.get_download(download_id)
        if not download:
            return None
        status = download["status"]
        if status not in {"queued", "preparing", "downloading"}:
            raise ValueError(f"Download is already {status}.")
        paused_at = now_iso()
        if status == "queued":
            # Never left the database: nothing is running to stop.
            return self.database.update_download(
                download_id, status="paused", speed_bps=0, updated_at=paused_at
            )
        # Same stop mechanics as cancel(); only the final state and the
        # manifest behavior differ (see below). The worker thread owns
        # the process wait, as it does for cancels.
        with self._lock:
            self._pause_requested.add(download_id)
            cancel_event = self._cancel_events.setdefault(download_id, threading.Event())
            cancel_event.set()
            process = self._processes.get(download_id)
            if process and process.poll() is None:
                process.terminate()
        # Unlike a cancel this writes no cancelled manifest: a paused
        # copy is still a live copy, and a resume completes the same one.
        target = Path(download["target_path"]) if download.get("target_path") else None
        if target and target.is_dir():
            partial_bytes, _, _ = directory_stats(target)
        else:
            partial_bytes = int(download.get("downloaded_bytes") or 0)
        return self.database.update_download(
            download_id,
            status="paused",
            downloaded_bytes=partial_bytes,
            speed_bps=0,
            error=None,
            updated_at=paused_at,
        )

    def resume(self, download_id: str) -> dict[str, Any] | None:
        download = self.database.get_download(download_id)
        if not download or download["status"] != "paused":
            return download
        updated = self.database.update_download(
            download_id, status="queued", error=None, updated_at=now_iso()
        )
        self._dispatch()
        return updated

    def pause_all(self) -> list[dict[str, Any]]:
        paused: list[dict[str, Any]] = []
        # Flip the queued ones first so no dispatch races a running pause
        # for a slot; then stop the running ones.
        for job in self.database.unfinished_downloads():
            if job["status"] == "queued":
                result = self.database.update_download(
                    job["id"], status="paused", speed_bps=0, updated_at=now_iso()
                )
            else:
                result = self.pause(job["id"])
            if result:
                paused.append(result)
        return paused

    def resume_all(self) -> list[dict[str, Any]]:
        resumed: list[dict[str, Any]] = []
        for job in self.database.paused_downloads():
            result = self.resume(job["id"])
            if result:
                resumed.append(result)
        return resumed

    def restart(self, download_id: str) -> dict[str, Any] | None:
        """Re-queue a failed job in place: same record, error cleared.

        Files already on disk stay and the worker skips them, so the
        retained bytes are kept and the progress bar starts fresh.
        """
        download = self.database.get_download(download_id)
        if not download or download["status"] != "failed":
            return download
        updated = self.database.update_download(
            download_id,
            status="queued",
            progress=0,
            error=None,
            completed_at=None,
            updated_at=now_iso(),
        )
        self._dispatch()
        return updated

    def move_to_front(self, download_id: str) -> dict[str, Any] | None:
        download = self.database.get_download(download_id)
        if not download or download["status"] != "queued":
            return download
        updated = self.database.bump_download_priority(download_id)
        self._dispatch()
        return updated

    def cancel(self, download_id: str) -> dict[str, Any] | None:
        download = self.database.get_download(download_id)
        if not download:
            return None
        if download["status"] not in {"queued", "preparing", "downloading", "paused"}:
            raise ValueError(f"Download is already {download['status']}.")

        cancelled_at = now_iso()
        with self._lock:
            cancel_event = self._cancel_events.setdefault(download_id, threading.Event())
            cancel_event.set()
            process = self._processes.get(download_id)
            if process and process.poll() is None:
                process.terminate()

        target = Path(download["target_path"]) if download.get("target_path") else None
        if target and target.is_dir():
            with self._manifest_lock:
                partial_bytes, _, _ = directory_stats(target)
                if self._should_write_cancelled_manifest(target):
                    try:
                        write_manifest(
                            target,
                            {
                                "status": "cancelled",
                                "repo_id": download["repo_id"],
                                "revision": download["revision"],
                                "cancelled_at": cancelled_at,
                                "partial_bytes": partial_bytes,
                            },
                        )
                    except OSError:
                        pass
        else:
            partial_bytes = int(download.get("downloaded_bytes") or 0)

        return self.database.update_download(
            download_id,
            status="cancelled",
            downloaded_bytes=partial_bytes,
            speed_bps=0,
            error=None,
            updated_at=cancelled_at,
            completed_at=cancelled_at,
        )

    def resume_unfinished(self) -> None:
        jobs = self.database.unfinished_downloads()
        for download in jobs:
            self.database.update_download(
                download["id"],
                status="queued",
                error=None,
                updated_at=now_iso(),
            )
        if jobs:
            self._dispatch()

    def _monitor(
        self,
        download_id: str,
        target: Path,
        total_bytes: int,
        stop: threading.Event,
        cancel_event: threading.Event,
    ) -> None:
        last_bytes = 0
        last_time = time.monotonic()
        speed = 0.0
        while not stop.wait(1.25):
            if cancel_event.is_set():
                return
            current, _, _ = directory_stats(target, include_cache=True)
            current_time = time.monotonic()
            elapsed = max(current_time - last_time, 0.001)
            instant_speed = max(0, current - last_bytes) / elapsed
            speed = instant_speed if speed == 0 else speed * 0.6 + instant_speed * 0.4
            progress = min(99.5, current * 100 / total_bytes) if total_bytes else 0
            self.database.update_download(
                download_id,
                status="downloading",
                downloaded_bytes=current,
                progress=round(progress, 2),
                speed_bps=round(speed, 2),
                updated_at=now_iso(),
            )
            last_bytes = current
            last_time = current_time

    @staticmethod
    def _should_write_cancelled_manifest(target: Path) -> bool:
        """A directory still carrying a complete manifest keeps it."""
        # Tracked updates (and re-downloads over an existing copy) must
        # not replace the library record with a cancellation note.
        return read_manifest(target).get("status") != "complete"

    @staticmethod
    def _raise_if_cancelled(cancel_event: threading.Event) -> None:
        if cancel_event.is_set():
            raise DownloadCancelled()

    def _retry_delay(self, attempt: int) -> float:
        return min(30.0, self.settings.download_retry_seconds * attempt)

    @staticmethod
    def _is_transient(error: Exception) -> bool:
        """Classify a worker failure: bounded retries are for transient faults.

        Deterministic failures (auth, gating, missing repository/revision/
        file) surface immediately; connection-type faults are retried.
        """
        message = f"{type(error).__name__}: {error}".lower()
        non_retryable = (
            "401",
            "403",
            "gated",
            "accept the model terms",
            "unauthorized",
            "access denied",
            "not found",
            "does not exist",
            "invalid revision",
        )
        if any(marker in message for marker in non_retryable):
            return False
        transient = (
            "timed out",
            "timeout",
            "temporary",
            "connection",
            "unreachable",
            "reset by peer",
            "broken pipe",
            "server disconnected",
            "read error",
            "429",
            "too many requests",
            "rate limit",
            "backoff",
        )
        return any(marker in message for marker in transient)

    def _worker_command(self, download: dict[str, Any], target: Path) -> list[str]:
        """Build the worker subprocess argv; pure, so it stays testable."""
        payload = download.get("payload") or {}
        revision = (payload.get("plan") or {}).get("resolved_sha") or download["revision"]
        return [
            sys.executable,
            "-m",
            "app.download_worker",
            "--repo-id",
            download["repo_id"],
            "--revision",
            revision,
            "--target",
            str(target),
            "--endpoint",
            self.settings.hf_endpoint,
            "--allow-patterns",
            json.dumps(payload.get("allow_patterns") or []),
            "--ignore-patterns",
            json.dumps(payload.get("ignore_patterns") or []),
            "--workers",
            str(self.settings.download_workers_per_job),
        ]

    def xet_log_path(self, download_id: str) -> Path:
        """The Xet client's per-job report (its own memory, CPU, and
        network monitors) lands here when ENABLE_XET is on."""
        return self.settings.data_dir / "xet" / f"{download_id}.log"

    def _worker_environment(self, download_id: str) -> dict[str, str]:
        """Build the worker subprocess env; pure, so it stays testable.

        The default transport is plain HTTP streaming: the Xet client
        buffers downloads in memory, so it is admitted only when the
        deployment opts in with ENABLE_XET=1.
        """
        environment = os.environ.copy()
        environment["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
        if self.settings.hf_token:
            environment["HF_TOKEN"] = self.settings.hf_token
        if self.settings.enable_xet:
            self.xet_log_path(download_id).parent.mkdir(parents=True, exist_ok=True)
            environment["HF_XET_LOG_DEST"] = str(self.xet_log_path(download_id))
        else:
            environment["HF_HUB_DISABLE_XET"] = "1"
        return environment

    def _download_snapshot(
        self,
        download_id: str,
        download: dict[str, Any],
        target: Path,
        cancel_event: threading.Event,
    ) -> None:
        # With a frozen plan the worker executes the pinned SHA, never the
        # possibly-moved branch label the job was queued with.
        command = self._worker_command(download, target)
        environment = self._worker_environment(download_id)

        with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as worker_output:
            process = subprocess.Popen(
                command,
                stdout=worker_output,
                stderr=worker_output,
                text=True,
                env=environment,
            )
            with self._lock:
                self._processes[download_id] = process
            try:
                while process.poll() is None:
                    if cancel_event.wait(0.25):
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=5)
                        raise DownloadCancelled()
                if process.returncode != 0:
                    worker_output.seek(0)
                    # The final error sits at the end of the stream; the
                    # job record carries the tail for diagnosis.
                    tail = worker_output.read().strip()[-4096:]
                    raise DownloadWorkerExit(process.returncode, tail)
            finally:
                with self._lock:
                    self._processes.pop(download_id, None)

    def _run(self, download_id: str) -> None:
        with self._lock:
            cancel_event = self._cancel_events.setdefault(download_id, threading.Event())
        attempts_made = 0
        try:
            download = self.database.get_download(download_id)
            if not download:
                return
            if download["status"] != "queued":
                # Paused between dispatch and start: the job waits for a
                # resume, and the slot frees in the finally block.
                return
            self._raise_if_cancelled(cancel_event)
            repo_id = validate_repo_id(download["repo_id"])
            target = repository_path(repo_id, self.settings.model_storage)
            target.mkdir(parents=True, exist_ok=True)
            self.database.update_download(
                download_id,
                status="preparing",
                error=None,
                updated_at=now_iso(),
            )
            self._raise_if_cancelled(cancel_event)
            payload = download.get("payload") or {}
            update_merge = bool(payload.get("update_merge"))
            plan_payload = payload.get("plan")
            plan = plan_from_payload(plan_payload) if plan_payload else None
            if plan is not None:
                metadata = dict(payload.get("metadata") or {})
                total_bytes = plan.selected_bytes
                resolved_sha = plan.sha
                requested_revision = plan.requested_revision
                database_metadata = {
                    **metadata,
                    "file_count": len(plan.files),
                    "requested_revision": requested_revision,
                    "resolved_sha": resolved_sha,
                    "selected_bytes": plan.selected_bytes,
                    "transfer_bytes": plan.transfer_bytes,
                    "cached_bytes": plan.cached_bytes,
                }
            else:
                # Legacy queued rows from before planning keep working: they
                # resolve and gather metadata at run time exactly as before.
                details = self.hub.model_details(repo_id, download["revision"])
                self._raise_if_cancelled(cancel_event)
                total_bytes = int(details.get("total_bytes") or 0)
                resolved_sha = details.get("sha")
                requested_revision = download["revision"]
                database_metadata = {
                    "pipeline_tag": details.get("pipeline_tag"),
                    "library_name": details.get("library_name"),
                    "license": details.get("license"),
                    "file_count": len(details.get("files") or []),
                    "gated": details.get("gated"),
                    "source_url": details.get("source_url"),
                }
            # The transport that will run is recorded on the job so a
            # failure can be read against it: Xet buffers downloads in
            # memory, the HTTP stream does not.
            engine = "xet" if self.settings.enable_xet else "http"
            database_metadata["engine"] = engine
            if engine == "xet":
                database_metadata["xet_log"] = str(self.xet_log_path(download_id))
            self.database.update_download(
                download_id,
                total_bytes=total_bytes,
                metadata_json=json.dumps(database_metadata),
                updated_at=now_iso(),
            )
            manifest_metadata = database_metadata
            if not update_merge:
                # Tracked-update jobs leave the copy's complete manifest
                # in place during the transfer; a crashed or cancelled
                # update must not destroy the library record.
                write_manifest(
                    target,
                    {
                        "status": "downloading",
                        "repo_id": repo_id,
                        "revision": requested_revision,
                        "sha": resolved_sha,
                        "source_url": manifest_metadata.get("source_url"),
                        "started_at": download["created_at"],
                    },
                )
            stop = threading.Event()
            monitor = threading.Thread(
                target=self._monitor,
                args=(download_id, target, total_bytes, stop, cancel_event),
                name=f"{BRAND.slug}-progress-{download_id[:8]}",
                daemon=True,
            )
            monitor.start()
            attempts_allowed = max(1, self.settings.download_max_attempts)
            final_error: Exception | None = None
            for attempt in range(1, attempts_allowed + 1):
                attempts_made = attempt
                try:
                    self._download_snapshot(download_id, download, target, cancel_event)
                    final_error = None
                    break
                except DownloadCancelled:
                    raise
                except Exception as error:  # transparent classification below
                    final_error = error
                    if attempt >= attempts_allowed or not self._is_transient(error):
                        break
                    delay = self._retry_delay(attempt)
                    try:
                        self.database.update_download(
                            download_id,
                            error=(
                                f"attempt {attempt}/{attempts_allowed} failed, retrying: "
                                f"{error}"
                            )[:2000],
                            updated_at=now_iso(),
                        )
                    except Exception:
                        pass
                    if cancel_event.wait(delay):
                        raise DownloadCancelled() from error
            try:
                if final_error is not None:
                    raise final_error
            finally:
                stop.set()
                monitor.join(timeout=3)
            self._raise_if_cancelled(cancel_event)
            final_size, file_count, _ = directory_stats(target)
            completed_at = now_iso()
            if update_merge and plan is not None:
                manifest_document = merge_update_manifest(
                    read_manifest(target),
                    plan.to_payload(),
                    writer_version=self.settings.app_version,
                    written_at=completed_at,
                    disk_file_count=file_count,
                    disk_total_bytes=final_size,
                )
            else:
                manifest_document = {
                    "status": "complete",
                    "repo_id": repo_id,
                    "revision": requested_revision,
                    "sha": resolved_sha,
                    "downloaded_at": completed_at,
                    "source_url": manifest_metadata.get("source_url"),
                    "pipeline_tag": manifest_metadata.get("pipeline_tag"),
                    "library_name": manifest_metadata.get("library_name"),
                    "license": manifest_metadata.get("license"),
                    "tags": manifest_metadata.get("tags") or [],
                    "gated": manifest_metadata.get("gated"),
                    "total_bytes": final_size,
                    "file_count": file_count,
                    "writer": {
                        "app_version": self.settings.app_version,
                        "schema_version": 2,
                        "written_at": completed_at,
                    },
                    **(
                        {
                            "requested_revision": plan.requested_revision,
                            "selection": {
                                **plan.to_payload()["selection"],
                                # Recorded so update diffs know which
                                # upstream additions the original mode
                                # would have covered.
                                "patterns": {
                                    "mode": plan.mode,
                                    "allow": plan.allow_patterns,
                                    "ignore": plan.ignore_patterns,
                                },
                            },
                        }
                        if plan
                        else {}
                    ),
                }
            # The completion note is authoritative and unconditional; the
            # lock only needs to make it atomic against a cancellation
            # check-then-write so the two cannot interleave.
            with self._manifest_lock:
                write_manifest(target, manifest_document)
            final_size, _, _ = directory_stats(target)
            self.model_storage.sync_repository(repo_id, target)
            self.indexer.index_path(target)
            self.database.update_download(
                download_id,
                status="complete",
                total_bytes=total_bytes or final_size,
                downloaded_bytes=final_size,
                progress=100,
                speed_bps=0,
                error=None,
                updated_at=completed_at,
                completed_at=completed_at,
            )
        except DownloadCancelled:
            current = self.database.get_download(download_id)
            # cancel() and pause() both write their final state before
            # the worker teardown lands here; this only covers a stop
            # that took effect before either could write.
            if current and current["status"] not in ("cancelled", "paused"):
                cancelled_at = now_iso()
                target = Path(current["target_path"]) if current.get("target_path") else None
                partial_bytes = directory_stats(target)[0] if target and target.is_dir() else 0
                self.database.update_download(
                    download_id,
                    status="cancelled",
                    downloaded_bytes=partial_bytes,
                    speed_bps=0,
                    error=None,
                    updated_at=cancelled_at,
                    completed_at=cancelled_at,
                )
        except Exception as error:
            current = self.database.get_download(download_id)
            if current and current["status"] in ("cancelled", "paused"):
                return
            message, metadata = self._failure_message(error, current, attempts_made)
            self.database.update_download(
                download_id,
                status="failed",
                speed_bps=0,
                error=message[:2000],
                metadata_json=json.dumps(metadata),
                updated_at=now_iso(),
                completed_at=now_iso(),
            )
        finally:
            with self._lock:
                self._submitted.discard(download_id)
                self._cancel_events.pop(download_id, None)
                self._processes.pop(download_id, None)
            # A finished job frees its transfer slot; the next queued job
            # (move-to-front first, then first-come) takes it.
            self._dispatch()

    def _failure_message(
        self,
        error: Exception,
        current: dict[str, Any] | None,
        attempts_made: int,
    ) -> tuple[str, dict[str, Any]]:
        """Map a job failure to the message the row shows.

        Gating is the one failure the app can explain: the frozen plan
        records the repository's ``gated`` flag, and the Hub's own
        401/403 replies say it too. A confirmed gate gets the definitive
        wording; a bare 5xx gets the "if this model is gated" wording.
        Everything else keeps the raw message.
        """
        message = str(error).strip() or error.__class__.__name__
        # Keep the client's final words with the job (engine and the
        # Xet log path were already recorded at run start).
        metadata = dict(current.get("metadata") or {}) if current else {}
        if isinstance(error, DownloadWorkerExit) and error.output:
            metadata["output_tail"] = error.output
        repo_id = (current or {}).get("repo_id", "")
        if metadata.get("gated") or self._is_auth_error(error):
            return self._gated_worker_message(repo_id, metadata.get("source_url")), metadata
        code = self._http_error_code(message)
        if code and code.startswith("5"):
            return (
                self._gated_possible_message(repo_id, metadata.get("source_url"), code),
                metadata,
            )
        if attempts_made > 1:
            message = f"failed after {attempts_made} attempts: {message}"
        return message, metadata

    @staticmethod
    def _is_auth_error(error: Exception) -> bool:
        text = f"{type(error).__name__}: {error}".lower()
        markers = (
            "401",
            "403",
            "unauthorized",
            "forbidden",
            "gated",
            "accept the model terms",
            "don't have permission",
            "do not have permission",
        )
        return any(marker in text for marker in markers)

    @staticmethod
    def _http_error_code(message: str) -> str | None:
        match = re.search(r"(?:status|HTTP)\s*(\d{3})\b", message, re.IGNORECASE)
        if match:
            return match.group(1)
        match = re.search(r"\b([45]\d{2})\b", message)
        return match.group(1) if match else None

    @staticmethod
    def _model_terms_url(repo_id: str, source_url: Any) -> str:
        url = str(source_url or "").strip()
        return url or f"https://huggingface.co/{repo_id}"

    @classmethod
    def _gated_access_message(cls, repo_id: str, details: dict[str, Any]) -> str:
        # Copy #26: planning a gated repository the token cannot read.
        link = cls._model_terms_url(repo_id, details.get("source_url"))
        return (
            "This repository is gated and the configured Hub token is not authorized. "
            f"Accept the model's terms at {link}, then check the token in "
            "Settings / Hub access."
        )

    @classmethod
    def _gated_worker_message(cls, repo_id: str, source_url: Any) -> str:
        # Copy #27: the transfer itself was refused on gate grounds.
        link = cls._model_terms_url(repo_id, source_url)
        return (
            "This repository is gated and the configured Hub token is not authorized. "
            f"Accept the model's terms at {link} and use an approved account's read "
            "token (Settings / Hub access)."
        )

    @classmethod
    def _gated_possible_message(cls, repo_id: str, source_url: Any, code: str) -> str:
        # Copy #28: a 5xx says nothing about gating by itself, so the
        # message offers it as the likely cause rather than asserting it.
        link = cls._model_terms_url(repo_id, source_url)
        return (
            f"The Hub returned HTTP {code} while starting this download. "
            f"If this model is gated, accept its terms at {link} and check the "
            "Hub token in Settings / Hub access."
        )

    def shutdown(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=False)

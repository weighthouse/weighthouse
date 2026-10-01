import json
import time
from dataclasses import dataclass

import pytest

from app.manifests import MANIFEST_FILENAME
from app.planning import (
    METADATA_PATTERNS,
    UNSAFE_PATTERNS,
    DownloadPlan,
    PlanError,
    Selection,
    SelectionError,
    plan_download,
    plan_from_payload,
    resolve_selection,
    selection_covers,
)


@dataclass
class FakeDryRunFile:
    filename: str
    file_size: int
    commit_hash: str = "sha-abc"
    is_cached: bool = False


def test_full_mode_selects_everything_with_no_patterns():
    selection = resolve_selection("full")

    assert selection.allow_patterns == []
    assert selection.ignore_patterns == []
    assert selection.to_payload() == {
        "mode": "full",
        "allow_patterns": [],
        "ignore_patterns": [],
        "gguf_path": None,
        "exclude_unsafe": False,
    }


def test_safetensors_mode_matches_upstream_semantics():
    selection = resolve_selection("safetensors")

    assert selection.allow_patterns == ["*.safetensors", *METADATA_PATTERNS]
    assert selection_covers(selection, "model.safetensors")
    assert selection_covers(selection, "config.json")
    assert selection_covers(selection, "tokenizer/tokenizer.json")
    assert not selection_covers(selection, "pytorch_model.bin")
    assert selection_covers(selection, "sub/dir/other.md")


def test_metadata_mode_excludes_weights():
    selection = resolve_selection("metadata")

    assert selection.allow_patterns == METADATA_PATTERNS
    assert selection_covers(selection, "LICENSE.txt")
    assert selection_covers(selection, "tok.model")
    assert not selection_covers(selection, "weights.gguf")
    assert not selection_covers(selection, "model.safetensors")


def test_gguf_mode_requires_and_pins_one_file():
    with pytest.raises(SelectionError, match="GGUF file must be selected"):
        resolve_selection("gguf")
    with pytest.raises(SelectionError, match="GGUF file must be selected"):
        resolve_selection("gguf", gguf_path="   ")

    selection = resolve_selection("gguf", gguf_path="sub/model-Q4_K_M.gguf")
    assert selection.gguf_path == "sub/model-Q4_K_M.gguf"
    assert selection.allow_patterns == ["sub/model-Q4_K_M.gguf", *METADATA_PATTERNS]
    assert selection_covers(selection, "sub/model-Q4_K_M.gguf")
    assert selection_covers(selection, "config.json")
    assert not selection_covers(selection, "other/model-Q8.gguf")
    assert selection.to_payload()["gguf_path"] == "sub/model-Q4_K_M.gguf"


def test_custom_mode_parses_patterns_and_excludes():
    selection = resolve_selection(
        "custom",
        custom_include=["*.safetensors , *.json", "", "*.safetensors"],
        custom_exclude=["*.bin, *.ckpt"],
    )

    assert selection.allow_patterns == ["*.safetensors", "*.json"]
    assert selection.ignore_patterns == ["*.bin", "*.ckpt"]
    assert selection_covers(selection, "a/model.safetensors")
    assert not selection_covers(selection, "old.bin")


def test_custom_mode_requires_included_pattern():
    with pytest.raises(SelectionError, match="at least one included pattern"):
        resolve_selection("custom", custom_include=["  "])


def test_exclude_unsafe_applies_to_every_mode_that_uses_it():
    selection = resolve_selection("full", exclude_unsafe=True)
    assert selection.ignore_patterns == UNSAFE_PATTERNS
    assert selection_covers(selection, "weights.safetensors")
    assert not selection_covers(selection, "weights.bin")

    metadata = resolve_selection("metadata", exclude_unsafe=True)
    assert metadata.ignore_patterns == UNSAFE_PATTERNS

    custom = resolve_selection("custom", custom_include=["*"], exclude_unsafe=True)
    assert custom.ignore_patterns == UNSAFE_PATTERNS


def test_mode_parameters_are_rejected_elsewhere():
    with pytest.raises(SelectionError):
        resolve_selection("full", gguf_path="x.gguf")
    with pytest.raises(SelectionError):
        resolve_selection("safetensors", custom_include=["*"])
    with pytest.raises(SelectionError):
        resolve_selection("gguf", gguf_path="x.gguf", custom_exclude=["*.bin"])
    with pytest.raises(SelectionError, match="only valid"):
        resolve_selection("custom", gguf_path="x.gguf")
    with pytest.raises(SelectionError, match="Unknown download mode"):
        resolve_selection("piecemeal")


def test_pattern_lists_are_capped():
    with pytest.raises(SelectionError, match="50"):
        resolve_selection("custom", custom_include=[f"*{i}.safetensors" for i in range(60)])


def test_plan_freezes_dry_run_files_and_splits_bytes():
    dry_run = [
        FakeDryRunFile("model.safetensors", 100, is_cached=False),
        FakeDryRunFile("config.json", 10, is_cached=True),
        FakeDryRunFile("LICENSE", 5, is_cached=False),
    ]
    selection = resolve_selection("safetensors", exclude_unsafe=True)

    plan = plan_download(
        requested_revision="main",
        resolved_sha="sha-abc",
        selection=selection,
        dry_run_files=dry_run,
        resolved_at="2026-09-26T00:00:00+00:00",
        planned_at="2026-09-26T00:00:01+00:00",
    )

    assert plan.sha == "sha-abc"
    assert plan.requested_revision == "main"
    assert plan.selected_bytes == 115
    assert plan.transfer_bytes == 105
    assert plan.cached_bytes == 10
    assert [item["path"] for item in plan.files] == [
        "model.safetensors",
        "config.json",
        "LICENSE",
    ]
    assert plan.to_payload()["selection"]["file_count"] == 3


def test_plan_rejects_dry_run_from_wrong_commit():
    selection = resolve_selection("metadata")

    with pytest.raises(PlanError, match="different commit"):
        plan_download(
            requested_revision="main",
            resolved_sha="sha-abc",
            selection=selection,
            dry_run_files=[FakeDryRunFile("README.md", 100, commit_hash="sha-other")],
        )


def test_plan_payload_round_trips_through_json():
    selection = resolve_selection("gguf", gguf_path="m.gguf")
    plan = plan_download(
        requested_revision="main",
        resolved_sha="sha-abc",
        selection=selection,
        dry_run_files=[FakeDryRunFile("m.gguf", 7)],
    )

    payload = json.loads(plan.to_json())
    rebuilt = plan_from_payload(payload)

    assert rebuilt == plan
    assert isinstance(rebuilt, DownloadPlan)
    assert payload["schema_version"] == 1
    assert payload["resolved_sha"] == "sha-abc"


def test_plan_from_payload_rejects_unknown_schema_version():
    with pytest.raises(PlanError, match="schema_version"):
        plan_from_payload({"schema_version": 99})


def test_plan_from_payload_rejects_incomplete_payload():
    with pytest.raises(PlanError, match="incomplete"):
        plan_from_payload({"schema_version": 1, "mode": "full"})


def test_queue_freezes_a_plan_and_records_selected_bytes(tmp_path):
    from app.config import Settings
    from app.database import Database
    from app.downloads import DownloadManager
    from app.indexer import LocalModelIndexer
    from app.storage import FilesystemModelStorage

    class Hub:
        settings = Settings(
            model_storage=(tmp_path / "models").resolve(),
            data_dir=(tmp_path / "data").resolve(),
        )

        @staticmethod
        def model_details(repo_id: str, revision: str):
            assert revision == "main"
            return {
                "sha": "sha-abc",
                "total_bytes": 999999,
                "pipeline_tag": "text-generation",
                "library_name": "transformers",
                "license": "mit",
                "files": [{"path": "model.safetensors", "size": 110, "blob_id": "blob-110"}],
                "gated": False,
                "source_url": "https://huggingface.co/acme/tiny",
            }

    settings = Hub.settings
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.initialize()
    manager = DownloadManager(
        settings,
        database,
        Hub(),
        LocalModelIndexer(settings, database),
        FilesystemModelStorage(settings),
    )
    manager._dry_run = lambda *args, **kwargs: [  # type: ignore[method-assign]
        FakeDryRunFile("model.safetensors", 110, is_cached=False),
        FakeDryRunFile("config.json", 10, is_cached=True),
    ]

    job = manager.queue("acme/tiny", "main", "safetensors")

    with database.connect() as connection:
        stored = connection.execute(
            "SELECT total_bytes, payload_json FROM downloads WHERE id = ?",
            (job["id"],),
        ).fetchone()
    assert stored is not None
    assert stored["total_bytes"] == 120
    payload = json.loads(stored["payload_json"])
    plan_payload = payload["plan"]
    assert plan_payload["requested_revision"] == "main"
    assert plan_payload["resolved_sha"] == "sha-abc"
    assert plan_payload["schema_version"] == 1
    assert plan_payload["mode"] == "safetensors"
    assert plan_payload["selection"]["transfer_bytes"] == 110
    assert plan_payload["selection"]["cached_bytes"] == 10
    assert payload["metadata"]["pipeline_tag"] == "text-generation"
    assert payload["allow_patterns"][0] == "*.safetensors"


def test_queue_rejects_gguf_mode_without_a_file(tmp_path):
    from app.config import Settings
    from app.database import Database
    from app.downloads import DownloadManager
    from app.indexer import LocalModelIndexer
    from app.storage import FilesystemModelStorage

    class Hub:
        settings = Settings(
            model_storage=(tmp_path / "models").resolve(),
            data_dir=(tmp_path / "data").resolve(),
        )

        @staticmethod
        def model_details(repo_id: str, revision: str):
            return {"sha": "sha-abc"}

    settings = Hub.settings
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.initialize()
    manager = DownloadManager(
        settings,
        database,
        Hub(),
        LocalModelIndexer(settings, database),
        FilesystemModelStorage(settings),
    )

    with pytest.raises(ValueError, match="GGUF file must be selected"):
        manager.queue("acme/tiny", "main", "gguf")


def _retry_manager(tmp_path, enable_xet: bool = False):
    import time as time_module

    from app.config import Settings
    from app.database import Database
    from app.downloads import DownloadManager
    from app.indexer import LocalModelIndexer
    from app.storage import FilesystemModelStorage

    class Hub:
        settings = Settings(
            model_storage=(tmp_path / "models").resolve(),
            data_dir=(tmp_path / "data").resolve(),
            enable_xet=enable_xet,
        )

        @staticmethod
        def model_details(repo_id: str, revision: str):
            return {
                "sha": "sha-abc",
                "pipeline_tag": "text-generation",
                "library_name": "transformers",
                "license": "mit",
                "files": [{"path": "model.safetensors", "size": 110, "blob_id": "blob-110"}],
                "gated": False,
                "source_url": "https://huggingface.co/acme/tiny",
            }

    settings = Hub.settings
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.initialize()
    manager = DownloadManager(
        settings,
        database,
        Hub(),
        LocalModelIndexer(settings, database),
        FilesystemModelStorage(settings),
    )
    manager._dry_run = lambda *args, **kwargs: [  # type: ignore[method-assign]
        FakeDryRunFile("model.safetensors", 110)
    ]
    manager._retry_delay = lambda attempt: 0  # type: ignore[method-assign]

    def await_terminal(download_id: str, timeout: float = 2.0):
        deadline = time_module.monotonic() + timeout
        while time_module.monotonic() < deadline:
            row = database.get_download(download_id)
            if row and row["status"] in {"complete", "failed", "cancelled"}:
                return row
            time_module.sleep(0.01)
        raise AssertionError("download did not reach a terminal status")

    return manager, database, await_terminal


def test_transient_snapshot_failures_are_retried_until_success(tmp_path):
    manager, database, await_terminal = _retry_manager(tmp_path)
    calls = {"count": 0}

    def flaky(download_id, download, target, cancel_event):
        calls["count"] += 1
        if calls["count"] < 3:
            raise RuntimeError("Connection error: failed to establish a connection")

    manager._download_snapshot = flaky  # type: ignore[method-assign]

    job = manager.queue("acme/tiny", "main", "full")
    finished = await_terminal(job["id"])

    assert finished["status"] == "complete"
    assert finished["error"] is None
    assert calls["count"] == 3


def test_deterministic_failure_fails_without_retry(tmp_path):
    manager, database, await_terminal = _retry_manager(tmp_path)
    calls = {"count": 0}

    def boom(download_id, download, target, cancel_event):
        calls["count"] += 1
        raise RuntimeError("Repository Not Found.")

    manager._download_snapshot = boom  # type: ignore[method-assign]

    job = manager.queue("acme/tiny", "main", "full")
    finished = await_terminal(job["id"])

    assert finished["status"] == "failed"
    assert calls["count"] == 1
    assert "Repository Not Found" in finished["error"]
    assert "failed after" not in finished["error"]


def test_retry_exhaustion_reports_the_attempt_count(tmp_path):
    manager, database, await_terminal = _retry_manager(tmp_path)
    calls = {"count": 0}

    def always_fails(download_id, download, target, cancel_event):
        calls["count"] += 1
        raise RuntimeError("connection error while fetching blob")

    manager._download_snapshot = always_fails  # type: ignore[method-assign]

    job = manager.queue("acme/tiny", "main", "full")
    finished = await_terminal(job["id"])

    assert finished["status"] == "failed"
    assert calls["count"] == manager.settings.download_max_attempts
    assert finished["error"].startswith(f"failed after {calls['count']} attempts")


def test_worker_command_pins_sha_and_worker_count(tmp_path):
    from test_planning import _retry_manager

    manager, database, _ = _retry_manager(tmp_path)
    job = manager.queue("acme/tiny", "main", "safetensors")
    with database.connect() as connection:
        row = connection.execute(
            "SELECT payload_json FROM downloads WHERE id = ?", (job["id"],)
        ).fetchone()

    download = {
        "repo_id": "acme/tiny",
        "revision": "main",
        "payload": json.loads(row["payload_json"]),
    }
    command = manager._worker_command(download, manager.settings.model_storage / "acme" / "tiny")

    revision_index = command.index("--revision") + 1
    assert command[revision_index] == "sha-abc"
    workers_index = command.index("--workers") + 1
    assert command[workers_index] == str(manager.settings.download_workers_per_job)
    allow_index = command.index("--allow-patterns") + 1
    assert json.loads(command[allow_index])[0] == "*.safetensors"


def test_worker_command_falls_back_to_requested_revision_without_plan(tmp_path):
    from test_planning import _retry_manager

    manager, database, _ = _retry_manager(tmp_path)
    command = manager._worker_command(
        {"repo_id": "acme/tiny", "revision": "main", "payload": {}},
        manager.settings.model_storage / "acme" / "tiny",
    )
    revision_index = command.index("--revision") + 1
    assert command[revision_index] == "main"


def test_worker_environment_disables_xet_by_default(tmp_path):
    manager, database, _ = _retry_manager(tmp_path)

    environment = manager._worker_environment("job-1")

    assert environment["HF_HUB_DISABLE_XET"] == "1"
    assert "HF_XET_LOG_DEST" not in environment
    assert environment["HF_HUB_DISABLE_PROGRESS_BARS"] == "1"


def test_worker_environment_admits_xet_when_enabled(tmp_path):
    manager, database, _ = _retry_manager(tmp_path, enable_xet=True)

    environment = manager._worker_environment("job-1")

    assert "HF_HUB_DISABLE_XET" not in environment
    expected_log = manager.settings.data_dir / "xet" / "job-1.log"
    assert environment["HF_XET_LOG_DEST"] == str(expected_log)
    assert expected_log.parent.is_dir()


def test_failed_worker_exit_records_engine_and_output_tail(tmp_path):
    from app.downloads import DownloadWorkerExit

    manager, database, await_terminal = _retry_manager(tmp_path)
    calls = {"count": 0}

    def fails_with_output(download_id, download, target, cancel_event):
        calls["count"] += 1
        raise DownloadWorkerExit(1, "ERROR: cannot fetch blob model-0001.safetensors")

    manager._download_snapshot = fails_with_output  # type: ignore[method-assign]

    job = manager.queue("acme/tiny", "main", "full")
    finished = await_terminal(job["id"])

    assert finished["status"] == "failed"
    assert calls["count"] == 1  # not transient: no retry
    assert "cannot fetch blob" in finished["error"]
    assert finished["metadata"]["engine"] == "http"
    assert finished["metadata"]["output_tail"] == (
        "ERROR: cannot fetch blob model-0001.safetensors"
    )


def test_worker_exit_message_variants():
    from app.downloads import DownloadWorkerExit

    # SIGKILL with no output: the message says what it means.
    assert str(DownloadWorkerExit(-9, None)) == (
        "The download process was killed by the operating system (SIGKILL) "
        "before it could report an error. This usually means the server or "
        'container ran out of memory while transferring. See "Performance '
        'and resources" in the Downloads documentation.'
    )
    # Another silent exit keeps the generic line.
    assert str(DownloadWorkerExit(1, None)) == "Download worker exited with code 1."
    # Any client output leads with the client's own words.
    assert str(DownloadWorkerExit(-9, "last line")) == "last line"


def test_killed_worker_reports_the_memory_message_on_the_job(tmp_path):
    from app.downloads import DownloadWorkerExit

    manager, database, await_terminal = _retry_manager(tmp_path)

    def killed_silently(download_id, download, target, cancel_event):
        # SIGKILL: the process dies before printing anything.
        raise DownloadWorkerExit(-9, None)

    manager._download_snapshot = killed_silently  # type: ignore[method-assign]

    job = manager.queue("acme/tiny", "main", "full")
    finished = await_terminal(job["id"])

    assert finished["status"] == "failed"
    assert "killed by the operating system (SIGKILL)" in finished["error"]
    assert "Performance and resources" in finished["error"]
    assert "output_tail" not in finished["metadata"]
    assert finished["metadata"]["engine"] == "http"


def test_run_records_xet_engine_and_log_path(tmp_path):
    manager, database, await_terminal = _retry_manager(tmp_path, enable_xet=True)

    def succeeds(download_id, download, target, cancel_event):
        (target / "model.safetensors").write_bytes(b"x" * 110)

    manager._download_snapshot = succeeds  # type: ignore[method-assign]

    job = manager.queue("acme/tiny", "main", "full")
    finished = await_terminal(job["id"])

    assert finished["status"] == "complete"
    assert finished["metadata"]["engine"] == "xet"
    assert finished["metadata"]["xet_log"] == str(
        manager.settings.data_dir / "xet" / f"{job['id']}.log"
    )


def test_governance_snapshot_recorded_in_job_history(tmp_path):
    from test_planning import _retry_manager

    manager, database, _ = _retry_manager(tmp_path)
    job = manager.queue("acme/tiny", "main", "full")
    with database.connect() as connection:
        row = connection.execute(
            "SELECT payload_json FROM downloads WHERE id = ?", (job["id"],)
        ).fetchone()

    governance = json.loads(row["payload_json"])["governance"]
    assert governance == {
        "max_attempts": manager.settings.download_max_attempts,
        "workers_per_job": manager.settings.download_workers_per_job,
        "max_parallel_jobs": manager.settings.max_concurrent_downloads,
    }


def _capped_manager(tmp_path, max_parallel: int):
    from dataclasses import replace

    manager, database, await_terminal = _retry_manager(tmp_path)
    manager.settings = replace(manager.settings, max_concurrent_downloads=max_parallel)
    manager.executor.shutdown(wait=False, cancel_futures=True)
    from concurrent.futures import ThreadPoolExecutor

    manager.executor = ThreadPoolExecutor(
        max_workers=manager.settings.max_concurrent_downloads,
        thread_name_prefix="mh-test-download",
    )
    return manager, database, await_terminal


def test_parallel_jobs_are_capped_by_max_concurrent(tmp_path):
    import threading

    manager, database, await_terminal = _capped_manager(tmp_path, max_parallel=1)
    release = threading.Event()
    inflight = {"count": 0, "max": 0}
    inflight_lock = threading.Lock()

    def blocking(_id, _download, _target, _cancel_event):
        with inflight_lock:
            inflight["count"] += 1
            inflight["max"] = max(inflight["max"], inflight["count"])
        release.wait(5)
        with inflight_lock:
            inflight["count"] -= 1

    manager._download_snapshot = blocking  # type: ignore[method-assign]

    first = manager.queue("acme/one", "main", "full")
    for _ in range(300):
        with inflight_lock:
            if inflight["count"] == 1:
                break
        time.sleep(0.01)
    else:
        raise AssertionError("first job never started")

    second = manager.queue("acme/two", "main", "full")
    for _ in range(30):  # confirm the second job waits while the first blocks
        with inflight_lock:
            if inflight["max"] > 1:
                raise AssertionError("parallel cap was exceeded")
        time.sleep(0.01)

    release.set()
    finished_first = await_terminal(first["id"])
    finished_second = await_terminal(second["id"])
    manager.shutdown()

    assert finished_first["status"] == "complete"
    assert finished_second["status"] == "complete"
    assert inflight["max"] == 1


def test_two_jobs_run_in_parallel_when_capacity_allows(tmp_path):
    import threading

    manager, database, await_terminal = _capped_manager(tmp_path, max_parallel=2)
    release = threading.Event()
    inflight = {"count": 0, "max": 0}
    inflight_lock = threading.Lock()

    def blocking(_id, _download, _target, _cancel_event):
        with inflight_lock:
            inflight["count"] += 1
            inflight["max"] = max(inflight["max"], inflight["count"])
        release.wait(5)
        with inflight_lock:
            inflight["count"] -= 1

    manager._download_snapshot = blocking  # type: ignore[method-assign]

    first = manager.queue("acme/one", "main", "full")
    second = manager.queue("acme/two", "main", "full")
    for _ in range(300):
        with inflight_lock:
            if inflight["max"] == 2:
                break
        time.sleep(0.01)
    else:
        raise AssertionError("second job never ran in parallel")

    release.set()
    await_terminal(first["id"])
    await_terminal(second["id"])
    manager.shutdown()

    assert inflight["max"] == 2


def test_cancel_during_retry_backoff_stops_cleanly(tmp_path):
    from test_planning import _retry_manager

    manager, database, await_terminal = _retry_manager(tmp_path)
    manager._retry_delay = lambda attempt: 0.5  # type: ignore[method-assign]
    calls = {"count": 0}

    def fails_once(_id, _download, _target, _cancel_event):
        calls["count"] += 1
        raise RuntimeError("connection error while fetching blob")

    manager._download_snapshot = fails_once  # type: ignore[method-assign]

    job = manager.queue("acme/tiny", "main", "full")
    for _ in range(300):
        row = database.get_download(job["id"])
        if row and (row["error"] or "").startswith("attempt 1/"):
            break
        time.sleep(0.01)
    else:
        raise AssertionError("retry status never became visible")

    cancelled = manager.cancel(job["id"])
    finished = await_terminal(job["id"])
    manager.shutdown()

    assert cancelled["status"] == "cancelled"
    assert finished["status"] == "cancelled"
    assert finished["error"] is None
    assert calls["count"] == 1


def test_rate_limit_errors_are_treated_as_transient_and_retried(tmp_path):
    manager, database, await_terminal = _retry_manager(tmp_path)
    calls = {"count": 0}

    def throttled_then_success(download_id, download, target, cancel_event):
        calls["count"] += 1
        if calls["count"] < 2:
            raise RuntimeError("Error while downloading: 429 Too Many Requests")

    manager._download_snapshot = throttled_then_success  # type: ignore[method-assign]

    job = manager.queue("acme/tiny", "main", "full")
    finished = await_terminal(job["id"])

    assert finished["status"] == "complete"
    assert calls["count"] == 2


def test_rate_limit_with_retry_exhaustion_fails_with_attempts_recorded(tmp_path):
    manager, database, await_terminal = _retry_manager(tmp_path)

    def always_throttled(download_id, download, target, cancel_event):
        raise RuntimeError("rate limit exceeded, retry later")

    manager._download_snapshot = always_throttled  # type: ignore[method-assign]

    job = manager.queue("acme/tiny", "main", "full")
    finished = await_terminal(job["id"])

    assert finished["status"] == "failed"
    assert finished["error"].startswith(
        f"failed after {manager.settings.download_max_attempts} attempts"
    )


def test_plan_records_upstream_blob_ids_for_tracking(tmp_path):
    from test_planning import _retry_manager

    manager, database, await_terminal = _retry_manager(tmp_path)

    def snapshot(download_id, download, target, cancel_event):
        (target / "model.safetensors").write_bytes(b"weights")

    manager._download_snapshot = snapshot  # type: ignore[method-assign]

    job = manager.queue("acme/tiny", "main", "safetensors")
    finished = await_terminal(job["id"])

    assert finished["status"] == "complete"
    manifest = json.loads(
        (manager.settings.model_storage / "acme" / "tiny" / MANIFEST_FILENAME).read_text(
            encoding="utf-8"
        )
    )
    tracked = {item["path"]: item.get("blob") for item in manifest["selection"]["files"]}
    assert tracked["model.safetensors"] == "blob-110"

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .brand import BRAND
from .library_db import migrate_library_db
from .library_files import LIBRARY_DB_FILENAME

REPO_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")

def _positive_int(name: str, default: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return max(1, min(value, maximum))

def _boolean(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}

@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", BRAND.display_name)
    app_version: str = os.getenv("APP_VERSION", "2.0.1")
    model_storage: Path = Path(os.getenv("MODEL_STORAGE", "/models")).expanduser().resolve()
    model_storage_backend: str = os.getenv("MODEL_STORAGE_BACKEND", "filesystem").strip().lower()
    data_dir: Path = Path(os.getenv("DATA_DIR", "/data")).expanduser().resolve()
    database_url: str | None = field(
        default=(os.getenv("DATABASE_URL") or "").strip() or None,
        repr=False,
    )
    hf_endpoint: str = os.getenv("HF_ENDPOINT", "https://huggingface.co").rstrip("/")
    hf_token: str | None = os.getenv("HF_TOKEN") or None
    max_concurrent_downloads: int = _positive_int("MAX_CONCURRENT_DOWNLOADS", 2, 8)
    download_workers_per_job: int = _positive_int("DOWNLOAD_WORKERS_PER_JOB", 4, 16)
    download_max_attempts: int = _positive_int("DOWNLOAD_MAX_ATTEMPTS", 3, 10)
    download_retry_seconds: int = _positive_int("DOWNLOAD_RETRY_SECONDS", 2, 30)
    # Opt-in for the hf_xet client on files Hugging Face serves from the
    # Xet CAS. Off by default: the client buffers downloads in memory, so
    # the default transport is plain HTTP streaming, whose working set is
    # bounded regardless of file size. ENABLE_XET=1 admits the faster
    # transport for those who have the RAM; see docs/downloads.md.
    enable_xet: bool = _boolean("ENABLE_XET", False)
    accounts_enabled: bool = _boolean("ACCOUNTS_ENABLED", True)
    secure_cookies: bool = _boolean("SECURE_COOKIES", True)
    allow_insecure_cookies: bool = _boolean("ALLOW_INSECURE_COOKIES", False)
    allow_accountless: bool = _boolean("ALLOW_ACCOUNTLESS", False)
    cookies_secure: bool = True  # recomputed per-instance in __post_init__
    # Content-Security-Policy applied to every response. The card pages
    # legitimately embed remote https images and KaTeX needs inline
    # styles; everything else stays same-origin. Empty disables the
    # header entirely (not recommended).
    content_security_policy: str = os.getenv(
        "CONTENT_SECURITY_POLICY",
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' https: data:; font-src 'self' data:; connect-src 'self'; "
        "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'",
    ).strip()
    # Companion to SameSite and the double-submit token: refuse
    # state-changing requests that carry our session cookie with a
    # foreign Origin.
    origin_check: bool = _boolean("ORIGIN_CHECK", True)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "cookies_secure",
            self.secure_cookies and not self.allow_insecure_cookies,
        )
    session_ttl_hours: int = _positive_int("SESSION_TTL_HOURS", 720, 8760)
    upload_chunk_mb: int = _positive_int("UPLOAD_CHUNK_MB", 8, 64)
    max_upload_size_gb: int = _positive_int("MAX_UPLOAD_SIZE_GB", 1024, 16384)
    runtime_targets_json: str = os.getenv("RUNTIME_TARGETS_JSON", "[]")
    runtime_workers: int = _positive_int("RUNTIME_WORKERS", 2, 8)
    runtime_api_token: str | None = os.getenv("RUNTIME_API_TOKEN") or None
    locations_json: str = os.getenv("LOCATIONS_JSON", "[]")
    watch_poll_minutes: int = _positive_int("WATCH_POLL_MINUTES", 120, 10080)
    s3_bucket: str | None = os.getenv("S3_BUCKET") or None
    s3_prefix: str = os.getenv("S3_PREFIX", "models").strip().strip("/")
    s3_endpoint_url: str | None = os.getenv("S3_ENDPOINT_URL") or None
    s3_region: str | None = os.getenv("S3_REGION") or None
    s3_access_key_id: str | None = os.getenv("S3_ACCESS_KEY_ID") or None
    s3_secret_access_key: str | None = os.getenv("S3_SECRET_ACCESS_KEY") or None
    s3_session_token: str | None = os.getenv("S3_SESSION_TOKEN") or None
    s3_use_ssl: bool = _boolean("S3_USE_SSL", True)
    s3_verify_ssl: bool = _boolean("S3_VERIFY_SSL", True)
    s3_addressing_style: str = os.getenv("S3_ADDRESSING_STYLE", "auto").strip().lower()
    s3_storage_class: str | None = os.getenv("S3_STORAGE_CLASS") or None
    s3_max_concurrency: int = _positive_int("S3_MAX_CONCURRENCY", 4, 32)
    s3_multipart_chunk_mb: int = _positive_int("S3_MULTIPART_CHUNK_MB", 64, 512)

    @property
    def database_path(self) -> Path:
        return self.data_dir / LIBRARY_DB_FILENAME

    @property
    def database_target(self) -> Path | str:
        if self.database_url:
            return self.database_url
        return migrate_library_db(self.data_dir)

    @property
    def hub_cache_path(self) -> Path:
        return self.data_dir / "hub-cache"

    def ensure_directories(self) -> None:
        self.model_storage.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.hub_cache_path.mkdir(parents=True, exist_ok=True)

    @property
    def s3_enabled(self) -> bool:
        return self.model_storage_backend == "s3"

settings = Settings()

def validate_repo_id(repo_id: str) -> str:
    value = repo_id.strip()
    if not REPO_ID_PATTERN.fullmatch(value):
        raise ValueError("Repository ID must use the form owner/model-name.")
    return value

def repository_path(repo_id: str, root: Path | None = None) -> Path:
    validated = validate_repo_id(repo_id)
    storage_root = (root or settings.model_storage).resolve()
    target = (storage_root / validated).resolve()
    if storage_root != target and storage_root not in target.parents:
        raise ValueError("Repository path escapes the configured model storage.")
    return target

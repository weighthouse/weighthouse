from __future__ import annotations

import json
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .brand import BRAND

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # SQLite remains usable when the optional adapter is absent.
    psycopg = None
    dict_row = None

if psycopg is None:
    INTEGRITY_ERRORS = (sqlite3.IntegrityError,)
else:
    INTEGRITY_ERRORS = (sqlite3.IntegrityError, psycopg.IntegrityError)

NAMED_PARAMETER_PATTERN = re.compile(r"(?<!:):([A-Za-z_][A-Za-z0-9_]*)")

def _postgres_query(query: str, parameters: object = ()) -> str:
    if isinstance(parameters, Mapping):
        return NAMED_PARAMETER_PATTERN.sub(r"%(\1)s", query)
    return query.replace("?", "%s")

# User preferences (D15): the whole writable contract. Every key here is
# a per-account setting the interface may change; nothing on this list
# touches deployment configuration, which stays environment-owned. No
# presets are seeded — an account starts with an empty object.
USER_PREFERENCE_DENSITY = ("cards", "rows")
USER_PREFERENCE_LIMITS = {
    "density": 8,
    "families": 5,
    "views": 12_000,
    "groups": 4_000,
    "columns": 4_000,
    "columns_version": 8,
}
USER_PREFERENCE_JSON_LIMIT = 16_000

def validate_user_preferences(preferences: Any) -> dict[str, Any]:
    """Shape-check one user's preference object, returning the canonical
    stored form. Raises ValueError on anything the interface didn't send
    on purpose — keys outside the contract, wrong-typed values, or an
    object too large to be a setting."""
    if not isinstance(preferences, dict):
        raise ValueError("Preferences must be an object.")
    unknown = sorted(set(preferences) - set(USER_PREFERENCE_LIMITS))
    if unknown:
        raise ValueError(f"Unknown preference key(s): {', '.join(unknown)}.")
    density = preferences.get("density")
    if density is not None and density not in USER_PREFERENCE_DENSITY:
        raise ValueError("density must be one of: " + ", ".join(USER_PREFERENCE_DENSITY) + ".")
    families = preferences.get("families")
    if families is not None and not isinstance(families, bool):
        raise ValueError("families must be a boolean.")
    columns = preferences.get("columns")
    if columns is not None:
        if not isinstance(columns, dict):
            raise ValueError("columns must be an object of surface to column ids.")
        cleaned_columns = {}
        for surface, value in columns.items():
            if not isinstance(surface, str) or not surface.isalnum() or len(surface) > 40:
                raise ValueError("Each columns surface must be an alphanumeric name.")
            if (
                not isinstance(value, list)
                or len(value) > 32
                or not all(isinstance(item, str) and item.isascii() and item for item in value)
            ):
                raise ValueError(f"columns[{surface}] must be a list of column ids.")
            cleaned_columns[surface] = [item[:40] for item in value]
        preferences["columns"] = cleaned_columns
    columns_version = preferences.get("columns_version")
    if columns_version is not None and (
        isinstance(columns_version, bool)
        or not isinstance(columns_version, int)
        or not 0 <= columns_version <= 1_000_000
    ):
        raise ValueError("columns_version must be an integer.")
    for key in ("views", "groups"):
        value = preferences.get(key)
        if value is None:
            continue
        if not isinstance(value, list) or len(json.dumps(value)) > USER_PREFERENCE_LIMITS[key]:
            raise ValueError(f"{key} must be a list within its size limit.")
        if key == "views":
            cleaned = []
            for view in value:
                if not isinstance(view, dict):
                    raise ValueError("Each view must be an object.")
                name = view.get("name")
                query = view.get("query")
                if not isinstance(name, str) or not name.strip():
                    raise ValueError("Each view needs a name.")
                if not isinstance(query, str):
                    raise ValueError("Each view needs a query.")
                entry: dict[str, Any] = {
                    "id": str(view.get("id") or uuid.uuid4().hex)[:64],
                    "name": name.strip()[:80],
                    "query": query[:400],
                }
                cleaned.append(entry)
            preferences[key] = cleaned
        else:
            cleaned_groups = []
            for group in value:
                if not isinstance(group, dict):
                    raise ValueError("Each group must be an object.")
                name = group.get("name")
                members = group.get("repo_ids")
                if not isinstance(name, str) or not name.strip():
                    raise ValueError("Each group needs a name.")
                if not isinstance(members, list) or not all(
                    isinstance(item, str) and item for item in members
                ):
                    raise ValueError("Each group needs a list of repo ids.")
                cleaned_groups.append({
                    "name": name.strip()[:80],
                    "repo_ids": [item[:200] for item in members][:200],
                })
            preferences[key] = cleaned_groups
    if len(json.dumps(preferences)) > USER_PREFERENCE_JSON_LIMIT:
        raise ValueError("Preferences are too large.")
    return preferences


class _PostgresConnection:
    def __init__(self, connection: Any):
        self._connection = connection

    def __enter__(self) -> _PostgresConnection:
        self._connection.__enter__()
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> object:
        return self._connection.__exit__(exc_type, exc_value, traceback)

    def execute(
        self,
        query: str,
        parameters: Mapping[str, Any] | Sequence[Any] = (),
    ) -> Any:
        return self._connection.execute(_postgres_query(query, parameters), parameters)

    def executemany(
        self,
        query: str,
        parameters: Iterable[Mapping[str, Any] | Sequence[Any]],
    ) -> Any:
        parameter_rows = list(parameters)
        cursor = self._connection.cursor()
        cursor.executemany(
            _postgres_query(query, parameter_rows[0] if parameter_rows else ()),
            parameter_rows,
        )
        return cursor

    def executescript(self, script: str) -> None:
        for statement in script.split(";"):
            if statement.strip():
                self._connection.execute(statement)

DOWNLOAD_FIELDS = {
    "status",
    "total_bytes",
    "downloaded_bytes",
    "progress",
    "speed_bps",
    "error",
    "target_path",
    "metadata_json",
    "updated_at",
    "completed_at",
    "priority",
}

RUNTIME_JOB_FIELDS = {
    "status",
    "total_bytes",
    "processed_bytes",
    "progress",
    "message",
    "error",
    "updated_at",
    "completed_at",
}

class Database:
    def __init__(self, target: Path | str):
        value = str(target).strip()
        self.backend = (
            "postgresql"
            if value.startswith(("postgresql://", "postgres://"))
            else "sqlite"
        )
        self.path = Path(target) if self.backend == "sqlite" else None
        self._database_url = value if self.backend == "postgresql" else None
        self._write_lock = threading.RLock()

    def connect(self) -> sqlite3.Connection | _PostgresConnection:
        if self.backend == "postgresql":
            if psycopg is None or dict_row is None:
                raise RuntimeError(
                    "PostgreSQL support requires the 'psycopg[binary]' dependency."
                )
            connection = psycopg.connect(self._database_url, row_factory=dict_row)
            return _PostgresConnection(connection)
        assert self.path is not None
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def initialize(self) -> None:
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._write_lock, self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('admin', 'member')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username_nocase
                    ON users(LOWER(username));

                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    csrf_token TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_sessions_expiry
                    ON sessions(expires_at);

                CREATE TABLE IF NOT EXISTS agent_keys (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    key_hash TEXT NOT NULL UNIQUE,
                    key_hint TEXT NOT NULL DEFAULT '',
                    scopes_json TEXT NOT NULL DEFAULT '[]',
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL,
                    last_used_at TEXT,
                    revoked_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_agent_keys_user
                    ON agent_keys(user_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS agent_key_events (
                    id TEXT PRIMARY KEY,
                    key_id TEXT NOT NULL REFERENCES agent_keys(id) ON DELETE CASCADE,
                    method TEXT NOT NULL,
                    path TEXT NOT NULL,
                    status INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_agent_key_events_key
                    ON agent_key_events(key_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS watched_models (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    repo_id TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    last_seen_sha TEXT,
                    upstream_sha TEXT,
                    changed_at TEXT,
                    checked_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(user_id, repo_id)
                );

                CREATE INDEX IF NOT EXISTS idx_watched_models_user
                    ON watched_models(user_id, created_at DESC);

                CREATE INDEX IF NOT EXISTS idx_watched_models_checked
                    ON watched_models(checked_at);

                CREATE TABLE IF NOT EXISTS model_notes (
                    id TEXT PRIMARY KEY,
                    repo_id TEXT NOT NULL,
                    author_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    body TEXT NOT NULL,
                    pinned INTEGER NOT NULL DEFAULT 0,
                    shared INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_model_notes_repo
                    ON model_notes(repo_id, created_at DESC);

                CREATE INDEX IF NOT EXISTS idx_model_notes_author
                    ON model_notes(author_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS downloads (
                    id TEXT PRIMARY KEY,
                    repo_id TEXT NOT NULL,
                    revision TEXT NOT NULL,
                    status TEXT NOT NULL,
                    total_bytes INTEGER NOT NULL DEFAULT 0,
                    downloaded_bytes INTEGER NOT NULL DEFAULT 0,
                    progress REAL NOT NULL DEFAULT 0,
                    speed_bps REAL NOT NULL DEFAULT 0,
                    error TEXT,
                    target_path TEXT,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
                    priority INTEGER NOT NULL DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS idx_downloads_status
                    ON downloads(status, updated_at DESC);

                CREATE TABLE IF NOT EXISTS local_models (
                    repo_id TEXT PRIMARY KEY,
                    relative_path TEXT NOT NULL UNIQUE,
                    size_bytes INTEGER NOT NULL DEFAULT 0,
                    file_count INTEGER NOT NULL DEFAULT 0,
                    modified_at TEXT NOT NULL,
                    downloaded_at TEXT,
                    revision TEXT,
                    sha TEXT,
                    pipeline_tag TEXT,
                    library_name TEXT,
                    license TEXT,
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    config_json TEXT NOT NULL DEFAULT '{}',
                    source_url TEXT,
                    managed INTEGER NOT NULL DEFAULT 0,
                    storage_backend TEXT NOT NULL DEFAULT 'filesystem',
                    cached INTEGER NOT NULL DEFAULT 1,
                    remote_uri TEXT,
                    location TEXT NOT NULL DEFAULT 'primary',
                    parameter_count INTEGER
                );

                CREATE INDEX IF NOT EXISTS idx_local_models_modified
                    ON local_models(modified_at DESC);

                CREATE TABLE IF NOT EXISTS runtime_jobs (
                    id TEXT PRIMARY KEY,
                    target_id TEXT NOT NULL,
                    target_name TEXT NOT NULL,
                    target_kind TEXT NOT NULL CHECK (target_kind IN ('ollama', 'vllm')),
                    repo_id TEXT NOT NULL,
                    runtime_model_name TEXT NOT NULL,
                    source_file TEXT,
                    status TEXT NOT NULL,
                    total_bytes INTEGER NOT NULL DEFAULT 0,
                    processed_bytes INTEGER NOT NULL DEFAULT 0,
                    progress REAL NOT NULL DEFAULT 0,
                    message TEXT NOT NULL DEFAULT '',
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    user_id TEXT REFERENCES users(id) ON DELETE SET NULL
                );

                CREATE INDEX IF NOT EXISTS idx_runtime_jobs_created
                    ON runtime_jobs(created_at DESC);

                CREATE UNIQUE INDEX IF NOT EXISTS idx_runtime_jobs_active_model
                    ON runtime_jobs(target_id, repo_id)
                    WHERE status IN ('queued', 'preparing', 'transferring', 'loading');

                CREATE UNIQUE INDEX IF NOT EXISTS idx_runtime_jobs_active_vllm
                    ON runtime_jobs(target_id)
                    WHERE target_kind = 'vllm'
                      AND status IN ('queued', 'preparing', 'transferring', 'loading');

                CREATE TABLE IF NOT EXISTS collections (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(user_id, name)
                );

                CREATE TABLE IF NOT EXISTS saved_models (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    repo_id TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(user_id, repo_id)
                );

                CREATE INDEX IF NOT EXISTS idx_saved_models_user_updated
                    ON saved_models(user_id, updated_at DESC);

                CREATE TABLE IF NOT EXISTS collection_items (
                    collection_id TEXT NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
                    saved_model_id TEXT NOT NULL REFERENCES saved_models(id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(collection_id, saved_model_id)
                );

                CREATE TABLE IF NOT EXISTS owned_repositories (
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
                    repo_id TEXT NOT NULL UNIQUE,
                    description TEXT NOT NULL DEFAULT '',
                    visibility TEXT NOT NULL DEFAULT 'private'
                        CHECK (visibility IN ('private', 'shared')),
                    status TEXT NOT NULL DEFAULT 'uploading'
                        CHECK (status IN ('uploading', 'ready')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_owned_repositories_owner
                    ON owned_repositories(owner_id, updated_at DESC);

                CREATE TABLE IF NOT EXISTS user_preferences (
                    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                    preferences_json TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS model_card_snapshots (
                    repo_id TEXT NOT NULL,
                    sha TEXT NOT NULL,
                    card_text TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    PRIMARY KEY (repo_id, sha)
                );
                """
            )
            columns = self._column_names(connection, "downloads")
            if "user_id" not in columns:
                connection.execute(
                    "ALTER TABLE downloads ADD COLUMN user_id TEXT "
                    "REFERENCES users(id) ON DELETE SET NULL"
                )
            if "priority" not in columns:
                # Queue position: higher goes first, ties keep
                # first-come order. "Move to front" sets it.
                connection.execute(
                    "ALTER TABLE downloads ADD COLUMN priority INTEGER "
                    "NOT NULL DEFAULT 0"
                )
            local_model_columns = self._column_names(connection, "local_models")
            if "storage_backend" not in local_model_columns:
                connection.execute(
                    "ALTER TABLE local_models ADD COLUMN storage_backend "
                    "TEXT NOT NULL DEFAULT 'filesystem'"
                )
            if "cached" not in local_model_columns:
                connection.execute(
                    "ALTER TABLE local_models ADD COLUMN cached INTEGER NOT NULL DEFAULT 1"
                )
            if "remote_uri" not in local_model_columns:
                connection.execute("ALTER TABLE local_models ADD COLUMN remote_uri TEXT")
            if "location" not in local_model_columns:
                connection.execute(
                    "ALTER TABLE local_models ADD COLUMN location "
                    "TEXT NOT NULL DEFAULT 'primary'"
                )
            if "parameter_count" not in local_model_columns:
                # The GGUF-declared parameter count (general.size); NULL
                # for formats that do not declare one.
                connection.execute(
                    "ALTER TABLE local_models ADD COLUMN parameter_count INTEGER"
                )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_downloads_user_created "
                "ON downloads(user_id, created_at DESC)"
            )
            connection.execute(
                "DELETE FROM sessions WHERE expires_at <= ?",
                (datetime.now(timezone.utc).isoformat(),),
            )

    def _column_names(
        self, connection: sqlite3.Connection | _PostgresConnection, table: str
    ) -> set[str]:
        if self.backend == "sqlite":
            rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
        else:
            rows = connection.execute(
                """
                SELECT column_name AS name
                FROM information_schema.columns
                WHERE table_schema = current_schema() AND table_name = ?
                """,
                (table,),
            ).fetchall()
        return {row["name"] for row in rows}

    @staticmethod
    def _decode_row(
        row: sqlite3.Row | dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        for key in ("payload_json", "metadata_json", "tags_json", "config_json", "scopes_json"):
            if key in result:
                raw = result.pop(key)
                output_key = key.removesuffix("_json")
                list_valued = key in ("tags_json", "scopes_json")
                try:
                    result[output_key] = json.loads(raw or ("[]" if list_valued else "{}"))
                except (TypeError, json.JSONDecodeError):
                    result[output_key] = [] if list_valued else {}
        if "managed" in result:
            result["managed"] = bool(result["managed"])
        if "cached" in result:
            result["cached"] = bool(result["cached"])
        if "pinned" in result:
            result["pinned"] = bool(result["pinned"])
        if "shared" in result:
            result["shared"] = bool(result["shared"])
        return result

    @staticmethod
    def _public_user(row: sqlite3.Row | dict[str, Any] | None) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        result.pop("password_hash", None)
        return result

    def count_users(self) -> int:
        with self.connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM users").fetchone()
            return int(row["count"])

    def create_user(self, record: dict[str, Any]) -> dict[str, Any]:
        with self._write_lock, self.connect() as connection:
            connection.execute(
                """
                INSERT INTO users (
                    id, username, display_name, password_hash, role, created_at, updated_at
                ) VALUES (
                    :id, :username, :display_name, :password_hash, :role, :created_at, :updated_at
                )
                """,
                record,
            )
        return self.get_user(record["id"], include_secret=False)

    def get_user(self, user_id: str, include_secret: bool = True) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row and include_secret else self._public_user(row)

    def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE LOWER(username) = LOWER(?)", (username,)
            ).fetchone()
        return dict(row) if row else None

    def list_users(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM users ORDER BY role, username"
            ).fetchall()
        return [self._public_user(row) for row in rows]

    def update_user_password(
        self, user_id: str, password_hash: str, updated_at: str
    ) -> None:
        with self._write_lock, self.connect() as connection:
            connection.execute(
                """
                UPDATE users SET password_hash = ?, updated_at = ?
                WHERE id = ?
                """,
                (password_hash, updated_at, user_id),
            )

    def create_session(self, record: dict[str, Any]) -> None:
        with self._write_lock, self.connect() as connection:
            connection.execute(
                """
                INSERT INTO sessions (
                    token_hash, user_id, csrf_token, created_at, expires_at
                ) VALUES (
                    :token_hash, :user_id, :csrf_token, :created_at, :expires_at
                )
                """,
                record,
            )

    def get_session(self, token_hash: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT sessions.*, users.id AS user_record_id, users.username,
                       users.display_name, users.role, users.created_at AS user_created_at
                FROM sessions
                JOIN users ON users.id = sessions.user_id
                WHERE sessions.token_hash = ?
                """,
                (token_hash,),
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["user"] = {
            "id": result.pop("user_record_id"),
            "username": result.pop("username"),
            "display_name": result.pop("display_name"),
            "role": result.pop("role"),
            "created_at": result.pop("user_created_at"),
        }
        return result

    def delete_session(self, token_hash: str) -> None:
        with self._write_lock, self.connect() as connection:
            connection.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))

    def delete_other_sessions(self, user_id: str, keep_token_hash: str) -> None:
        with self._write_lock, self.connect() as connection:
            connection.execute(
                "DELETE FROM sessions WHERE user_id = ? AND token_hash != ?",
                (user_id, keep_token_hash),
            )

    def create_agent_key(self, record: dict[str, Any]) -> None:
        with self._write_lock, self.connect() as connection:
            connection.execute(
                """
                INSERT INTO agent_keys (
                    id, name, key_hash, key_hint, scopes_json, user_id,
                    created_at, last_used_at, revoked_at
                ) VALUES (
                    :id, :name, :key_hash, :key_hint, :scopes_json, :user_id,
                    :created_at, :last_used_at, :revoked_at
                )
                """,
                record,
            )

    def get_agent_key(self, key_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM agent_keys WHERE id = ?",
                (key_id,),
            ).fetchone()
        return self._decode_row(row)

    def get_agent_key_by_hash(self, key_hash: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM agent_keys WHERE key_hash = ?",
                (key_hash,),
            ).fetchone()
        return self._decode_row(row)

    def list_agent_keys(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT agent_keys.*, users.id AS owner_record_id, users.username,
                       users.display_name AS owner_display_name, users.role AS owner_role
                FROM agent_keys
                JOIN users ON users.id = agent_keys.user_id
                ORDER BY agent_keys.created_at DESC
                """
            ).fetchall()
        keys = [self._decode_row(row) for row in rows]
        for key in keys:
            key["user"] = {
                "id": key.pop("owner_record_id"),
                "username": key.pop("username"),
                "display_name": key.pop("owner_display_name"),
                "role": key.pop("owner_role"),
            }
        return keys

    def count_active_agent_keys(self) -> int:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS total FROM agent_keys WHERE revoked_at IS NULL"
            ).fetchone()
        return int(row["total"]) if row else 0

    def revoke_agent_key(self, key_id: str, revoked_at: str) -> bool:
        with self._write_lock, self.connect() as connection:
            cursor = connection.execute(
                "UPDATE agent_keys SET revoked_at = :revoked_at "
                "WHERE id = :id AND revoked_at IS NULL",
                {"id": key_id, "revoked_at": revoked_at},
            )
            return cursor.rowcount > 0

    def purge_agent_key(self, key_id: str) -> bool:
        """Remove the key outright; its audit-trail rows cascade with it.
        A revoke is a stamp the key keeps — this is the end of it, and
        the caller may no longer pass any gate."""
        with self._write_lock, self.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM agent_keys WHERE id = ?",
                (key_id,),
            )
            return cursor.rowcount > 0

    def touch_agent_key(self, key_id: str, used_at: str) -> None:
        with self._write_lock, self.connect() as connection:
            connection.execute(
                "UPDATE agent_keys SET last_used_at = :used_at WHERE id = :id",
                {"id": key_id, "used_at": used_at},
            )

    def record_agent_key_event(self, record: dict[str, Any]) -> None:
        with self._write_lock, self.connect() as connection:
            connection.execute(
                """
                INSERT INTO agent_key_events (
                    id, key_id, method, path, status, created_at
                ) VALUES (
                    :id, :key_id, :method, :path, :status, :created_at
                )
                """,
                record,
            )
            connection.execute(
                """
                DELETE FROM agent_key_events
                WHERE key_id = :key_id AND id NOT IN (
                    SELECT id FROM agent_key_events WHERE key_id = :key_id
                    ORDER BY created_at DESC, id DESC LIMIT :limit
                )
                """,
                {"key_id": record["key_id"], "limit": 200},
            )

    def list_agent_key_events(
        self, key_id: str, limit: int = 150
    ) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, key_id, method, path, status, created_at
                FROM agent_key_events
                WHERE key_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (key_id, max(1, min(limit, 500))),
            ).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # Model notes (D14): per-model threads, author-private by default.
    # ------------------------------------------------------------------

    def create_model_note(self, record: dict[str, Any]) -> dict[str, Any]:
        with self._write_lock, self.connect() as connection:
            connection.execute(
                """
                INSERT INTO model_notes (
                    id, repo_id, author_id, body, pinned, shared,
                    created_at, updated_at
                ) VALUES (
                    :id, :repo_id, :author_id, :body, :pinned, :shared,
                    :created_at, :updated_at
                )
                """,
                record,
            )
        return self.get_model_note(record["id"])  # type: ignore[return-value]

    def get_model_note(self, note_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM model_notes WHERE id = ?", (note_id,)
            ).fetchone()
        return self._decode_row(row) if row else None

    def list_model_notes(
        self, repo_id: str, viewer_id: str, is_admin: bool = False
    ) -> list[dict[str, Any]]:
        """Visible entries for one thread: the viewer's own entries always,
        shared entries from every author, everything for admins.
        Author attribution rides each row."""
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT n.*, u.display_name AS author_name,
                       u.username AS author_username
                FROM model_notes n
                JOIN users u ON u.id = n.author_id
                WHERE n.repo_id = ?
                  AND (n.shared = 1 OR n.author_id = ? OR ?)
                ORDER BY n.pinned DESC, n.created_at DESC, n.id DESC
                """,
                (repo_id, viewer_id, is_admin),
            ).fetchall()
        return [self._decode_row(row) for row in rows]

    def update_model_note(
        self, note_id: str, *, body: str | None = None, updated_at: str
    ) -> dict[str, Any] | None:
        with self._write_lock, self.connect() as connection:
            if body is not None:
                connection.execute(
                    """
                    UPDATE model_notes
                    SET body = :body, updated_at = :updated_at
                    WHERE id = :id
                    """,
                    {"id": note_id, "body": body, "updated_at": updated_at},
                )
        return self.get_model_note(note_id)

    def set_model_note_pinned(
        self, note_id: str, pinned: bool, updated_at: str
    ) -> dict[str, Any] | None:
        """Pin is exclusive per author within a thread (D14): pinning one
        entry releases that author's previous pin and nobody else's. Each
        viewer's cell then shows pinned-else-latest among notes visible
        to them."""
        with self._write_lock, self.connect() as connection:
            row = connection.execute(
                "SELECT repo_id, author_id FROM model_notes WHERE id = ?", (note_id,)
            ).fetchone()
            if not row:
                return None
            if pinned:
                connection.execute(
                    """
                    UPDATE model_notes SET pinned = 0, updated_at = :updated_at
                    WHERE repo_id = :repo_id AND author_id = :author_id AND pinned = 1
                    """,
                    {
                        "repo_id": row["repo_id"],
                        "author_id": row["author_id"],
                        "updated_at": updated_at,
                    },
                )
            connection.execute(
                """
                UPDATE model_notes
                SET pinned = :pinned, updated_at = :updated_at
                WHERE id = :id
                """,
                {"id": note_id, "pinned": 1 if pinned else 0, "updated_at": updated_at},
            )
        return self.get_model_note(note_id)

    def set_model_note_shared(
        self, note_id: str, shared: bool, updated_at: str
    ) -> dict[str, Any] | None:
        """Share is a thread-level act (D14): flips every entry in the
        thread authored by the same author, never other people's entries."""
        with self._write_lock, self.connect() as connection:
            row = connection.execute(
                "SELECT repo_id, author_id FROM model_notes WHERE id = ?", (note_id,)
            ).fetchone()
            if not row:
                return None
            connection.execute(
                """
                UPDATE model_notes
                SET shared = :shared, updated_at = :updated_at
                WHERE repo_id = :repo_id AND author_id = :author_id
                """,
                {
                    "repo_id": row["repo_id"],
                    "author_id": row["author_id"],
                    "shared": 1 if shared else 0,
                    "updated_at": updated_at,
                },
            )
        return self.get_model_note(note_id)

    def delete_model_note(self, note_id: str) -> None:
        with self._write_lock, self.connect() as connection:
            connection.execute("DELETE FROM model_notes WHERE id = ?", (note_id,))

    def note_search_hits(
        self, viewer_id: str, query: str, is_admin: bool = False, limit: int = 50
    ) -> list[dict[str, Any]]:
        """repo_ids + matched note snippets visible to the viewer (D14:
        search hits note bodies; privacy respected per entry)."""
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT n.repo_id, n.body, n.pinned, n.created_at,
                       u.display_name AS author_name
                FROM model_notes n
                JOIN users u ON u.id = n.author_id
                WHERE (n.shared = 1 OR n.author_id = ? OR ?)
                  AND LOWER(n.body) LIKE LOWER(?)
                ORDER BY n.pinned DESC, n.created_at DESC
                LIMIT ?
                """,
                (viewer_id, is_admin, f"%{query}%", max(1, min(limit, 50))),
            ).fetchall()
        return [self._decode_row(row) for row in rows]

    def note_previews(self, viewer_id: str, is_admin: bool = False) -> dict[str, dict[str, Any]]:
        """Cell preview per copy (D14): the pinned-else-latest entry among
        notes visible to this viewer, keyed by repo_id. Absent key = no
        visible notes for that copy."""
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT repo_id, author_id, body, pinned, created_at, author_name
                FROM (
                    SELECT n.repo_id, n.author_id, n.body, n.pinned,
                           n.created_at, u.display_name AS author_name,
                           ROW_NUMBER() OVER (
                               PARTITION BY n.repo_id
                               ORDER BY n.pinned DESC, n.created_at DESC, n.id DESC
                           ) AS rn
                    FROM model_notes n
                    JOIN users u ON u.id = n.author_id
                    WHERE n.shared = 1 OR n.author_id = ? OR ?
                )
                WHERE rn = 1
                """,
                (viewer_id, is_admin),
            ).fetchall()
        return {row["repo_id"]: self._decode_row(row) for row in rows}

    # ------------------------------------------------------------------
    # User preferences (D15): per-account settings live in the database,
    # never in deployment config. One row per user; the allowlist below
    # is the whole contract — new keys are added here deliberately.
    # ------------------------------------------------------------------

    def get_user_preferences(self, user_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT preferences_json FROM user_preferences WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        if not row:
            return {}
        try:
            parsed = json.loads(row["preferences_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def set_user_preferences(
        self, user_id: str, preferences: dict[str, Any], updated_at: str
    ) -> dict[str, Any]:
        validated = validate_user_preferences(preferences)
        with self._write_lock, self.connect() as connection:
            connection.execute(
                """
                INSERT INTO user_preferences (user_id, preferences_json, updated_at)
                VALUES (:user_id, :preferences_json, :updated_at)
                ON CONFLICT(user_id) DO UPDATE SET
                    preferences_json = excluded.preferences_json,
                    updated_at = excluded.updated_at
                """,
                {
                    "user_id": user_id,
                    "preferences_json": json.dumps(validated),
                    "updated_at": updated_at,
                },
            )
        return validated

    # Model card snapshots (D17): the stored card is pinned to the copy's
    # on-disk sha and never drifts ahead of the weights. Keyed by
    # (repo_id, sha); refetching happens only in the caller that notices a
    # sha without a snapshot. A rolling trio of recent revisions is kept.

    def get_card_snapshot(self, repo_id: str, sha: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT repo_id, sha, card_text, fetched_at
                FROM model_card_snapshots
                WHERE repo_id = ? AND sha = ?
                """,
                (repo_id, sha),
            ).fetchone()
        return self._decode_row(row)

    def store_card_snapshot(self, record: dict[str, Any]) -> None:
        with self._write_lock, self.connect() as connection:
            connection.execute(
                """
                INSERT INTO model_card_snapshots (repo_id, sha, card_text, fetched_at)
                VALUES (:repo_id, :sha, :card_text, :fetched_at)
                ON CONFLICT(repo_id, sha) DO UPDATE SET
                    card_text = excluded.card_text,
                    fetched_at = excluded.fetched_at
                """,
                record,
            )
            connection.execute(
                """
                DELETE FROM model_card_snapshots
                WHERE repo_id = :repo_id AND sha NOT IN (
                    SELECT sha FROM model_card_snapshots
                    WHERE repo_id = :repo_id
                    ORDER BY fetched_at DESC
                    LIMIT 3
                )
                """,
                {"repo_id": record["repo_id"]},
            )

    def create_watched_model(self, record: dict[str, Any]) -> None:
        with self._write_lock, self.connect() as connection:
            connection.execute(
                """
                INSERT INTO watched_models (
                    id, user_id, repo_id, metadata_json, last_seen_sha,
                    upstream_sha, changed_at, checked_at, created_at, updated_at
                ) VALUES (
                    :id, :user_id, :repo_id, :metadata_json, :last_seen_sha,
                    :upstream_sha, :changed_at, :checked_at, :created_at, :updated_at
                )
                """,
                record,
            )

    def get_watched_model(
        self, user_id: str, repo_id: str
    ) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM watched_models WHERE user_id = ? AND repo_id = ?",
                (user_id, repo_id),
            ).fetchone()
        return self._decode_row(row)

    def delete_watched_model(self, user_id: str, repo_id: str) -> bool:
        with self._write_lock, self.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM watched_models WHERE user_id = ? AND repo_id = ?",
                (user_id, repo_id),
            )
            return cursor.rowcount > 0

    def list_watched_models(self, user_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM watched_models
                WHERE user_id = ?
                ORDER BY CASE WHEN changed_at IS NULL THEN 1 ELSE 0 END,
                         changed_at DESC,
                         created_at DESC
                """,
                (user_id,),
            ).fetchall()
        return [self._decode_row(row) for row in rows]

    def list_watches_for_scan(self) -> list[dict[str, Any]]:
        """All watches, oldest check first — the poller's work order."""
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM watched_models
                ORDER BY checked_at IS NOT NULL, checked_at, created_at
                """
            ).fetchall()
        return [self._decode_row(row) for row in rows]

    def record_watch_check(
        self,
        watch_id: str,
        *,
        checked_at: str,
        upstream_sha: str | None,
        changed_at: str | None,
    ) -> None:
        """Store a poller observation, self-healing the drifted flag.

        ``changed_at`` arrives null when the baseline matches upstream
        again, or the first-detection timestamp when it does not.
        """
        with self._write_lock, self.connect() as connection:
            connection.execute(
                """
                UPDATE watched_models
                SET upstream_sha = :upstream_sha,
                    changed_at = :changed_at,
                    checked_at = :checked_at,
                    updated_at = :checked_at
                WHERE id = :id
                """,
                {
                    "id": watch_id,
                    "upstream_sha": upstream_sha,
                    "changed_at": changed_at,
                    "checked_at": checked_at,
                },
            )

    def record_watch_failure(self, watch_id: str, checked_at: str) -> None:
        """Honest grace: an unreachable upstream advances the check slot
        (so the poller does not hammer a failing endpoint) while leaving
        every recorded observation untouched."""
        with self._write_lock, self.connect() as connection:
            connection.execute(
                """
                UPDATE watched_models
                SET checked_at = :checked_at, updated_at = :checked_at
                WHERE id = :id
                """,
                {"id": watch_id, "checked_at": checked_at},
            )

    def mark_watch_seen(
        self, user_id: str, repo_id: str, seen_sha: str, seen_at: str
    ) -> dict[str, Any] | None:
        """Acknowledge upstream movement on a copy-less watch."""
        with self._write_lock, self.connect() as connection:
            connection.execute(
                """
                UPDATE watched_models
                SET last_seen_sha = :seen_sha, upstream_sha = :seen_sha,
                    changed_at = NULL, updated_at = :seen_at
                WHERE user_id = :user_id AND repo_id = :repo_id
                """,
                {
                    "user_id": user_id,
                    "repo_id": repo_id,
                    "seen_sha": seen_sha,
                    "seen_at": seen_at,
                },
            )
        return self.get_watched_model(user_id, repo_id)

    def create_download(self, record: dict[str, Any]) -> dict[str, Any]:
        params = {**record, "user_id": record.get("user_id")}
        with self._write_lock, self.connect() as connection:
            connection.execute(
                """
                INSERT INTO downloads (
                    id, repo_id, revision, status, total_bytes, downloaded_bytes,
                    progress, speed_bps, error, target_path, payload_json,
                    metadata_json, created_at, updated_at, completed_at, user_id
                ) VALUES (
                    :id, :repo_id, :revision, :status, :total_bytes, :downloaded_bytes,
                    :progress, :speed_bps, :error, :target_path, :payload_json,
                    :metadata_json, :created_at, :updated_at, :completed_at, :user_id
                )
                """,
                params,
            )
        return self.get_download(record["id"])

    def update_download(self, download_id: str, **changes: Any) -> dict[str, Any] | None:
        safe_changes = {key: value for key, value in changes.items() if key in DOWNLOAD_FIELDS}
        if not safe_changes:
            return self.get_download(download_id)
        assignments = ", ".join(f"{key} = :{key}" for key in safe_changes)
        safe_changes["id"] = download_id
        with self._write_lock, self.connect() as connection:
            connection.execute(f"UPDATE downloads SET {assignments} WHERE id = :id", safe_changes)
        return self.get_download(download_id)

    def get_download(self, download_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM downloads WHERE id = ?", (download_id,)).fetchone()
        return self._decode_row(row)

    def list_downloads(
        self, limit: int = 100, user_id: str | None = None, include_unowned: bool = False
    ) -> list[dict[str, Any]]:
        with self.connect() as connection:
            if user_id is None:
                rows = connection.execute(
                    "SELECT * FROM downloads ORDER BY created_at DESC LIMIT ?", (limit,)
                ).fetchall()
            elif include_unowned:
                rows = connection.execute(
                    """
                    SELECT * FROM downloads
                    WHERE user_id = ? OR user_id IS NULL
                    ORDER BY created_at DESC LIMIT ?
                    """,
                    (user_id, limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM downloads
                    WHERE user_id = ?
                    ORDER BY created_at DESC LIMIT ?
                    """,
                    (user_id, limit),
                ).fetchall()
        return [self._decode_row(row) for row in rows]

    def find_active_download(self, repo_id: str) -> dict[str, Any] | None:
        # A paused job still owns its target directory: moves, deletes,
        # and new queues for the same repository wait on it (or resume it).
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM downloads
                WHERE repo_id = ?
                    AND status IN ('queued', 'preparing', 'downloading', 'paused')
                ORDER BY created_at DESC LIMIT 1
                """,
                (repo_id,),
            ).fetchone()
        return self._decode_row(row)

    def unfinished_downloads(self) -> list[dict[str, Any]]:
        # Paused jobs are deliberately excluded: a pause survives a server
        # restart instead of silently re-starting the transfer.
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM downloads WHERE status IN ('queued', 'preparing', 'downloading')"
            ).fetchall()
        return [self._decode_row(row) for row in rows]

    def queued_downloads(self) -> list[dict[str, Any]]:
        """The queue in dispatch order: moved-to-front first, then FIFO."""
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM downloads
                WHERE status = 'queued'
                ORDER BY priority DESC, created_at ASC
                """
            ).fetchall()
        return [self._decode_row(row) for row in rows]

    def paused_downloads(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM downloads WHERE status = 'paused'"
            ).fetchall()
        return [self._decode_row(row) for row in rows]

    def delete_download(self, download_id: str) -> bool:
        """Remove the history record. Files on disk are untouched; the
        Delete panel manages those."""
        with self._write_lock, self.connect() as connection:
            result = connection.execute(
                "DELETE FROM downloads WHERE id = ?", (download_id,)
            )
            deleted = getattr(result, "rowcount", None)
            if deleted is None:
                return self.get_download(download_id) is None
            return deleted > 0

    def bump_download_priority(self, download_id: str) -> dict[str, Any] | None:
        """One above the highest queued priority, so the job leads the
        queue (and starts immediately if a transfer slot is free)."""
        with self._write_lock, self.connect() as connection:
            connection.execute(
                """
                UPDATE downloads
                SET priority = (
                        SELECT COALESCE(MAX(priority), 0) + 1
                        FROM downloads WHERE status = 'queued'
                    )
                WHERE id = ? AND status = 'queued'
                """,
                (download_id,),
            )
        return self.get_download(download_id)

    def create_runtime_job(self, record: dict[str, Any]) -> dict[str, Any]:
        with self._write_lock, self.connect() as connection:
            connection.execute(
                """
                INSERT INTO runtime_jobs (
                    id, target_id, target_name, target_kind, repo_id,
                    runtime_model_name, source_file, status, total_bytes,
                    processed_bytes, progress, message, error, created_at,
                    updated_at, completed_at, user_id
                ) VALUES (
                    :id, :target_id, :target_name, :target_kind, :repo_id,
                    :runtime_model_name, :source_file, :status, :total_bytes,
                    :processed_bytes, :progress, :message, :error, :created_at,
                    :updated_at, :completed_at, :user_id
                )
                """,
                record,
            )
        return self.get_runtime_job(record["id"])

    def update_runtime_job(
        self, job_id: str, **changes: Any
    ) -> dict[str, Any] | None:
        safe_changes = {
            key: value for key, value in changes.items() if key in RUNTIME_JOB_FIELDS
        }
        if not safe_changes:
            return self.get_runtime_job(job_id)
        assignments = ", ".join(f"{key} = :{key}" for key in safe_changes)
        safe_changes["id"] = job_id
        with self._write_lock, self.connect() as connection:
            connection.execute(
                f"UPDATE runtime_jobs SET {assignments} WHERE id = :id",
                safe_changes,
            )
        return self.get_runtime_job(job_id)

    def get_runtime_job(self, job_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM runtime_jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return self._decode_row(row)

    def list_runtime_jobs(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM runtime_jobs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._decode_row(row) for row in rows]

    def find_active_runtime_job(
        self, target_id: str, repo_id: str
    ) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM runtime_jobs
                WHERE target_id = ? AND repo_id = ?
                  AND status IN ('queued', 'preparing', 'transferring', 'loading')
                ORDER BY created_at DESC LIMIT 1
                """,
                (target_id, repo_id),
            ).fetchone()
        return self._decode_row(row)

    def find_active_runtime_target(
        self, target_id: str
    ) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM runtime_jobs
                WHERE target_id = ?
                  AND status IN ('queued', 'preparing', 'transferring', 'loading')
                ORDER BY created_at DESC LIMIT 1
                """,
                (target_id,),
            ).fetchone()
        return self._decode_row(row)

    def fail_unfinished_runtime_jobs(self, updated_at: str) -> None:
        message = f"{BRAND.display_name} restarted before this runtime job completed."
        with self._write_lock, self.connect() as connection:
            connection.execute(
                """
                UPDATE runtime_jobs
                SET status = 'failed',
                    error = ?,
                    message = 'Interrupted',
                    updated_at = ?,
                    completed_at = ?
                WHERE status IN ('queued', 'preparing', 'transferring', 'loading')
                """,
                (message, updated_at, updated_at),
            )

    def upsert_local_model(self, record: dict[str, Any]) -> None:
        record = {**record, "location": record.get("location") or "primary"}
        record["parameter_count"] = record.get("parameter_count")
        with self._write_lock, self.connect() as connection:
            connection.execute(
                """
                INSERT INTO local_models (
                    repo_id, relative_path, size_bytes, file_count, modified_at,
                    downloaded_at, revision, sha, pipeline_tag, library_name,
                    license, tags_json, config_json, source_url, managed,
                    storage_backend, cached, remote_uri, location, parameter_count
                ) VALUES (
                    :repo_id, :relative_path, :size_bytes, :file_count, :modified_at,
                    :downloaded_at, :revision, :sha, :pipeline_tag, :library_name,
                    :license, :tags_json, :config_json, :source_url, :managed,
                    :storage_backend, :cached, :remote_uri, :location, :parameter_count
                )
                ON CONFLICT(repo_id) DO UPDATE SET
                    relative_path = excluded.relative_path,
                    size_bytes = excluded.size_bytes,
                    file_count = excluded.file_count,
                    modified_at = excluded.modified_at,
                    downloaded_at = excluded.downloaded_at,
                    revision = excluded.revision,
                    sha = excluded.sha,
                    pipeline_tag = excluded.pipeline_tag,
                    library_name = excluded.library_name,
                    license = excluded.license,
                    tags_json = excluded.tags_json,
                    config_json = excluded.config_json,
                    source_url = excluded.source_url,
                    managed = excluded.managed,
                    storage_backend = excluded.storage_backend,
                    cached = excluded.cached,
                    remote_uri = excluded.remote_uri,
                    location = excluded.location,
                    parameter_count = excluded.parameter_count
                """,
                record,
            )

    def list_local_models(self, query: str = "") -> list[dict[str, Any]]:
        with self.connect() as connection:
            if query:
                rows = connection.execute(
                    """
                    SELECT * FROM local_models
                    WHERE LOWER(repo_id) LIKE LOWER(?)
                       OR LOWER(pipeline_tag) LIKE LOWER(?)
                       OR LOWER(library_name) LIKE LOWER(?)
                    ORDER BY modified_at DESC
                    """,
                    (f"%{query}%", f"%{query}%", f"%{query}%"),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM local_models ORDER BY modified_at DESC"
                ).fetchall()
        return [self._decode_row(row) for row in rows]

    def get_local_model(self, repo_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM local_models WHERE repo_id = ?", (repo_id,)
            ).fetchone()
        return self._decode_row(row)

    def prune_local_models(self, relative_paths: set[str]) -> None:
        with self._write_lock, self.connect() as connection:
            rows = connection.execute(
                "SELECT relative_path FROM local_models WHERE storage_backend = 'filesystem'"
            ).fetchall()
            stale = [row["relative_path"] for row in rows if row["relative_path"] not in relative_paths]
            connection.executemany(
                "DELETE FROM local_models WHERE relative_path = ?",
                ((path,) for path in stale),
            )

    def delete_local_model(self, repo_id: str) -> bool:
        """Forget a local model record and its per-user visibility rows."""
        with self._write_lock, self.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM local_models WHERE repo_id = ?",
                (repo_id,),
            )
            removed = cursor.rowcount > 0
        return removed

    def set_local_model_cached(self, repo_id: str, cached: bool) -> dict[str, Any] | None:
        with self._write_lock, self.connect() as connection:
            connection.execute(
                "UPDATE local_models SET cached = ? WHERE repo_id = ?",
                (int(cached), repo_id),
            )
        return self.get_local_model(repo_id)

    def list_visible_local_models(
        self, user_id: str, query: str = ""
    ) -> list[dict[str, Any]]:
        parameters: list[Any] = [user_id]
        query_clause = ""
        if query:
            query_clause = (
                " AND (LOWER(local_models.repo_id) LIKE LOWER(?) "
                "OR LOWER(local_models.pipeline_tag) LIKE LOWER(?) "
                "OR LOWER(local_models.library_name) LIKE LOWER(?))"
            )
            parameters.extend((f"%{query}%", f"%{query}%", f"%{query}%"))
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT local_models.*
                FROM local_models
                LEFT JOIN owned_repositories
                    ON owned_repositories.repo_id = local_models.repo_id
                WHERE (
                    owned_repositories.id IS NULL
                    OR owned_repositories.owner_id = ?
                    OR owned_repositories.visibility = 'shared'
                )
                """
                + query_clause
                + " ORDER BY local_models.modified_at DESC",
                parameters,
            ).fetchall()
        return [self._decode_row(row) for row in rows]

    def get_visible_local_model(
        self, user_id: str, repo_id: str
    ) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT local_models.*
                FROM local_models
                LEFT JOIN owned_repositories
                    ON owned_repositories.repo_id = local_models.repo_id
                WHERE local_models.repo_id = ?
                  AND (
                    owned_repositories.id IS NULL
                    OR owned_repositories.owner_id = ?
                    OR owned_repositories.visibility = 'shared'
                  )
                """,
                (repo_id, user_id),
            ).fetchone()
        return self._decode_row(row)

    # Library query system (D16): the facet rail is computed from the
    # actual library — counts over what this viewer can see, never
    # hardcoded buckets. Favorited and watching are the only built-in
    # filter concepts; every other rail entry is a value that exists on
    # disk right now.

    LOCAL_MODEL_SORTS = {
        "repo_id": "local_models.repo_id",
        "size_bytes": "local_models.size_bytes",
        "parameter_count": "local_models.parameter_count",
        "file_count": "local_models.file_count",
        "modified_at": "local_models.modified_at",
        "downloaded_at": "local_models.downloaded_at",
        "pipeline_tag": "local_models.pipeline_tag",
        "library_name": "local_models.library_name",
        "location": "local_models.location",
        "storage_backend": "local_models.storage_backend",
    }

    def query_visible_local_models(
        self,
        user_id: str,
        *,
        query: str = "",
        sort: str = "modified_at",
        order: str = "desc",
        favorited: bool = False,
        watching: bool = False,
        task: str = "",
        library: str = "",
        location: str = "",
        storage: str = "",
    ) -> list[dict[str, Any]]:
        column = self.LOCAL_MODEL_SORTS.get(sort)
        if column is None:
            raise ValueError(
                "Unknown sort column. Choose one of: " + ", ".join(sorted(self.LOCAL_MODEL_SORTS)) + "."
            )
        direction = "DESC" if str(order).lower() == "desc" else "ASC"
        if str(order).lower() not in ("asc", "desc"):
            raise ValueError("Order must be asc or desc.")
        clauses = [
            """
            (
                owned_repositories.id IS NULL
                OR owned_repositories.owner_id = ?
                OR owned_repositories.visibility = 'shared'
            )
            """
        ]
        parameters: list[Any] = [user_id]
        if query:
            clauses.append(
                "(LOWER(local_models.repo_id) LIKE LOWER(?) "
                "OR LOWER(local_models.pipeline_tag) LIKE LOWER(?) "
                "OR LOWER(local_models.library_name) LIKE LOWER(?))"
            )
            parameters.extend((f"%{query}%", f"%{query}%", f"%{query}%"))
        if favorited:
            clauses.append(
                "EXISTS (SELECT 1 FROM saved_models sm "
                "WHERE sm.user_id = ? AND sm.repo_id = local_models.repo_id)"
            )
            parameters.append(user_id)
        if watching:
            clauses.append(
                "EXISTS (SELECT 1 FROM watched_models wm "
                "WHERE wm.user_id = ? AND wm.repo_id = local_models.repo_id)"
            )
            parameters.append(user_id)
        if task:
            clauses.append("LOWER(local_models.pipeline_tag) = LOWER(?)")
            parameters.append(task)
        if library:
            clauses.append("LOWER(local_models.library_name) = LOWER(?)")
            parameters.append(library)
        if location:
            clauses.append("COALESCE(local_models.location, 'primary') = ?")
            parameters.append(location)
        if storage:
            clauses.append("local_models.storage_backend = ?")
            parameters.append(storage)
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT local_models.* FROM local_models "
                "LEFT JOIN owned_repositories "
                "ON owned_repositories.repo_id = local_models.repo_id "
                "WHERE " + " AND ".join(clauses) + " "
                f"ORDER BY {column} {direction}, local_models.repo_id ASC",
                parameters,
            ).fetchall()
        return [self._decode_row(row) for row in rows]

    def visible_local_model_facets(
        self, user_id: str, query: str = ""
    ) -> dict[str, Any]:
        """Rail counts over everything this viewer can see, narrowed by the
        text query only (facet filters never hide their own counts)."""
        clauses = [
            """
            (
                owned_repositories.id IS NULL
                OR owned_repositories.owner_id = ?
                OR owned_repositories.visibility = 'shared'
            )
            """
        ]
        parameters: list[Any] = [user_id]
        if query:
            clauses.append(
                "(LOWER(local_models.repo_id) LIKE LOWER(?) "
                "OR LOWER(local_models.pipeline_tag) LIKE LOWER(?) "
                "OR LOWER(local_models.library_name) LIKE LOWER(?))"
            )
            parameters.extend((f"%{query}%", f"%{query}%", f"%{query}%"))
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT local_models.pipeline_tag, local_models.library_name,
                       COALESCE(local_models.location, 'primary') AS location,
                       local_models.storage_backend,
                       EXISTS (SELECT 1 FROM saved_models sm
                               WHERE sm.user_id = ? AND sm.repo_id = local_models.repo_id) AS favorited,
                       EXISTS (SELECT 1 FROM watched_models wm
                               WHERE wm.user_id = ? AND wm.repo_id = local_models.repo_id) AS watching
                FROM local_models
                LEFT JOIN owned_repositories
                    ON owned_repositories.repo_id = local_models.repo_id
                WHERE
                """
                + " AND ".join(clauses),
                [user_id, user_id, *parameters],
            ).fetchall()

        def bucket(key: str) -> list[dict[str, Any]]:
            counts: dict[str, int] = {}
            for row in rows:
                value = row[key]
                if value in (None, ""):
                    continue
                counts[value] = counts.get(value, 0) + 1
            return [
                {"value": value, "count": count}
                for value, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
            ]

        return {
            "total": len(rows),
            "favorited": sum(1 for row in rows if row["favorited"]),
            "watching": sum(1 for row in rows if row["watching"]),
            "tasks": bucket("pipeline_tag"),
            "libraries": bucket("library_name"),
            "locations": bucket("location"),
            "storage": bucket("storage_backend"),
        }

    def create_collection(self, record: dict[str, Any]) -> dict[str, Any]:
        with self._write_lock, self.connect() as connection:
            connection.execute(
                """
                INSERT INTO collections (
                    id, user_id, name, description, created_at, updated_at
                ) VALUES (
                    :id, :user_id, :name, :description, :created_at, :updated_at
                )
                """,
                record,
            )
        return self.get_collection(record["id"], record["user_id"])

    def get_collection(self, collection_id: str, user_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM collections WHERE id = ? AND user_id = ?",
                (collection_id, user_id),
            ).fetchone()
        return dict(row) if row else None

    def list_collections(self, user_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT collections.*, COUNT(collection_items.saved_model_id) AS model_count
                FROM collections
                LEFT JOIN collection_items
                    ON collection_items.collection_id = collections.id
                WHERE collections.user_id = ?
                GROUP BY collections.id
                ORDER BY LOWER(collections.name)
                """,
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_collection(self, collection_id: str, user_id: str) -> bool:
        with self._write_lock, self.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM collections WHERE id = ? AND user_id = ?",
                (collection_id, user_id),
            )
        return cursor.rowcount > 0

    def save_model(self, record: dict[str, Any], collection_ids: list[str]) -> dict[str, Any]:
        collection_ids = list(dict.fromkeys(collection_ids))
        with self._write_lock, self.connect() as connection:
            connection.execute(
                """
                INSERT INTO saved_models (
                    id, user_id, repo_id, note, metadata_json, created_at, updated_at
                ) VALUES (
                    :id, :user_id, :repo_id, :note, :metadata_json, :created_at, :updated_at
                )
                ON CONFLICT(user_id, repo_id) DO UPDATE SET
                    note = excluded.note,
                    metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at
                """,
                record,
            )
            row = connection.execute(
                "SELECT id FROM saved_models WHERE user_id = ? AND repo_id = ?",
                (record["user_id"], record["repo_id"]),
            ).fetchone()
            saved_id = row["id"]
            connection.execute(
                "DELETE FROM collection_items WHERE saved_model_id = ?", (saved_id,)
            )
            if collection_ids:
                valid = connection.execute(
                    """
                    SELECT id FROM collections
                    WHERE user_id = ? AND id IN ({})
                    """.format(",".join("?" for _ in collection_ids)),
                    (record["user_id"], *collection_ids),
                ).fetchall()
                connection.executemany(
                    """
                    INSERT INTO collection_items (
                        collection_id, saved_model_id, created_at
                    ) VALUES (?, ?, ?)
                    """,
                    (
                        (item["id"], saved_id, record["updated_at"])
                        for item in valid
                    ),
                )
        return self.get_saved_model(record["user_id"], record["repo_id"])

    def _saved_collections(
        self,
        connection: sqlite3.Connection | _PostgresConnection,
        saved_id: str,
    ) -> list[str]:
        rows = connection.execute(
            "SELECT collection_id FROM collection_items WHERE saved_model_id = ?",
            (saved_id,),
        ).fetchall()
        return [row["collection_id"] for row in rows]

    def get_saved_model(self, user_id: str, repo_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM saved_models WHERE user_id = ? AND repo_id = ?",
                (user_id, repo_id),
            ).fetchone()
            if not row:
                return None
            result = self._decode_row(row)
            result["collections"] = self._saved_collections(connection, result["id"])
        return result

    def list_saved_models(
        self, user_id: str, query: str = "", collection_id: str = ""
    ) -> list[dict[str, Any]]:
        joins = ""
        clauses = ["saved_models.user_id = ?"]
        parameters: list[Any] = [user_id]
        if collection_id:
            joins = (
                " JOIN collection_items ON collection_items.saved_model_id = saved_models.id"
            )
            clauses.append("collection_items.collection_id = ?")
            parameters.append(collection_id)
        if query:
            clauses.append(
                "(LOWER(saved_models.repo_id) LIKE LOWER(?) "
                "OR LOWER(saved_models.note) LIKE LOWER(?))"
            )
            parameters.extend((f"%{query}%", f"%{query}%"))
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT DISTINCT saved_models.* FROM saved_models"
                + joins
                + " WHERE "
                + " AND ".join(clauses)
                + " ORDER BY saved_models.updated_at DESC",
                parameters,
            ).fetchall()
            results = []
            for row in rows:
                item = self._decode_row(row)
                item["collections"] = self._saved_collections(connection, item["id"])
                results.append(item)
        return results

    def saved_repo_ids(self, user_id: str) -> set[str]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT repo_id FROM saved_models WHERE user_id = ?", (user_id,)
            ).fetchall()
        return {row["repo_id"] for row in rows}

    def delete_saved_model(self, user_id: str, repo_id: str) -> bool:
        with self._write_lock, self.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM saved_models WHERE user_id = ? AND repo_id = ?",
                (user_id, repo_id),
            )
        return cursor.rowcount > 0

    def create_owned_repository(self, record: dict[str, Any]) -> dict[str, Any]:
        with self._write_lock, self.connect() as connection:
            connection.execute(
                """
                INSERT INTO owned_repositories (
                    id, owner_id, repo_id, description, visibility, status,
                    created_at, updated_at
                ) VALUES (
                    :id, :owner_id, :repo_id, :description, :visibility, :status,
                    :created_at, :updated_at
                )
                """,
                record,
            )
        return self.get_owned_repository(record["repo_id"], record["owner_id"])

    def get_owned_repository(
        self, repo_id: str, owner_id: str | None = None
    ) -> dict[str, Any] | None:
        with self.connect() as connection:
            if owner_id:
                row = connection.execute(
                    """
                    SELECT owned_repositories.*, users.username AS owner_username,
                           users.display_name AS owner_display_name
                    FROM owned_repositories
                    JOIN users ON users.id = owned_repositories.owner_id
                    WHERE repo_id = ? AND owner_id = ?
                    """,
                    (repo_id, owner_id),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    SELECT owned_repositories.*, users.username AS owner_username,
                           users.display_name AS owner_display_name
                    FROM owned_repositories
                    JOIN users ON users.id = owned_repositories.owner_id
                    WHERE repo_id = ?
                    """,
                    (repo_id,),
                ).fetchone()
        return dict(row) if row else None

    def list_owned_repositories(self, user_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT owned_repositories.*, users.username AS owner_username,
                       users.display_name AS owner_display_name,
                       local_models.size_bytes, local_models.file_count,
                       local_models.modified_at
                FROM owned_repositories
                JOIN users ON users.id = owned_repositories.owner_id
                LEFT JOIN local_models ON local_models.repo_id = owned_repositories.repo_id
                WHERE owner_id = ? OR visibility = 'shared'
                ORDER BY owned_repositories.updated_at DESC
                """,
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_owned_repository(
        self, repo_id: str, owner_id: str, **changes: Any
    ) -> dict[str, Any] | None:
        allowed = {
            key: value
            for key, value in changes.items()
            if key in {"description", "visibility", "status", "updated_at"}
        }
        if not allowed:
            return self.get_owned_repository(repo_id, owner_id)
        assignments = ", ".join(f"{key} = :{key}" for key in allowed)
        parameters = {**allowed, "repo_id": repo_id, "owner_id": owner_id}
        with self._write_lock, self.connect() as connection:
            connection.execute(
                f"""
                UPDATE owned_repositories SET {assignments}
                WHERE repo_id = :repo_id AND owner_id = :owner_id
                """,
                parameters,
            )
        return self.get_owned_repository(repo_id, owner_id)

    def delete_owned_repository(self, repo_id: str, owner_id: str) -> bool:
        with self._write_lock, self.connect() as connection:
            owned = connection.execute(
                "SELECT 1 FROM owned_repositories WHERE repo_id = ? AND owner_id = ?",
                (repo_id, owner_id),
            ).fetchone()
            if not owned:
                return False
            cursor = connection.execute(
                "DELETE FROM owned_repositories WHERE repo_id = ? AND owner_id = ?",
                (repo_id, owner_id),
            )
            connection.execute("DELETE FROM local_models WHERE repo_id = ?", (repo_id,))
        return cursor.rowcount > 0

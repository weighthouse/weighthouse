from __future__ import annotations

import hmac
import json
import logging
import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from .brand import BRAND
from .agentkeys import (
    AGENT_KEY_PREFIX,
    AGENT_KEY_SCOPES,
    MAX_AGENT_KEYS,
    AgentKeyDenied,
    AgentKeyError,
    generate_agent_key,
    key_hint,
    parse_scopes,
    resolve_agent_bearer,
)
from .auth import AuthService, utc_iso
from .config import settings, validate_repo_id
from .database import INTEGRITY_ERRORS, Database
from .downloads import DownloadManager
from .deletion import (
    DeletionError,
    build_deletion_proposal,
    delete_selected_files,
    delete_whole_copy,
)
from .hub_service import HubService
from .indexer import LocalModelIndexer
from .locations import StorageLocation, find_location, parse_locations
from .mcp_server import McpServices, handle_mcp_message, parse_error_response
from .moves import MoveError, apply_move, build_move_plan
from .verify import VerifyError, verify_copy
from .manifests import read_manifest
from .updates import (
    UpdateApplyError,
    UpdateDiffError,
    apply_paths,
    build_update_diff,
    exact_patterns,
)
from .runtimes import RuntimeManager
from .storage import create_model_storage
from .uploads import UploadManager
from .watch import WatchError, WatchPoller, build_watch_payload, ensure_watch

database = Database(settings.database_target)
hub = HubService(settings)
indexer = LocalModelIndexer(settings, database)
model_storage = create_model_storage(settings)
logger = logging.getLogger(__name__)
downloads = DownloadManager(settings, database, hub, indexer, model_storage)
auth = AuthService(settings, database)
uploads = UploadManager(settings, database, indexer, model_storage)
runtimes = RuntimeManager(settings, database)

# Watch poller (D13): the quiet loop that gives a watch its meaning —
# staggered light revision checks; an unreachable upstream burns its
# slot without touching any recorded observation.
def fetch_watch_sha(repo_id: str) -> str | None:
    return hub.model_summary(repo_id).get("sha")

watch_poller = WatchPoller(settings, database, fetch_watch_sha)

# Filesystem storage locations (admin-declared allowlist; MODEL_STORAGE is
# always the implicit "primary"). Parsing is eager so an unusable allowlist
# stops startup with a precise message instead of dropping a root silently.
locations, primary_location_name = parse_locations(
    settings.locations_json,
    default_root=settings.model_storage,
)
indexer.set_locations(locations)

def model_root(model: dict[str, Any]) -> Path:
    """Resolve a stored copy's directory through its declared location.

    Every writer derives relative_path inside its root; this assertion
    keeps a hand-edited database row from pointing anywhere else.
    """
    declared = find_location(locations, model.get("location"))
    root = (declared or locations[0]).root
    target = (root / model["relative_path"]).resolve()
    resolved_root = root.resolve()
    if target != resolved_root and resolved_root not in target.parents:
        raise ValueError("A storage record pointed outside its declared location.")
    return target

def refresh_model_index() -> dict[str, Any]:
    result = indexer.scan()
    remote_models: list[dict[str, Any]] = []
    remote_error = None
    if model_storage.remote:
        try:
            remote_models = model_storage.discover_repositories()
            for model in remote_models:
                if model.get("source") == "user-upload":
                    owner_id = model.get("owner_id")
                    if (
                        not owner_id
                        or not database.get_owned_repository(model["repo_id"], owner_id)
                    ):
                        continue
                indexer.index_remote(model)
        except Exception as error:
            remote_error = (str(error).strip() or error.__class__.__name__)[:500]
    models = database.list_local_models()
    return {
        "count": len(models),
        "models": models,
        "local_count": result["count"],
        "remote_count": len(remote_models),
        "remote_error": remote_error,
        "scanned_at": result["scanned_at"],
    }

@asynccontextmanager
async def lifespan(_: FastAPI):
    ensure_deployment_posture()
    settings.ensure_directories()
    database.initialize()
    database.fail_unfinished_runtime_jobs(utc_iso())
    auth.ensure_local_user()
    await run_in_threadpool(refresh_model_index)
    downloads.resume_unfinished()
    watch_poller.start()
    yield
    downloads.shutdown()
    runtimes.shutdown()
    watch_poller.stop()
    hub.close()

app = FastAPI(
    title=f"{BRAND.display_name} API",
    version=settings.app_version,
    lifespan=lifespan,
    # The schema is public shape only in the code; serving the interactive
    # explorer unauthenticated was the one doc leak left. Operators read
    # the code; the API surface is small and named.
    docs_url=None,
    redoc_url=None,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=[
        "Content-Type",
        "Authorization",
        "X-CSRF-Token",
        "Upload-Offset",
        "Upload-Length",
    ],
)

@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if settings.content_security_policy:
        response.headers["Content-Security-Policy"] = settings.content_security_policy
    return response

@app.middleware("http")
async def refuse_cross_origin_writes(request: Request, call_next):
    """Origin belt-and-braces beside the CSRF guard.

    SameSite plus the double-submit token carry the protection; this
    refuses the case the day a future client ships without them: a
    state-changing request that carries our session cookie while naming
    a foreign Origin is not ours to act on. No Origin header (curl,
    same-origin form posts) checks nothing.
    """
    if (
        settings.origin_check
        and request.method not in SAFE_REQUEST_METHODS
        and auth.cookie_name in request.cookies
    ):
        origin = request.headers.get("origin")
        if origin:
            foreign = urlsplit(origin).netloc
            if foreign and foreign != request.headers.get("host", ""):
                return JSONResponse(
                    {"detail": "Cross-origin writes are refused."}, status_code=403
                )
    return await call_next(request)

@app.middleware("http")
async def agent_key_audit(request: Request, call_next):
    """Record one audit row per agent-key request, outcome included."""
    response = await call_next(request)
    agent_key = getattr(request.state, "agent_key", None)
    if agent_key:
        await run_in_threadpool(
            database.record_agent_key_event,
            {
                "id": uuid.uuid4().hex,
                "key_id": agent_key["id"],
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "created_at": utc_iso(),
            },
        )
    return response


class CredentialsRequest(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=256)

class SetupRequest(CredentialsRequest):
    display_name: str = Field(default="", max_length=80)

class CreateUserRequest(SetupRequest):
    pass

class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=8, max_length=256)

class DownloadRequest(BaseModel):
    repo_id: str
    revision: str = Field(default="main", max_length=200)
    mode: Literal["full", "safetensors", "gguf", "metadata", "custom"] = "full"
    gguf_path: str = Field(default="", max_length=300)
    exclude_unsafe: bool = False
    include_patterns: list[str] = Field(default_factory=list, max_length=50)
    exclude_patterns: list[str] = Field(default_factory=list, max_length=50)
    watch: bool = True  # D13: downloading subscribes; opt-out keeps an existing watch

    @field_validator("repo_id")
    @classmethod
    def repo_is_valid(cls, value: str) -> str:
        return validate_repo_id(value)

    @field_validator("include_patterns", "exclude_patterns")
    @classmethod
    def patterns_are_bounded(cls, values: list[str]) -> list[str]:
        cleaned = []
        for value in values:
            item = value.strip()
            if len(item) > 200:
                raise ValueError("File patterns must be 200 characters or fewer.")
            if item:
                cleaned.append(item)
        return cleaned

class CollectionRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=240)

class SavedModelRequest(BaseModel):
    repo_id: str
    note: str = Field(default="", max_length=1000)
    collection_ids: list[str] = Field(default_factory=list, max_length=50)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("repo_id")
    @classmethod
    def saved_repo_is_valid(cls, value: str) -> str:
        return validate_repo_id(value)

class RepositoryRequest(BaseModel):
    slug: str = Field(min_length=1, max_length=96)
    description: str = Field(default="", max_length=500)
    visibility: Literal["private", "shared"] = "private"

class RepositoryUpdateRequest(BaseModel):
    description: str = Field(default="", max_length=500)
    visibility: Literal["private", "shared"] = "private"

class DeleteRepositoryRequest(BaseModel):
    confirmation: str = Field(min_length=1, max_length=200)

class RuntimeLoadRequest(BaseModel):
    repo_id: str
    runtime_model_name: str | None = Field(default=None, max_length=128)
    source_file: str | None = Field(default=None, max_length=500)

    @field_validator("repo_id")
    @classmethod
    def runtime_repo_is_valid(cls, value: str) -> str:
        return validate_repo_id(value)

def session_for_request(request: Request) -> dict[str, Any] | None:
    return auth.session(request.cookies.get(auth.cookie_name))

def require_user(request: Request) -> dict[str, Any]:
    if auth.setup_required():
        raise HTTPException(status_code=428, detail="Create the owner account first.")
    session = session_for_request(request)
    if not session or not session.get("user"):
        raise HTTPException(status_code=401, detail="Sign in to continue.")
    request.state.auth_session = session
    return session["user"]

def require_write_user(
    request: Request, user: Annotated[dict[str, Any], Depends(require_user)]
) -> dict[str, Any]:
    session = request.state.auth_session
    if not auth.verify_csrf(session, request.headers.get("X-CSRF-Token")):
        raise HTTPException(status_code=403, detail="Security token is missing or expired.")
    return user

def require_admin(
    request: Request, user: Annotated[dict[str, Any], Depends(require_user)]
) -> dict[str, Any]:
    # CSRF is a write guard, not a read guard: browsers never send the
    # header on GET, so demanding it on admin reads made /api/agent-keys
    # (and its events) permanently 403 for the real UI while every
    # non-admin read worked. Mirror scoped_agent_dependency: verify the
    # token only where it can matter — a request that changes something.
    if request.method not in SAFE_REQUEST_METHODS:
        session = request.state.auth_session
        if not auth.verify_csrf(session, request.headers.get("X-CSRF-Token")):
            raise HTTPException(status_code=403, detail="Security token is missing or expired.")
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Administrator access is required.")
    return user

CurrentUser = Annotated[dict[str, Any], Depends(require_user)]
WriteUser = Annotated[dict[str, Any], Depends(require_write_user)]
AdminUser = Annotated[dict[str, Any], Depends(require_admin)]

SAFE_REQUEST_METHODS = ("GET", "HEAD")

def scoped_agent_dependency(*required_scopes: str):
    """Authenticate an agent-callable endpoint by key or by session.

    A bearer key opens the door when it carries every required scope and
    acts with its owner's identity. Session callers keep the ordinary
    rules — sign-in and CSRF on writes — unchanged.
    """

    def dependency(request: Request) -> dict[str, Any]:
        header = request.headers.get("authorization") or ""
        if header.startswith(f"Bearer {AGENT_KEY_PREFIX}"):
            try:
                context = resolve_agent_bearer(header[7:].strip(), database, required_scopes)
            except AgentKeyDenied as error:
                raise HTTPException(status_code=error.status, detail=error.message) from error
            request.state.agent_key = context["key"]
            database.touch_agent_key(context["key"]["id"], utc_iso())
            return context["user"]
        user = require_user(request)
        if request.method not in SAFE_REQUEST_METHODS:
            session = request.state.auth_session
            if not auth.verify_csrf(session, request.headers.get("X-CSRF-Token")):
                raise HTTPException(
                    status_code=403, detail="Security token is missing or expired."
                )
        return user

    return dependency

AgentSearch = Annotated[dict[str, Any], Depends(scoped_agent_dependency("search"))]
AgentReadable = Annotated[dict[str, Any], Depends(scoped_agent_dependency("read"))]
AgentQueue = Annotated[dict[str, Any], Depends(scoped_agent_dependency("queue"))]
AgentMutable = Annotated[dict[str, Any], Depends(scoped_agent_dependency("mutate"))]
# MCP: the endpoint authenticates any valid key or session; per-tool
# scopes are enforced against the caller's own scope set at dispatch.
McpCaller = Annotated[dict[str, Any], Depends(scoped_agent_dependency())]

def mcp_services() -> McpServices:
    """One request-scoped view of the application's services."""
    return McpServices(
        database=database,
        hub=hub,
        downloads=downloads,
        indexer=indexer,
        model_storage=model_storage,
        settings=settings,
        rescan_index=refresh_model_index,
        tracked_update_diff=tracked_update_diff,
        watch_ensure=watch_ensure_for_user,
        locations=list(locations),
    )

def mcp_caller_scopes(request: Request, user: dict[str, Any]) -> set[str]:
    agent_key = getattr(request.state, "agent_key", None)
    if agent_key:
        return set(agent_key.get("scopes") or [])
    return set(AGENT_KEY_SCOPES)  # a signed-in human wields the full ladder

def set_session_cookie(response: Response, raw_token: str) -> None:
    response.set_cookie(
        auth.cookie_name,
        raw_token,
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,
        secure=settings.cookies_secure,
        samesite="lax",
        path="/",
    )

TRANSPORT_WARNING = (
    "Sign-ins will not stick: this deployment marks its cookies Secure but is served "
    "over plain HTTP. Put TLS in front, or acknowledge the tradeoff with "
    "ALLOW_INSECURE_COOKIES=true and restart."
)

def transport_warning_for(
    request: Request, settings_snapshot: Any = None
) -> str | None:
    """The posture banner's text, populated only when reality disagrees.

    uvicorn's proxy-headers support rewrites the scheme from
    X-Forwarded-Proto, so behind a TLS proxy this stays None even
    when the traffic arrived over the LAN.
    """
    snapshot = settings_snapshot or settings
    if not snapshot.cookies_secure:
        return None
    if request.url.scheme == "https":
        return None
    return TRANSPORT_WARNING

def ensure_deployment_posture(settings_snapshot: Any = None) -> None:
    """Startup refusals for configurations nobody should run by accident.

    Disabling accounts removes sign-in from a deployment that proxies
    model downloads on the owner's behalf; that remains possible, but
    only with an explicit acknowledgment at the same place the rest of
    the posture lives.
    """
    snapshot = settings_snapshot or settings
    if not snapshot.accounts_enabled and not snapshot.allow_accountless:
        raise RuntimeError(
            "ACCOUNTS_ENABLED=false requires an explicit safety acknowledgment: "
            "set ALLOW_ACCOUNTLESS=true as well. Without accounts, anyone who "
            "can reach this deployment drives its downloads and storage."
        )

def auth_payload(session: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "accounts_enabled": settings.accounts_enabled,
        "setup_required": auth.setup_required(),
        "user": session.get("user") if session else None,
        "csrf_token": session.get("csrf_token") if session else None,
    }

@app.get("/api/health")
def health(request: Request) -> dict:
    settings.ensure_directories()
    usage = shutil.disk_usage(settings.model_storage)
    object_storage = model_storage.health()
    signed_in = bool((session_for_request(request) or {}).get("user"))
    storage = {
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
        "writable": os_access_writable(settings.model_storage),
    }
    # The absolute storage path is deployment topology; that belongs to
    # the operator, not to anyone who can reach the sign-in screen.
    if signed_in:
        storage["path"] = str(settings.model_storage)
    return {
        "status": "ok" if object_storage["connected"] else "degraded",
        "app": settings.app_name,
        "version": settings.app_version,
        "database_backend": database.backend,
        "storage": storage,
        "object_storage": object_storage,
        "hf_token_configured": bool(settings.hf_token),
        "hf_endpoint": settings.hf_endpoint,
        "accounts_enabled": settings.accounts_enabled,
        "upload_chunk_bytes": settings.upload_chunk_mb * 1024**2,
        "max_upload_size_bytes": settings.max_upload_size_gb * 1024**3,
        "runtime_target_count": len(runtimes.targets),
        "runtime_api_token_configured": bool(settings.runtime_api_token),
    }

def os_access_writable(path: Path) -> bool:
    try:
        probe = path / BRAND.fs_probe_filename
        probe.touch(exist_ok=True)
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False

@app.get("/api/auth/status")
def auth_status(request: Request) -> dict:
    payload = auth_payload(session_for_request(request))
    payload["transport_warning"] = transport_warning_for(request)
    return payload

@app.post("/api/auth/setup", status_code=201)
def setup_account(payload: SetupRequest, response: Response) -> dict:
    if not settings.accounts_enabled:
        raise HTTPException(status_code=409, detail="Accounts are disabled.")
    if not auth.setup_required():
        raise HTTPException(status_code=409, detail="The owner account already exists.")
    try:
        user = auth.create_owner(payload.username, payload.display_name, payload.password)
    except (ValueError, *INTEGRITY_ERRORS) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    raw_token, csrf_token = auth.create_session(user["id"])
    set_session_cookie(response, raw_token)
    return auth_payload({"user": user, "csrf_token": csrf_token})

@app.post("/api/auth/login")
def login(payload: CredentialsRequest, request: Request, response: Response) -> dict:
    if not settings.accounts_enabled:
        raise HTTPException(status_code=409, detail="Accounts are disabled.")
    client = request.client.host if request.client else "unknown"
    try:
        user = auth.authenticate(
            payload.username, payload.password, f"{client}:{payload.username.lower()}"
        )
    except ValueError as error:
        raise HTTPException(status_code=429, detail=str(error)) from error
    if not user:
        raise HTTPException(status_code=401, detail="Username or password is incorrect.")
    raw_token, csrf_token = auth.create_session(user["id"])
    set_session_cookie(response, raw_token)
    public_user = database.get_user(user["id"], include_secret=False)
    return auth_payload({"user": public_user, "csrf_token": csrf_token})

@app.post("/api/auth/logout")
def logout(request: Request, response: Response, _: WriteUser) -> dict:
    auth.revoke(request.cookies.get(auth.cookie_name))
    response.delete_cookie(auth.cookie_name, path="/")
    return {"status": "signed_out"}

@app.get("/api/users")
def list_users(user: CurrentUser) -> dict:
    if user["role"] != "admin":
        return {"items": [user]}
    return {"items": database.list_users()}

@app.post("/api/users", status_code=201)
def create_user(payload: CreateUserRequest, _: AdminUser) -> dict:
    try:
        return auth.create_user(
            payload.username, payload.display_name, payload.password, role="member"
        )
    except (ValueError, *INTEGRITY_ERRORS) as error:
        detail = (
            "That username is already in use."
            if isinstance(error, INTEGRITY_ERRORS)
            else str(error)
        )
        raise HTTPException(status_code=400, detail=detail) from error

@app.patch("/api/account/password")
def change_password(
    payload: PasswordChangeRequest, request: Request, user: WriteUser
) -> dict:
    raw_token = request.cookies.get(auth.cookie_name)
    if not raw_token or not settings.accounts_enabled:
        raise HTTPException(
            status_code=409,
            detail="Password changes are unavailable when accounts are disabled.",
        )
    try:
        auth.change_password(
            user["id"], payload.current_password, payload.new_password, raw_token
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"status": "password_changed"}

@app.get("/api/hub/models")
async def search_hub_models(
    user: AgentSearch,
    search: Annotated[str, Query(max_length=200)] = "",
    sort: Literal["trending", "downloads", "updated", "likes"] = "trending",
    task: Annotated[str, Query(max_length=100)] = "",
    library: Annotated[str, Query(max_length=100)] = "",
    app_filter: Annotated[str, Query(alias="app", max_length=100)] = "",
    parameters: Annotated[str, Query(max_length=100)] = "",
    limit: Annotated[int, Query(ge=1, le=50)] = 30,
) -> dict:
    try:
        items = await run_in_threadpool(
            hub.search_models,
            search,
            sort,
            task,
            library,
            app_filter,
            parameters,
            limit,
        )
    except Exception as error:
        logger.exception("Hub search failed")
        raise HTTPException(
            status_code=502, detail="Hugging Face Hub request failed."
        ) from error
    local_ids = {model["repo_id"] for model in database.list_local_models()}
    saved_ids = database.saved_repo_ids(user["id"])
    for item in items:
        item["local"] = item["id"] in local_ids
        item["saved"] = item["id"] in saved_ids
    return {"items": items, "count": len(items)}

@app.get("/api/hub/gguf-range")
async def hub_gguf_range(
    request: Request,
    _: CurrentUser,
    repo_id: Annotated[str, Query(max_length=200)],
    filename: Annotated[str, Query(max_length=500)],
    revision: Annotated[str, Query(max_length=200)] = "main",
) -> Response:
    try:
        result = await run_in_threadpool(
            hub.read_gguf_range,
            repo_id,
            filename,
            revision,
            request.headers.get("Range"),
        )
        return Response(
            content=result["content"],
            status_code=result["status_code"],
            media_type="application/octet-stream",
            headers=result["headers"],
        )
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=416, detail=str(error)) from error
    except Exception as error:
        logger.exception("GGUF range read failed for %s/%s", repo_id, filename)
        raise HTTPException(
            status_code=502, detail="Unable to read GGUF metadata."
        ) from error

@app.get("/api/hub/models/{repo_id:path}")
async def hub_model(repo_id: str, user: AgentSearch, revision: str = "main") -> dict:
    try:
        validated = validate_repo_id(repo_id)
        details = await run_in_threadpool(hub.model_details, validated, revision)
        details["model_card"] = await run_in_threadpool(
            hub.read_model_card, validated, revision
        )
        details["local"] = database.get_local_model(validated) is not None
        details["saved"] = validated in database.saved_repo_ids(user["id"])
        return details
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        logger.exception("Hub model details failed for %s", repo_id)
        raise HTTPException(status_code=502, detail="Unable to load model.") from error

def can_access_download(download: dict[str, Any], user: dict[str, Any]) -> bool:
    return (
        download.get("user_id") == user["id"]
        or (user["role"] == "admin" and download.get("user_id") is None)
    )

@app.get("/api/downloads")
def list_downloads(user: AgentReadable) -> dict:
    items = database.list_downloads(
        user_id=user["id"], include_unowned=user["role"] == "admin"
    )
    return {
        "items": items,
        "active": sum(
            item["status"] in {"queued", "preparing", "downloading"} for item in items
        ),
    }

@app.get("/api/downloads/{download_id}")
def get_download(download_id: str, user: AgentReadable) -> dict:
    download = database.get_download(download_id)
    if not download or not can_access_download(download, user):
        raise HTTPException(status_code=404, detail="Download not found")
    return download

@app.post("/api/downloads", status_code=202)
def start_download(payload: DownloadRequest, user: AgentQueue) -> dict:
    if database.get_owned_repository(payload.repo_id):
        raise HTTPException(
            status_code=409,
            detail="An account-owned repository already uses this storage path.",
        )
    try:
        job = downloads.queue(
            payload.repo_id,
            payload.revision,
            payload.mode,
            user_id=user["id"],
            gguf_path=payload.gguf_path,
            exclude_unsafe=payload.exclude_unsafe,
            custom_include=payload.include_patterns,
            custom_exclude=payload.exclude_patterns,
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if payload.watch:
        # D13 default: a download subscribes its owner to upstream
        # change. Opt-out leaves any existing watch untouched.
        try:
            watch_ensure_for_user(payload.repo_id, user["id"])
        except WatchError as error:
            return {**job, "watch_error": str(error)}
    return job

def watch_ensure_for_user(repo_id: str, user_id: str) -> dict[str, Any]:
    """The one subscription path shared by HTTP, MCP, and the app."""
    try:
        validated = validate_repo_id(repo_id)
    except ValueError as error:
        raise WatchError(str(error)) from error
    copy = database.get_local_model(validated)
    snapshot = None
    if not copy:
        try:
            snapshot = hub.model_summary(validated)
        except Exception as error:
            raise WatchError(f"Upstream lookup failed: {error}") from error
    return ensure_watch(
        database,
        snapshot,
        validated,
        user_id,
        copy_sha=(copy or {}).get("sha"),
        clock=utc_iso,
    )

class ClearDownloadsRequest(BaseModel):
    ids: list[str] = Field(default_factory=list)


def _get_download_or_404(download_id: str, user: dict[str, Any]) -> dict:
    existing = database.get_download(download_id)
    if not existing or not can_access_download(existing, user):
        raise HTTPException(status_code=404, detail="Download not found")
    return existing


_DOWNLOAD_LIVE_STATUSES = {"queued", "preparing", "downloading", "paused"}


@app.post("/api/downloads/{download_id}/cancel")
def cancel_download(download_id: str, user: AgentQueue) -> dict:
    _get_download_or_404(download_id, user)
    try:
        return downloads.cancel(download_id)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post("/api/downloads/{download_id}/pause")
def pause_download(download_id: str, user: AgentQueue) -> dict:
    _get_download_or_404(download_id, user)
    try:
        return downloads.pause(download_id)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post("/api/downloads/{download_id}/resume")
def resume_download(download_id: str, user: AgentQueue) -> dict:
    _get_download_or_404(download_id, user)
    download = downloads.resume(download_id)
    if not download:
        raise HTTPException(status_code=404, detail="Download not found")
    if download["status"] != "queued":
        raise HTTPException(status_code=409, detail="Only paused downloads can be resumed.")
    return download


@app.post("/api/downloads/pause-all")
def pause_all_downloads(user: AgentQueue) -> dict:
    return {"paused": downloads.pause_all()}


@app.post("/api/downloads/resume-all")
def resume_all_downloads(user: AgentQueue) -> dict:
    return {"resumed": downloads.resume_all()}


@app.post("/api/downloads/{download_id}/restart")
def restart_download(download_id: str, user: AgentQueue) -> dict:
    _get_download_or_404(download_id, user)
    download = downloads.restart(download_id)
    if not download or download["status"] != "queued":
        raise HTTPException(status_code=409, detail="Only failed downloads can be restarted.")
    return download


@app.post("/api/downloads/{download_id}/move-to-front")
def move_download_to_front(download_id: str, user: AgentQueue) -> dict:
    _get_download_or_404(download_id, user)
    download = downloads.move_to_front(download_id)
    if not download or download["status"] != "queued":
        raise HTTPException(
            status_code=409, detail="Only queued downloads can be moved."
        )
    return download


@app.delete("/api/downloads/{download_id}")
def delete_download(download_id: str, user: AgentQueue) -> dict:
    existing = _get_download_or_404(download_id, user)
    if existing["status"] in _DOWNLOAD_LIVE_STATUSES:
        raise HTTPException(
            status_code=409, detail="This download is still active; cancel it first."
        )
    database.delete_download(download_id)
    return {"deleted": True}


@app.post("/api/downloads/clear")
def clear_downloads(payload: ClearDownloadsRequest, user: AgentQueue) -> dict:
    """Remove history records for the given ids. Files on disk stay;
    the Delete panel manages those."""
    deleted = 0
    for job_id in list(dict.fromkeys(payload.ids))[:500]:
        download = database.get_download(job_id)
        if not download or not can_access_download(download, user):
            continue
        if download["status"] in _DOWNLOAD_LIVE_STATUSES:
            continue
        if database.delete_download(job_id):
            deleted += 1
    return {"deleted": deleted}

@app.get("/api/local-models")
def local_models(
    user: AgentReadable,
    query: Annotated[str, Query(max_length=200)] = "",
    sort: Annotated[str, Query(max_length=40)] = "modified_at",
    order: Annotated[str, Query(max_length=4)] = "desc",
    favorited: bool = False,
    watching: bool = False,
    task: Annotated[str, Query(max_length=100)] = "",
    library: Annotated[str, Query(max_length=100)] = "",
    location: Annotated[str, Query(max_length=100)] = "",
    storage: Annotated[str, Query(max_length=20)] = "",
) -> dict:
    try:
        items = database.query_visible_local_models(
            user["id"],
            query=query,
            sort=sort,
            order=order,
            favorited=favorited,
            watching=watching,
            task=task,
            library=library,
            location=location,
            storage=storage,
        )
        facets = database.visible_local_model_facets(user["id"], query)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    is_admin = user["role"] == "admin"
    note_hits: dict[str, list[dict[str, Any]]] = {}
    if query:
        for hit in database.note_search_hits(user["id"], query, is_admin):
            note_hits.setdefault(hit["repo_id"], []).append(hit)
        if note_hits:
            matched = {item["repo_id"] for item in items}
            for repo_id in note_hits:
                if repo_id in matched:
                    continue
                model = database.get_visible_local_model(user["id"], repo_id)
                if model:
                    items.append(model)
    previews = database.note_previews(user["id"], is_admin)
    for item in items:
        if item["repo_id"] in note_hits:
            item["note_hits"] = note_hits[item["repo_id"]]
        if item["repo_id"] in previews:
            item["note_preview"] = previews[item["repo_id"]]
    return {
        "items": items,
        "count": len(items),
        "total_bytes": sum(item["size_bytes"] for item in items),
        "facets": facets,
    }

@app.get("/api/locations")
def list_locations(_: AgentReadable) -> dict:
    return {
        "items": [location.to_payload() for location in locations],
        "primary": primary_location_name,
        "count": len(locations),
    }

class AgentKeyRequest(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    scopes: list[str] = Field(min_length=1, max_length=8)
    # A key acts as the account it belongs to. An admin may mint for
    # another member; absent this, the key belongs to the caller.
    user_id: str | None = None

def public_agent_key(key: dict[str, Any]) -> dict[str, Any]:
    """The shareable view of a key: never its digest, never its raw token."""
    owner = key.get("user") or {}
    return {
        "id": key["id"],
        "name": key["name"],
        "key_hint": key["key_hint"],
        "scopes": key.get("scopes") or [],
        "created_at": key["created_at"],
        "last_used_at": key.get("last_used_at"),
        "revoked_at": key.get("revoked_at"),
        "user": {
            "id": owner.get("id"),
            "username": owner.get("username"),
            "display_name": owner.get("display_name"),
        },
    }

@app.get("/api/agent-keys")
def list_agent_keys_route(_: AdminUser) -> dict:
    return {
        "items": [public_agent_key(key) for key in database.list_agent_keys()],
        "scopes": AGENT_KEY_SCOPES,
        "max_active": MAX_AGENT_KEYS,
    }

@app.post("/api/agent-keys", status_code=201)
def create_agent_key_route(request: AgentKeyRequest, user: AdminUser) -> dict:
    try:
        scopes = parse_scopes(request.scopes)
    except AgentKeyError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    # The minted key acts as its owner's account; an admin picks that
    # account here (defaulting to their own).
    owner = user
    if request.user_id:
        looked = database.get_user(request.user_id, include_secret=False)
        if looked is None:
            raise HTTPException(status_code=400, detail="That user does not exist.")
        owner = looked
    if database.count_active_agent_keys() >= MAX_AGENT_KEYS:
        raise HTTPException(
            status_code=409,
            detail=f"At most {MAX_AGENT_KEYS} active agent keys are supported. Revoke one first.",
        )
    raw, digest = generate_agent_key()
    now = utc_iso()
    record = {
        "id": uuid.uuid4().hex,
        "name": request.name.strip(),
        "key_hash": digest,
        "key_hint": key_hint(raw),
        "scopes_json": json.dumps(scopes),
        "user_id": owner["id"],
        "created_at": now,
        "last_used_at": None,
        "revoked_at": None,
    }
    database.create_agent_key(record)
    return {
        "key": raw,  # shown exactly once; only the digest is stored
        "record": public_agent_key(
            {
                **record,
                "scopes": scopes,
                "user": {
                    "id": owner["id"],
                    "username": owner["username"],
                    "display_name": owner["display_name"],
                },
            }
        ),
    }

@app.delete("/api/agent-keys/{key_id}")
def revoke_agent_key_route(key_id: str, _: AdminUser) -> dict:
    if not database.revoke_agent_key(key_id, utc_iso()):
        raise HTTPException(
            status_code=404, detail="Agent API key not found or already revoked."
        )
    return {"status": "revoked"}

@app.delete("/api/agent-keys/{key_id}/purge")
def purge_agent_key_route(key_id: str, _: AdminUser) -> dict:
    """Retire a key from the record entirely: the row and its event
    history go, so the list stays a place for living credentials."""
    if not database.purge_agent_key(key_id):
        raise HTTPException(status_code=404, detail="Agent API key not found.")
    return {"status": "purged"}

@app.get("/api/agent-keys/{key_id}/events")
def agent_key_events_route(key_id: str, _: AdminUser, limit: int = 100) -> dict:
    key = database.get_agent_key(key_id)
    if not key:
        raise HTTPException(status_code=404, detail="Agent API key not found.")
    return {"items": database.list_agent_key_events(key_id, limit)}

@app.post("/api/mcp")
async def mcp_endpoint(request: Request, user: McpCaller) -> Response:
    """Streamable-HTTP MCP endpoint: one JSON-RPC message per POST,
    answered with a single JSON response. Stateless; no SSE."""
    raw = await request.body()
    try:
        payload = json.loads(raw or b"null")
    except (ValueError, UnicodeDecodeError):
        return JSONResponse(parse_error_response(), status_code=400)
    if payload is None:
        return JSONResponse(parse_error_response(), status_code=400)
    status, response = handle_mcp_message(
        payload,
        user,
        mcp_caller_scopes(request, user),
        mcp_services(),
    )
    if response is None:
        return Response(status_code=202)
    return JSONResponse(response, status_code=status)

@app.delete("/api/mcp")
def mcp_session_end(_: McpCaller) -> Response:
    """Stateless endpoint: sessions end by simply stopping."""
    return Response(status_code=204)

@app.get("/api/mcp")
def mcp_stream(_: McpCaller) -> Response:
    """No server-push stream in this deployment."""
    raise HTTPException(status_code=405, detail="Server-sent events are not offered; use POST.")

class WatchRequest(BaseModel):
    repo_id: str

    @field_validator("repo_id")
    @classmethod
    def watch_repo_is_valid(cls, value: str) -> str:
        return validate_repo_id(value)

@app.get("/api/watches")
def list_watches(user: CurrentUser) -> dict:
    """The watcher's subscriptions, shaped from stored facts only."""
    items = []
    for row in database.list_watched_models(user["id"]):
        copy = database.get_local_model(row["repo_id"])
        items.append(build_watch_payload(row, copy))
    previews = database.note_previews(user["id"], user["role"] == "admin")
    for item in items:
        if item["repo_id"] in previews:
            item["note_preview"] = previews[item["repo_id"]]
    return {
        "items": items,
        "count": len(items),
        "stale_count": sum(item["update_available"] for item in items),
    }

@app.post("/api/watches/check")
def check_watches_now(user: WriteUser) -> dict:
    """Immediate upstream checks for the caller's watches.

    Deliberately bypasses the poller's cadence so the button means
    "check now"; a one-second spacing between lookups keeps upstream
    load gentle for a user-initiated pass.
    """
    import time

    rows = database.list_watched_models(user["id"])
    checked = 0
    unreachable = 0
    for row in rows:
        if watch_poller.check_one(row, utc_iso()):
            checked += 1
        else:
            unreachable += 1
        time.sleep(1.0)
    return {"checked": checked, "unreachable": unreachable}


@app.post("/api/watches", status_code=201)
def watch_model(payload: WatchRequest, user: WriteUser) -> dict:
    try:
        row = watch_ensure_for_user(payload.repo_id, user["id"])
    except WatchError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    copy = database.get_local_model(payload.repo_id)
    return build_watch_payload(row, copy)

@app.delete("/api/watches/{repo_id:path}")
def unwatch_model(repo_id: str, user: WriteUser) -> dict:
    try:
        validated = validate_repo_id(repo_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if not database.delete_watched_model(user["id"], validated):
        raise HTTPException(status_code=404, detail="This model is not being watched.")
    return {"status": "unwatched"}

@app.post("/api/watches/{repo_id:path}/seen")
def mark_watch_seen(repo_id: str, user: WriteUser) -> dict:
    try:
        validated = validate_repo_id(repo_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    row = database.get_watched_model(user["id"], validated)
    if not row:
        raise HTTPException(status_code=404, detail="This model is not being watched.")
    upstream = str(row.get("upstream_sha") or "").strip()
    if not upstream:
        raise HTTPException(
            status_code=409,
            detail="Nothing to acknowledge: no upstream change has been recorded.",
        )
    row = database.mark_watch_seen(user["id"], validated, upstream, utc_iso())
    copy = database.get_local_model(validated)
    return build_watch_payload(row, copy)

@app.post("/api/local-models/scan")
async def scan_local_models(_: AgentMutable) -> dict:
    return await run_in_threadpool(refresh_model_index)

def visible_model(repo_id: str, user_id: str) -> dict[str, Any]:
    try:
        validated = validate_repo_id(repo_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    model = database.get_visible_local_model(user_id, validated)
    if not model:
        raise HTTPException(status_code=404, detail="Local model not found")
    return model

@app.post("/api/local-models/{repo_id:path}/restore")
async def restore_local_model(repo_id: str, user: AgentQueue) -> dict:
    model = visible_model(repo_id, user["id"])
    if model["storage_backend"] != "s3":
        raise HTTPException(status_code=409, detail="This model is not backed by S3.")
    if database.find_active_download(model["repo_id"]):
        raise HTTPException(
            status_code=409,
            detail="Wait for the active download to finish before restoring this cache.",
        )
    try:
        root = await run_in_threadpool(model_storage.restore_repository, model["repo_id"])
        await run_in_threadpool(indexer.index_path, root)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    result = indexer.files_for_model(model["repo_id"])
    if not result:
        raise HTTPException(status_code=404, detail="Local model not found")
    return result

@app.delete("/api/local-models/{repo_id:path}/cache")
async def evict_local_model_cache(repo_id: str, user: AgentMutable) -> dict:
    model = visible_model(repo_id, user["id"])
    if model["storage_backend"] != "s3":
        raise HTTPException(status_code=409, detail="Only S3-backed models have a removable cache.")
    if database.find_active_download(model["repo_id"]):
        raise HTTPException(
            status_code=409,
            detail="Wait for the active download to finish before removing this cache.",
        )
    try:
        await run_in_threadpool(model_storage.evict_repository_cache, model["repo_id"])
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    updated = database.set_local_model_cached(model["repo_id"], False)
    return {"status": "evicted", "model": updated}

# Model notes (D14): author-private per-model threads. Mutations belong to
# the author; the admin's ruled power is seeing everything, not editing
# other people's entries. Agents act with their owner's identity, so key
# callers reach their own notes exactly like session users do.

class NoteCreateRequest(BaseModel):
    body: str = Field(min_length=1, max_length=8000)
    pinned: bool = False

class NoteEditRequest(BaseModel):
    body: str = Field(min_length=1, max_length=8000)

class NotePinRequest(BaseModel):
    pinned: bool

class NoteShareRequest(BaseModel):
    shared: bool

def own_note(note_id: str, repo_id: str, user: dict[str, Any]) -> dict[str, Any]:
    note = database.get_model_note(note_id)
    if not note or note["repo_id"] != repo_id:
        raise HTTPException(status_code=404, detail="Note not found.")
    if note["author_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Only the author may change this note.")
    return note

@app.get("/api/local-models/{repo_id:path}/notes")
def list_model_notes_route(repo_id: str, user: AgentReadable) -> dict:
    model = visible_model(repo_id, user["id"])
    items = database.list_model_notes(model["repo_id"], user["id"], user["role"] == "admin")
    return {"items": items, "count": len(items)}

@app.post("/api/local-models/{repo_id:path}/notes", status_code=201)
def create_model_note_route(
    repo_id: str, payload: NoteCreateRequest, user: AgentMutable
) -> dict:
    model = visible_model(repo_id, user["id"])
    timestamp = utc_iso()
    note = database.create_model_note(
        {
            "id": uuid.uuid4().hex,
            "repo_id": model["repo_id"],
            "author_id": user["id"],
            "body": payload.body.strip(),
            "pinned": 0,
            "shared": 0,
            "created_at": timestamp,
            "updated_at": timestamp,
        }
    )
    if payload.pinned:
        note = database.set_model_note_pinned(note["id"], True, utc_iso())
    return note

@app.patch("/api/local-models/{repo_id:path}/notes/{note_id}")
def edit_model_note_route(
    repo_id: str, note_id: str, payload: NoteEditRequest, user: AgentMutable
) -> dict:
    model = visible_model(repo_id, user["id"])
    own_note(note_id, model["repo_id"], user)
    return database.update_model_note(
        note_id, body=payload.body.strip(), updated_at=utc_iso()
    )

@app.post("/api/local-models/{repo_id:path}/notes/{note_id}/pin")
def pin_model_note_route(
    repo_id: str, note_id: str, payload: NotePinRequest, user: AgentMutable
) -> dict:
    model = visible_model(repo_id, user["id"])
    own_note(note_id, model["repo_id"], user)
    updated = database.set_model_note_pinned(note_id, payload.pinned, utc_iso())
    if not updated:
        raise HTTPException(status_code=404, detail="Note not found.")
    return updated

@app.post("/api/local-models/{repo_id:path}/notes/{note_id}/share")
def share_model_note_route(
    repo_id: str, note_id: str, payload: NoteShareRequest, user: AgentMutable
) -> dict:
    model = visible_model(repo_id, user["id"])
    own_note(note_id, model["repo_id"], user)
    updated = database.set_model_note_shared(note_id, payload.shared, utc_iso())
    if not updated:
        raise HTTPException(status_code=404, detail="Note not found.")
    thread = database.list_model_notes(model["repo_id"], user["id"], user["role"] == "admin")
    # Same listing contract as the notes GET route; a bare list here
    # failed response validation and made every share a 500.
    return {"items": thread, "count": len(thread)}

@app.delete("/api/local-models/{repo_id:path}/notes/{note_id}")
def delete_model_note_route(repo_id: str, note_id: str, user: AgentMutable) -> dict:
    model = visible_model(repo_id, user["id"])
    own_note(note_id, model["repo_id"], user)
    database.delete_model_note(note_id)
    return {"status": "deleted"}

class UpdateApplyRequest(BaseModel):
    paths: list[str] = Field(default_factory=list)

class FileDeletionRequest(BaseModel):
    paths: list[str] = Field(min_length=1, max_length=200)

class CopyDeletionRequest(BaseModel):
    confirmation: str = Field(min_length=1, max_length=300)

class MoveRequest(BaseModel):
    destination: str = Field(min_length=1, max_length=40)

def tracked_model_root(model: dict[str, Any]) -> Path:
    return model_root(model)

def require_no_active_download(model: dict[str, Any], action: str) -> None:
    if database.find_active_download(model["repo_id"]):
        raise HTTPException(
            status_code=409,
            detail=f"Wait for the active download to finish before {action}.",
        )

@app.get("/api/local-models/{repo_id:path}/deletion")
def local_model_deletion_plan(repo_id: str, user: AgentReadable) -> dict:
    model = visible_model(repo_id, user["id"])
    return build_deletion_proposal(model["repo_id"], tracked_model_root(model))

@app.post("/api/local-models/{repo_id:path}/deletion/files")
def apply_local_model_file_deletion(
    repo_id: str, request: FileDeletionRequest, user: AgentMutable
) -> dict:
    model = visible_model(repo_id, user["id"])
    require_no_active_download(model, "deleting files")
    try:
        summary = delete_selected_files(
            model["repo_id"],
            tracked_model_root(model),
            request.paths,
            storage_backend=model["storage_backend"],
        )
    except DeletionError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    indexer.index_path(tracked_model_root(model))
    model_storage.sync_repository(model["repo_id"], tracked_model_root(model))
    return summary

@app.delete("/api/local-models/{repo_id:path}")
async def delete_local_model_copy(
    repo_id: str, request: CopyDeletionRequest, user: AgentMutable
) -> dict:
    model = visible_model(repo_id, user["id"])
    require_no_active_download(model, "deleting this model")
    try:
        await run_in_threadpool(
            delete_whole_copy,
            model["repo_id"],
            tracked_model_root(model),
            request.confirmation,
            model_storage=model_storage,
            storage_root=settings.model_storage,
        )
    except DeletionError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    database.delete_local_model(model["repo_id"])
    return {"status": "deleted"}

def require_location(name: str) -> StorageLocation:
    location = find_location(locations, name)
    if not location:
        raise HTTPException(status_code=404, detail=f"Unknown storage location '{name}'.")
    return location

@app.post("/api/local-models/{repo_id:path}/moves", status_code=202)
async def move_local_model(repo_id: str, request: MoveRequest, user: AgentMutable) -> dict:
    model = visible_model(repo_id, user["id"])
    if model["storage_backend"] != "filesystem":
        raise HTTPException(
            status_code=409,
            detail="S3-backed copies are moved by restoring or evicting the local cache instead.",
        )
    require_no_active_download(model, "moving this model")
    source = require_location(str(model.get("location") or primary_location_name))
    destination = require_location(request.destination)
    plan = await run_in_threadpool(
        _build_move_plan_checked,
        model["repo_id"],
        source,
        destination,
        model["relative_path"],
    )
    try:
        result = await run_in_threadpool(
            apply_move,
            plan,
            source,
            destination,
            indexer=indexer,
        )
    except MoveError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"status": "moved", **result}

def _build_move_plan_checked(
    repo_id: str,
    source: StorageLocation,
    destination: StorageLocation,
    relative_path: str,
) -> dict[str, Any]:
    free_bytes: int | None = None
    try:
        free_bytes = shutil.disk_usage(destination.root).free
    except OSError:
        free_bytes = None
    try:
        return build_move_plan(
            repo_id,
            source,
            destination,
            relative_path,
            destination_free_bytes=free_bytes,
        )
    except MoveError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

def tracked_update_diff(model: dict[str, Any], root: Path) -> dict[str, Any]:
    """Compute the update diff artifact for a library copy (or raise)."""
    manifest = read_manifest(root)
    try:
        details = hub.model_details(model["repo_id"], "main")
    except Exception as error:
        raise HTTPException(
            status_code=502, detail=f"Upstream lookup failed: {error}"
        ) from error
    try:
        return build_update_diff(manifest, details, root=root)
    except UpdateDiffError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

@app.get("/api/local-models/{repo_id:path}/verify")
def verify_local_model(repo_id: str, user: AgentReadable) -> dict:
    model = visible_model(repo_id, user["id"])
    root = tracked_model_root(model)
    try:
        return verify_copy(root)
    except VerifyError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

@app.get("/api/local-models/{repo_id:path}/updates")
async def local_model_updates(repo_id: str, user: AgentReadable) -> dict:
    model = visible_model(repo_id, user["id"])
    if database.find_active_download(model["repo_id"]):
        raise HTTPException(
            status_code=409,
            detail="Wait for the active download to finish before checking for updates.",
        )
    return await run_in_threadpool(tracked_update_diff, model, tracked_model_root(model))

@app.post("/api/local-models/{repo_id:path}/updates/apply", status_code=202)
async def apply_local_model_updates(
    repo_id: str, request: UpdateApplyRequest, user: AgentQueue
) -> dict:
    model = visible_model(repo_id, user["id"])
    if database.find_active_download(model["repo_id"]):
        raise HTTPException(
            status_code=409,
            detail="Wait for the active download to finish before updating this model.",
        )
    diff = await run_in_threadpool(tracked_update_diff, model, tracked_model_root(model))
    try:
        paths = apply_paths(diff, request.paths)
    except UpdateApplyError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if not paths:
        raise HTTPException(
            status_code=409,
            detail="Nothing to update: this copy already matches the upstream revision.",
        )
    try:
        return downloads.queue(
            model["repo_id"],
            diff["upstream"]["sha"],
            mode="custom",
            user_id=user["id"],
            custom_include=exact_patterns(paths),
            update_merge=True,
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

@app.get("/api/local-models/{repo_id:path}/card")
async def local_model_card(repo_id: str, user: AgentReadable) -> dict:
    """The card for THE CARD'S copy: pinned to the sha on disk (D17).

    A copy that has not moved never refetches; a copy that lands on a new
    revision snapshots that revision once and owns it thereafter. An
    unreachable upstream yields no card — better absent than stale.
    """
    model = visible_model(repo_id, user["id"])
    validated = model["repo_id"]
    copy = database.get_local_model(validated)
    sha = (copy or {}).get("sha") or None
    if sha:
        snapshot = database.get_card_snapshot(validated, sha)
        if snapshot:
            return {
                "sha": sha,
                "card": snapshot["card_text"],
                "fetched_at": snapshot["fetched_at"],
            }
        try:
            card = await run_in_threadpool(hub.read_model_card, validated, sha)
        except Exception:
            card = None
        fetched_at = None
        if card:
            fetched_at = utc_iso()
            database.store_card_snapshot(
                {
                    "repo_id": validated,
                    "sha": sha,
                    "card_text": card,
                    "fetched_at": fetched_at,
                }
            )
        return {"sha": sha, "card": card, "fetched_at": fetched_at}
    return {"sha": None, "card": None, "fetched_at": None}

@app.get("/api/local-models/{repo_id:path}")
def local_model(repo_id: str, user: AgentReadable) -> dict:
    model = visible_model(repo_id, user["id"])
    root = model_root(model)
    if model["storage_backend"] == "s3" and (not model["cached"] or not root.is_dir()):
        database.set_local_model_cached(model["repo_id"], False)
        remote = model_storage.list_repository_files(model["repo_id"])
        if not remote:
            raise HTTPException(status_code=404, detail="S3 model files were not found.")
        remote["model"] = database.get_local_model(model["repo_id"])
        return remote
    result = indexer.files_for_model(model["repo_id"])
    if not result:
        raise HTTPException(status_code=404, detail="Local model not found")
    return result

def require_runtime_admin(user: dict[str, Any]) -> None:
    if user["role"] != "admin":
        raise HTTPException(
            status_code=403, detail="Administrator access is required."
        )

def runtime_api_principal(request: Request) -> dict[str, Any] | None:
    expected = settings.runtime_api_token
    authorization = request.headers.get("Authorization") or ""
    if not expected or not authorization.startswith("Bearer "):
        return None
    supplied = authorization.removeprefix("Bearer ")
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Invalid runtime API token.")
    return {"id": None, "role": "admin", "runtime_api": True}

def require_runtime_reader(request: Request) -> dict[str, Any]:
    principal = runtime_api_principal(request)
    if principal:
        return principal
    user = require_user(request)
    require_runtime_admin(user)
    return user

def require_runtime_writer(request: Request) -> dict[str, Any]:
    principal = runtime_api_principal(request)
    if principal:
        return principal
    user = require_user(request)
    session = request.state.auth_session
    if not auth.verify_csrf(session, request.headers.get("X-CSRF-Token")):
        raise HTTPException(status_code=403, detail="Security token is missing or expired.")
    require_runtime_admin(user)
    return user

RuntimeReader = Annotated[dict[str, Any], Depends(require_runtime_reader)]
RuntimeWriter = Annotated[dict[str, Any], Depends(require_runtime_writer)]

@app.get("/api/runtimes")
def list_runtimes(_: RuntimeReader) -> dict:
    return {"items": runtimes.public_targets()}

@app.get("/api/runtime-jobs")
def list_runtime_jobs(
    _: RuntimeReader, limit: Annotated[int, Query(ge=1, le=200)] = 100
) -> dict:
    items = database.list_runtime_jobs(limit)
    return {
        "items": items,
        "active": sum(
            item["status"] in {"queued", "preparing", "transferring", "loading"}
            for item in items
        ),
    }

@app.get("/api/runtime-jobs/{job_id}")
def get_runtime_job(job_id: str, _: RuntimeReader) -> dict:
    job = database.get_runtime_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Runtime job not found.")
    return job

@app.post("/api/runtimes/{target_id}/load", status_code=202)
def load_runtime_model(
    target_id: str, payload: RuntimeLoadRequest, principal: RuntimeWriter
) -> dict:
    model = (
        database.get_local_model(payload.repo_id)
        if principal.get("runtime_api")
        else visible_model(payload.repo_id, principal["id"])
    )
    if not model:
        raise HTTPException(status_code=404, detail="Local model not found")
    try:
        return runtimes.queue(
            target_id,
            model,
            payload.runtime_model_name,
            payload.source_file,
            principal["id"],
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

# Per-user preferences (D15): a database row keyed by user, written only
# through the interface's own contract. Nothing here can alter deployment
# configuration — environment variables own that side of the line.

class PreferencesUpdateRequest(BaseModel):
    preferences: dict[str, Any]

@app.get("/api/preferences")
def read_preferences(user: CurrentUser) -> dict:
    return {"preferences": database.get_user_preferences(user["id"])}

@app.put("/api/preferences")
def write_preferences(payload: PreferencesUpdateRequest, user: WriteUser) -> dict:
    try:
        stored = database.set_user_preferences(user["id"], payload.preferences, utc_iso())
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"preferences": stored}

@app.get("/api/collections")
def list_collections(user: CurrentUser) -> dict:
    return {"items": database.list_collections(user["id"])}

@app.post("/api/collections", status_code=201)
def create_collection(payload: CollectionRequest, user: WriteUser) -> dict:
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Collection name is required.")
    timestamp = utc_iso()
    try:
        return database.create_collection(
            {
                "id": uuid.uuid4().hex,
                "user_id": user["id"],
                "name": name,
                "description": payload.description.strip(),
                "created_at": timestamp,
                "updated_at": timestamp,
            }
        )
    except INTEGRITY_ERRORS as error:
        raise HTTPException(
            status_code=409, detail="You already have a collection with that name."
        ) from error

@app.delete("/api/collections/{collection_id}")
def delete_collection(collection_id: str, user: WriteUser) -> dict:
    if not database.delete_collection(collection_id, user["id"]):
        raise HTTPException(status_code=404, detail="Collection not found.")
    return {"status": "deleted"}

def safe_saved_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "author",
        "pipeline_tag",
        "library_name",
        "license",
        "parameter_count",
        "last_modified",
        "local",
    }
    result = {key: value for key, value in metadata.items() if key in allowed}
    if len(json.dumps(result)) > 16_000:
        raise ValueError("Saved model metadata is too large.")
    return result

@app.get("/api/saved-models")
def list_saved_models(
    user: CurrentUser,
    query: Annotated[str, Query(max_length=200)] = "",
    collection_id: Annotated[str, Query(max_length=100)] = "",
) -> dict:
    items = database.list_saved_models(user["id"], query, collection_id)
    return {"items": items, "count": len(items)}

@app.post("/api/saved-models")
def save_model(payload: SavedModelRequest, user: WriteUser) -> dict:
    timestamp = utc_iso()
    existing = database.get_saved_model(user["id"], payload.repo_id)
    try:
        return database.save_model(
            {
                "id": existing["id"] if existing else uuid.uuid4().hex,
                "user_id": user["id"],
                "repo_id": payload.repo_id,
                "note": payload.note.strip(),
                "metadata_json": json.dumps(safe_saved_metadata(payload.metadata)),
                "created_at": existing["created_at"] if existing else timestamp,
                "updated_at": timestamp,
            },
            payload.collection_ids,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

@app.delete("/api/saved-models/{repo_id:path}")
def unsave_model(repo_id: str, user: WriteUser) -> dict:
    try:
        validated = validate_repo_id(repo_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if not database.delete_saved_model(user["id"], validated):
        raise HTTPException(status_code=404, detail="Saved model not found.")
    return {"status": "removed"}

@app.get("/api/uploads/repositories")
def list_upload_repositories(user: CurrentUser) -> dict:
    return {"items": database.list_owned_repositories(user["id"])}

@app.post("/api/uploads/repositories", status_code=201)
def create_upload_repository(payload: RepositoryRequest, user: WriteUser) -> dict:
    try:
        return uploads.create_repository(
            user, payload.slug, payload.description, payload.visibility
        )
    except (ValueError, FileExistsError, *INTEGRITY_ERRORS) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

@app.patch("/api/uploads/repositories")
def update_upload_repository(
    payload: RepositoryUpdateRequest,
    user: WriteUser,
    repo_id: Annotated[str, Query(max_length=200)],
) -> dict:
    try:
        return uploads.update_repository(
            validate_repo_id(repo_id),
            user["id"],
            payload.description,
            payload.visibility,
        )
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

@app.get("/api/uploads/repositories/files/status")
def upload_file_status(
    user: CurrentUser,
    repo_id: Annotated[str, Query(max_length=200)],
    path: Annotated[str, Query(max_length=500)],
) -> dict:
    try:
        return uploads.file_status(repo_id, user["id"], path)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

@app.put("/api/uploads/repositories/files")
async def upload_file_chunk(
    request: Request,
    user: WriteUser,
    repo_id: Annotated[str, Query(max_length=200)],
    path: Annotated[str, Query(max_length=500)],
) -> dict:
    try:
        offset = int(request.headers.get("Upload-Offset", "-1"))
        total = int(request.headers.get("Upload-Length", "-1"))
    except ValueError as error:
        raise HTTPException(status_code=400, detail="Upload headers are invalid.") from error
    try:
        content_length = int(request.headers.get("Content-Length", "0") or 0)
    except ValueError as error:
        raise HTTPException(status_code=400, detail="Content length is invalid.") from error
    if content_length > settings.upload_chunk_mb * 1024**2:
        raise HTTPException(status_code=413, detail="Upload chunk is too large.")
    chunks: list[bytes] = []
    received = 0
    limit = settings.upload_chunk_mb * 1024**2
    async for chunk in request.stream():
        received += len(chunk)
        if received > limit:
            raise HTTPException(status_code=413, detail="Upload chunk is too large.")
        chunks.append(chunk)
    payload = b"".join(chunks)
    try:
        return await run_in_threadpool(
            uploads.upload_chunk,
            repo_id,
            user["id"],
            path,
            offset,
            total,
            payload,
        )
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except FileExistsError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except (ValueError, OSError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

@app.post("/api/uploads/repositories/finalize")
async def finalize_upload_repository(
    user: WriteUser, repo_id: Annotated[str, Query(max_length=200)]
) -> dict:
    try:
        return await run_in_threadpool(uploads.finalize, repo_id, user["id"])
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

@app.delete("/api/uploads/repositories")
async def delete_upload_repository(
    payload: DeleteRepositoryRequest,
    user: WriteUser,
    repo_id: Annotated[str, Query(max_length=200)],
) -> dict:
    try:
        await run_in_threadpool(
            uploads.delete_repository, repo_id, user["id"], payload.confirmation
        )
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"status": "deleted"}

app_directory = Path(__file__).resolve().parent
static_directory = next(
    (
        candidate
        for candidate in (
            app_directory.parent / "static",
            app_directory.parent.parent / "frontend" / "dist",
        )
        if (candidate / "index.html").is_file()
    ),
    None,
)
if static_directory is not None:
    app.mount("/", StaticFiles(directory=static_directory, html=True), name="frontend")

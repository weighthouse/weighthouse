from __future__ import annotations

import hmac
import json
import os
import subprocess
import threading

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, field_validator

from .brand import BRAND
from .config import validate_repo_id
from .runtimes import validate_runtime_model_name

def _positive_int(name: str, default: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return max(1, min(value, maximum))

def _checked_extra_args(payload: tuple[str, ...]) -> tuple[str, ...]:
    reserved = {"--model", "--host", "--port", "--served-model-name"}
    if any(item.split("=", 1)[0] in reserved for item in payload):
        raise RuntimeError(
            "VLLM_AGENT_EXTRA_ARGS_JSON cannot override model, host, port, "
            "or served-model-name."
        )
    return tuple(payload)

def validated_extra_args(raw: str | None) -> tuple[str, ...]:
    """The env-driven argument policy, as a pure function for tests."""
    raw = os.getenv("VLLM_AGENT_EXTRA_ARGS_JSON", "[]") if raw is None else raw
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            f"VLLM_AGENT_EXTRA_ARGS_JSON is invalid JSON: {error.msg}."
        ) from error
    if (
        not isinstance(payload, list)
        or len(payload) > 100
        or any(not isinstance(item, str) or len(item) > 500 for item in payload)
    ):
        raise RuntimeError(
            "VLLM_AGENT_EXTRA_ARGS_JSON must be an array of at most 100 strings."
        )
    return _checked_extra_args(tuple(payload))

def _extra_args() -> tuple[str, ...]:
    return validated_extra_args(None)

@dataclass(frozen=True)
class AgentSettings:
    token: str = os.getenv("VLLM_AGENT_TOKEN", "")
    model_root: Path = Path(
        os.getenv("VLLM_AGENT_MODEL_ROOT", "/models")
    ).expanduser().resolve()
    executable: str = os.getenv("VLLM_AGENT_EXECUTABLE", "vllm").strip() or "vllm"
    # A served model answers whoever can reach it; loopback by default,
    # so sharing it with the LAN is a configuration decision, never an
    # accident.
    host: str = os.getenv("VLLM_AGENT_VLLM_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port: int = _positive_int("VLLM_AGENT_VLLM_PORT", 8000, 65535)
    startup_timeout_seconds: int = _positive_int(
        "VLLM_AGENT_STARTUP_TIMEOUT_SECONDS", 900, 7200
    )
    shutdown_timeout_seconds: int = _positive_int(
        "VLLM_AGENT_SHUTDOWN_TIMEOUT_SECONDS", 45, 300
    )
    log_path: Path = Path(
        os.getenv("VLLM_AGENT_LOG_PATH", "./vllm-agent.log")
    ).expanduser().resolve()
    extra_args: tuple[str, ...] = _extra_args()

    @property
    def api_key_configured(self) -> bool:
        return any(item.split("=", 1)[0] == "--api-key" for item in self.extra_args)

settings = AgentSettings()

class LoadModelRequest(BaseModel):
    repo_id: str
    model_path: str = Field(min_length=1, max_length=1000)
    served_model_name: str = Field(min_length=1, max_length=128)

    @field_validator("repo_id")
    @classmethod
    def repo_is_valid(cls, value: str) -> str:
        return validate_repo_id(value)

    @field_validator("served_model_name")
    @classmethod
    def name_is_valid(cls, value: str) -> str:
        return validate_runtime_model_name(value)

def require_token(
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    if not settings.token:
        raise HTTPException(
            status_code=503,
            detail="Set VLLM_AGENT_TOKEN before exposing the vLLM manager.",
        )
    prefix = "Bearer "
    supplied = (
        authorization[len(prefix) :]
        if authorization and authorization.startswith(prefix)
        else ""
    )
    if not supplied or not hmac.compare_digest(supplied, settings.token):
        raise HTTPException(status_code=401, detail="Invalid runtime agent token.")

AgentAuth = Annotated[None, Depends(require_token)]

class VllmProcessManager:
    def __init__(self, configured: AgentSettings):
        self.settings = configured
        self._lock = threading.RLock()
        self._process: subprocess.Popen[bytes] | None = None
        self._model_path: str | None = None
        self._served_model_name: str | None = None
        self._started_at: float | None = None

    def _is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "status": "ready" if self._is_running() else "idle",
                "model_path": self._model_path if self._is_running() else None,
                "served_model_name": (
                    self._served_model_name if self._is_running() else None
                ),
                "pid": self._process.pid if self._is_running() else None,
                "started_at_unix": self._started_at if self._is_running() else None,
                "vllm_url": f"http://{self.settings.host}:{self.settings.port}",
            }

    def _validated_model_path(self, value: str) -> Path:
        candidate = Path(value).expanduser().resolve()
        root = self.settings.model_root
        if root != candidate and root not in candidate.parents:
            raise ValueError(
                f"Model path must stay inside {self.settings.model_root}."
            )
        if not candidate.is_dir():
            raise ValueError("The requested model directory does not exist.")
        return candidate

    def _stop_current(self) -> None:
        if not self._is_running():
            return
        assert self._process is not None
        self._process.terminate()
        try:
            self._process.wait(timeout=self.settings.shutdown_timeout_seconds)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=15)
        self._process = None
        self._model_path = None
        self._served_model_name = None
        self._started_at = None

    def _log_tail(self) -> str:
        try:
            with self.settings.log_path.open("rb") as source:
                source.seek(0, 2)
                size = source.tell()
                source.seek(max(0, size - 4096))
                return source.read().decode("utf-8", errors="replace").strip()[-2000:]
        except OSError:
            return ""

    def _wait_until_ready(self, served_model_name: str) -> None:
        deadline = time.monotonic() + self.settings.startup_timeout_seconds
        endpoint = f"http://127.0.0.1:{self.settings.port}/v1/models"
        with httpx.Client(timeout=5.0) as client:
            while time.monotonic() < deadline:
                if not self._is_running():
                    detail = self._log_tail()
                    raise RuntimeError(
                        "vLLM exited during startup."
                        + (f" Last log output: {detail}" if detail else "")
                    )
                try:
                    response = client.get(endpoint)
                    if response.status_code == 200:
                        payload = response.json()
                        model_ids = {
                            str(item.get("id"))
                            for item in payload.get("data", [])
                            if isinstance(item, dict)
                        }
                        if served_model_name in model_ids:
                            return
                except (httpx.HTTPError, ValueError):
                    pass
                time.sleep(1)
        raise RuntimeError(
            f"vLLM did not become ready within "
            f"{self.settings.startup_timeout_seconds} seconds."
        )

    def load(self, request: LoadModelRequest) -> dict[str, Any]:
        model_path = self._validated_model_path(request.model_path)
        if not self.settings.api_key_configured:
            # The served endpoint speaks plain HTTP; loading without auth
            # hands the model and the GPU to everyone on the network.
            raise RuntimeError(
                "Refusing to serve an unauthenticated model: add "
                "--api-key <secret> to VLLM_AGENT_EXTRA_ARGS_JSON."
            )
        with self._lock:
            if (
                self._is_running()
                and self._model_path == str(model_path)
                and self._served_model_name == request.served_model_name
            ):
                return self.status()
            self._stop_current()
            self.settings.log_path.parent.mkdir(parents=True, exist_ok=True)
            command = [
                self.settings.executable,
                "serve",
                str(model_path),
                "--served-model-name",
                request.served_model_name,
                "--host",
                self.settings.host,
                "--port",
                str(self.settings.port),
                *self.settings.extra_args,
            ]
            creation_flags = (
                subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            )
            with self.settings.log_path.open("ab") as output:
                self._process = subprocess.Popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    shell=False,
                    creationflags=creation_flags,
                )
            self._model_path = str(model_path)
            self._served_model_name = request.served_model_name
            self._started_at = time.time()
            try:
                self._wait_until_ready(request.served_model_name)
            except Exception:
                self._stop_current()
                raise
            return self.status()

manager = VllmProcessManager(settings)
app = FastAPI(
    title=f"{BRAND.display_name} vLLM Runtime Agent",
    version="1.0.0",
    docs_url="/docs",
    redoc_url=None,
)

@app.get("/health")
def health(_: AgentAuth) -> dict[str, Any]:
    return {
        **manager.status(),
        "manager": f"{BRAND.slug}-vllm-agent",
        "model_root": str(settings.model_root),
    }

@app.post("/v1/models/load")
async def load_model(
    payload: LoadModelRequest, _: AgentAuth
) -> dict[str, Any]:
    try:
        return await run_in_threadpool(manager.load, payload)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except (OSError, RuntimeError) as error:
        raise HTTPException(status_code=502, detail=str(error)) from error

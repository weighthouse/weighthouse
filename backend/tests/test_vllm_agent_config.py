"""The vLLM agent refuses to serve an unauthenticated model (audit D8).

The runtime feature is dormant until targets are configured, which is
exactly why its launch policy can be strict: an operator who enables it
reads the refusal message and configures auth deliberately.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.vllm_agent import LoadModelRequest, VllmProcessManager, validated_extra_args


def _manager(tmp_path: Path, extra_args: tuple[str, ...]) -> VllmProcessManager:
    import dataclasses

    import app.config as config
    import app.vllm_agent as agent

    weights = tmp_path / "acme" / "tiny"
    weights.mkdir(parents=True)
    configured = dataclasses.replace(
        agent.settings,
        model_root=tmp_path,
        log_path=tmp_path / "agent.log",
        extra_args=extra_args,
    )
    return VllmProcessManager(configured)


def test_extra_args_without_an_api_key_still_parse():
    # Parsing stays import-time tolerant: the agent must be able to boot
    # and answer /health even before the operator finishes configuring.
    assert validated_extra_args('["--max-model-len", "4096"]') == (
        "--max-model-len",
        "4096",
    )


def test_load_refuses_to_serve_without_an_api_key(tmp_path: Path):
    manager = _manager(tmp_path, extra_args=())
    request = LoadModelRequest(
        repo_id="acme/tiny",
        model_path=str(tmp_path / "acme" / "tiny"),
        served_model_name="acme/tiny",
    )
    with pytest.raises(RuntimeError, match="--api-key"):
        manager.load(request)


def test_reserved_overrides_are_still_refused():
    with pytest.raises(RuntimeError, match="cannot override"):
        validated_extra_args('["--api-key", "sk-secret", "--host", "0.0.0.0"]')
    with pytest.raises(RuntimeError, match="cannot override"):
        validated_extra_args('["--api-key", "sk-secret", "--port=9"]')


def test_garbage_shapes_are_refused():
    with pytest.raises(RuntimeError, match="invalid JSON"):
        validated_extra_args("{not json")
    with pytest.raises(RuntimeError, match="at most 100 strings"):
        validated_extra_args('"--api-key"')
    with pytest.raises(RuntimeError, match="at most 100 strings"):
        validated_extra_args("[1, 2, 3]")


def test_the_default_launch_binds_loopback():
    import inspect

    import app.vllm_agent as agent

    source = inspect.getsource(type(agent.settings))
    assert 'VLLM_AGENT_VLLM_HOST", "127.0.0.1"' in source

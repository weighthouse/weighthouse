"""MCP server: the JSON-RPC framing and the scope-gated tool surface.

These tests exercise ``handle_mcp_message`` in isolation with a small
services bundle over a real Database — the framing rules (single
message, parse-vs-protocol errors, notifications without responses)
and the authorization ladder (per-tool scopes, destructive refusals)
stay pinned independently of any MCP client.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.database import Database
from app.mcp_server import (
    MCP_PROTOCOL_VERSION,
    McpServices,
    McpToolError,
    call_tool,
    handle_mcp_message,
    tool_definitions,
)


@pytest.fixture()
def database(tmp_path: Path) -> Database:
    database = Database(tmp_path / "library.sqlite3")
    database.initialize()
    database.create_user(
        {
            "id": "owner-1",
            "username": "keeper",
            "display_name": "The Keeper",
            "password_hash": "x",
            "role": "admin",
            "created_at": "2026-09-26T00:00:00+00:00",
            "updated_at": "2026-09-26T00:00:00+00:00",
        }
    )
    database.upsert_local_model(
        {
            "repo_id": "acme/echo",
            "relative_path": "acme/echo",
            "size_bytes": 42,
            "file_count": 2,
            "modified_at": "2026-09-26T01:00:00+00:00",
            "downloaded_at": None,
            "revision": "main",
            "sha": "sha-x",
            "pipeline_tag": "text-generation",
            "library_name": "transformers",
            "license": "mit",
            "tags_json": "[]",
            "config_json": "{}",
            "source_url": "https://huggingface.co/acme/echo",
            "managed": True,
            "storage_backend": "filesystem",
            "cached": True,
            "remote_uri": None,
        }
    )
    return database


def _services(database: Database) -> McpServices:
    stub = SimpleNamespace()
    stub.search_models = lambda *args: []
    stub.model_details = lambda *args: {}
    stub._plan = lambda *args: (None, {})
    stub.queue = lambda *a, **kw: {}
    stub.files_for_model = lambda repo_id: {"model": repo_id, "files": []}
    stub.list_repository_files = lambda repo_id: {"model": repo_id, "files": []}
    stub.sync_repository = lambda *args: None
    stub.health = lambda: {}
    location = SimpleNamespace(name="primary", root=Path("/models"))
    return McpServices(
        database=database,
        hub=stub,
        downloads=stub,
        indexer=stub,
        model_storage=stub,
        settings=SimpleNamespace(model_storage=Path("/models")),
        tracked_update_diff=lambda model, root: {"upstream": {"sha": "abc"}, "files": []},
        locations=[location],
    )


ALL_SCOPES = {"search", "read", "queue", "mutate"}


def test_initialize_handshake_versions_the_server(database: Database):
    status, response = handle_mcp_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "clientInfo": {"name": "t"}},
        },
        {"id": "owner-1"},
        ALL_SCOPES,
        _services(database),
    )
    assert status == 200
    result = response["result"]
    assert result["protocolVersion"] == MCP_PROTOCOL_VERSION
    assert "tools" in result["capabilities"]
    assert result["serverInfo"]["name"]


def test_tools_list_hides_internals_and_matches_handlers(database: Database):
    status, response = handle_mcp_message(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"id": "owner-1"},
        ALL_SCOPES,
        _services(database),
    )
    tools = response["result"]["tools"]
    names = [tool["name"] for tool in tools]
    assert "search_models" in names and "delete_copy" in names
    assert all("requiredScope" not in tool for tool in tools)
    assert all(tool["inputSchema"]["type"] == "object" for tool in tools)


def test_notifications_answer_202_and_batches_reject(database: Database):
    services = _services(database)
    user = {"id": "owner-1"}
    status, response = handle_mcp_message(
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        user,
        ALL_SCOPES,
        services,
    )
    assert (status, response) == (202, None)
    status, response = handle_mcp_message(
        [{"jsonrpc": "2.0", "id": 1, "method": "ping"}],
        user,
        ALL_SCOPES,
        services,
    )
    assert status == 400
    assert response["error"]["code"] == -32600
    status, response = handle_mcp_message(
        {"jsonrpc": "1.0", "id": 1, "method": "ping"},
        user,
        ALL_SCOPES,
        services,
    )
    assert response["error"]["code"] == -32600


def test_unknown_methods_and_tools_frame_errors(database: Database):
    services = _services(database)
    user = {"id": "owner-1"}
    status, response = handle_mcp_message(
        {"jsonrpc": "2.0", "id": 3, "method": "prompts/list"},
        user,
        ALL_SCOPES,
        services,
    )
    assert status == 400
    assert response["error"]["code"] == -32601

    status, response = handle_mcp_message(
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "nope"}},
        user,
        ALL_SCOPES,
        services,
    )
    assert status == 400
    assert response["error"]["code"] == -32602

    status, response = handle_mcp_message(
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "library_list"}},
        user,
        ALL_SCOPES,
        services,
    )
    assert status == 200
    result = response["result"]
    assert result["structuredContent"]["count"] == 1
    assert result["structuredContent"]["items"][0]["repo_id"] == "acme/echo"
    assert result["content"][0]["type"] == "text"


def test_scope_denial_is_an_honest_tool_result(database: Database):
    result = call_tool(
        "library_list", {}, {"id": "owner-1"}, {"search"}, _services(database)
    )
    assert result["isError"] is True
    assert "'read'" in result["content"][0]["text"]


def test_delete_copy_refuses_without_explicit_confirmation(database: Database):
    result = call_tool(
        "delete_copy",
        {"repo_id": "acme/echo"},
        {"id": "owner-1"},
        ALL_SCOPES,
        _services(database),
    )
    assert result["isError"] is True
    assert "confirmation=repo_id" in result["content"][0]["text"]


def test_delete_copy_refuses_a_wrong_confirmation_string(database: Database):
    result = call_tool(
        "delete_copy",
        {"repo_id": "acme/echo", "confirmation": "acme/other"},
        {"id": "owner-1"},
        ALL_SCOPES,
        _services(database),
    )
    assert result["isError"] is True
    assert "confirmation=repo_id" in result["content"][0]["text"]


def test_storage_locations_reports_the_primary(database: Database):
    result = call_tool(
        "storage_locations", {}, {"id": "owner-1"}, {"read"}, _services(database)
    )
    payload = result["structuredContent"]
    assert payload["primary"] == "primary"
    assert payload["count"] == 1


def test_model_files_reads_the_visible_copy(database: Database):
    result = call_tool(
        "model_files",
        {"repo_id": "acme/echo"},
        {"id": "owner-1"},
        {"read"},
        _services(database),
    )
    assert result["structuredContent"]["model"] == "acme/echo"


def test_unknown_repo_is_a_tool_error_not_a_crash(database: Database):
    result = call_tool(
        "model_files",
        {"repo_id": "acme/ghost"},
        {"id": "owner-1"},
        {"read"},
        _services(database),
    )
    assert result["isError"] is True
    assert "not found" in result["content"][0]["text"]


def test_every_tool_declares_a_real_scope():
    known = {"search", "read", "queue", "mutate"}
    for tool in tool_definitions():
        assert tool["requiredScope"] in known

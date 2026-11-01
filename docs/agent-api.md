# Agents

Weighthouse exposes an MCP endpoint and a set of API keys so that local agents can search the Hub, watch the library, and manage model files.

Both live in **Settings → Agent API keys**. Only administrators can create keys.

## Creating a key

Give the key a name and the scopes it needs, then `Create agent key`. The token is shown once — `ak_…` — and only its last four characters are kept. Make sure you copy this key to a safe location before dismissing it.

| Scope | Grants |
|---|---|
| `search` | Discover repositories on the Hub. |
| `read` | Read the library: copies, files, update diffs, verify reports, locations, download state. |
| `queue` | Ask the library to fetch: downloads, updates, restores. |
| `mutate` | Change stored content: deletions, moves, cache eviction, storage scans. |

Revoke a key to stop it immediately; the revoked entry stays on record until a permanent delete removes both the key and its request history.

Keys are minted by administrators and carry the creating account's identity, so requests made through a key act as that account. A maximum of 64 keys can be active at any time, per installation; revoking or deleting a key frees its slot.

## MCP endpoint

The endpoint is `POST /api/mcp` — Streamable HTTP, one JSON-RPC 2.0 message per request, authenticated as the key:

```bash
curl -X POST http://weighthouse-host:7860/api/mcp \
  -H "Authorization: Bearer ak_your_token" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

Tools available to a key follow its scopes: `search_models`, `model_details`, `library_list`, `model_files`, `check_updates`, `verify_copy`, `storage_locations`, `plan_download`, `queue_download`, `download_status`, `queue_update`, `move_copy`, `delete_files`, `delete_copy`, `rescan_storage`. A tool outside the key's scopes answers with a per-tool denial naming the missing scope.

Deleting a copy through MCP follows the same rule as the HTTP API: pass `confirmation` equal to the exact `repo_id`.

Most MCP clients take an endpoint URL plus a header. Point the client at `http://weighthouse-host:7860/api/mcp` with the `Authorization: Bearer …` header and an appropriate instructive prompt, and it can discover and use the tools itself through `tools/list` and `tools/call`.

Requests through a key are recorded in the key's history: method, path, and status.

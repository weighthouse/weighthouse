#!/usr/bin/env bash
# Disposable local ModelHarbor instance for visual verification.
# Boots on :8321 against a throwaway data dir and seeded model storage,
# creates the owner account, and indexes the seeded repositories.
set -euo pipefail

cd "$(dirname "$0")/.."

export MODELHARBOR_DEV=1
export PORT="${PORT:-8321}"
export DATA_DIR="$(pwd)/.devdata"
export MODEL_STORAGE="$(pwd)/.devmodels"
export ALLOW_INSECURE_COOKIES=1
export ACCOUNTS_ENABLED=1

mkdir -p "$DATA_DIR" "$MODEL_STORAGE"

# --- Seed repositories (zero-byte files; only manifest metadata is exercised) ---
now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

mk() { # mk <dir> <repo_id> <size_gb> <extra-files...>
  local dir="$1" repo="$2" gb="$3"; shift 3
  mkdir -p "$dir"
  # manifest
  cat > "$dir/.modelmanifest.json" <<EOF
{
  "schema_version": 2,
  "status": "complete",
  "repo_id": "$repo",
  "revision": "9f3a2c1",
  "sha": "4be07aa1c2d3e4f5",
  "pipeline_tag": "${PTAG:-text}",
  "library_name": "${LIB:-transformers}",
  "license": "apache-2.0",
  "tags": ${TAGS:-[]},
  "downloaded_at": "$now",
  "source_url": "https://huggingface.co/$repo",
  "storage_backend": "filesystem",
  "location": "default"
}
EOF
  # config.json (gives the indexer architectures)
  cat > "$dir/config.json" <<EOF
{
  "architectures": ${ARCH:-"["\"LlamaForCausalModel"\"]"},
  "model_type": "${MTYPE:-${PTAG:-text}}",
  "torch_dtype": "bfloat16",
  "vocab_size": 128256
}
EOF
  # README (card document for the model page)
  cat > "$dir/README.md" <<EOF
# $repo

A seeded repository used for UI development and screenshots.

## Runtimes

| Runtime | Minimum | Notes |
|---|---|---|
| vLLM | 0.11.0 | default |

> Seeded content: this card is a stand-in for third-party prose.
EOF
  # a couple of weight-ish files so the file table has rows
  dd if=/dev/zero of="$dir/model-00001-of-00002.safetensors" bs=1 count=0 2>/dev/null
  dd if=/dev/zero of="$dir/model-00002-of-00002.safetensors" bs=1 count=0 2>/dev/null
  for extra in "$@"; do
    dd if=/dev/zero of="$dir/$extra" bs=1 count=0 2>/dev/null
  done
}

rm -rf "$MODEL_STORAGE"/* 2>/dev/null || true
export PTAG=text LIB=transformers ARCH='["LlamaForCausalModel"]' MTYPE=llama TAGS='["llama","instruct"]'
mk "$MODEL_STORAGE/unsloth/Llama-3.3-70B-Instruct-GGUF" "unsloth/Llama-3.3-70B-Instruct-GGUF" 87 \
   "Llama-3.3-70B-Instruct-Q4_K_M.gguf" "Llama-3.3-70B-Instruct-Q5_K_M.gguf"
export PTAG=image-to-text LIB=diffusers ARCH='["Qwen2ForCausalModel"]' MTYPE=qwen2
TAGS='["qwen","gguf"]'
mk "$MODEL_STORAGE/abenzersps/Qwen-Image-2.1-Uncensored-GGUF" "abenzersps/Qwen-Image-2.1-Uncensored-GGUF" 62 \
   "Qwen-Image-2.1-Uncensored-Q4_K_M.gguf" "Qwen-Image-2.1-Uncensored-Q5_K_M.gguf"
export PTAG=text LIB=transformers ARCH='["Qwen3MoeForCausalLM"]' MTYPE=qwen3 TAGS='["qwen","moe"]'
mk "$MODEL_STORAGE/unsloth/Qwen3-30B-A3B-Instruct-2507-GGUF" "unsloth/Qwen3-30B-A3B-Instruct-2507-GGUF" 19 \
   "Qwen3-30B-A3B-Instruct-2507-UD-Q4_K_XL.gguf"
export PTAG=text LIB=transformers ARCH='["LlamaForCausalModel"]' MTYPE=llama TAGS='["llama","gguf"]'
mk "$MODEL_STORAGE/bartowski/Meta-Llama-3.1-8B-Instruct-GGUF" "bartowski/Meta-Llama-3.1-8B-Instruct-GGUF" 5 \
   "Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf"

# --- Boot + seed ---
URL="http://127.0.0.1:$PORT"

nohup .venv/bin/python -m uvicorn backend.app.main:app --port "$PORT" --host 127.0.0.1 > "$DATA_DIR/uvicorn.log" 2>&1 &
SERVER_PID=$!
disown "$SERVER_PID" 2>/dev/null || true

for _ in $(seq 1 60); do
  if curl -sf "$URL/api/health" > /dev/null 2>&1; then break; fi
  sleep 0.5
done
if ! curl -sf "$URL/api/health" > /dev/null 2>&1; then
  echo "server did not come up; log tail:"; tail -20 "$DATA_DIR/uvicorn.log"; exit 1
fi

# owner account (no-op if it already exists)
AUTH_JSON="$(curl -sf -c "$DATA_DIR/cookies" -X POST "$URL/api/auth/setup" \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","display_name":"Admin","password":"dev-password-42"}' 2>/dev/null \
  || curl -sf -c "$DATA_DIR/cookies" -X POST "$URL/api/auth/login" \
     -H 'Content-Type: application/json' \
     -d '{"username":"admin","password":"dev-password-42"}')"
CSRF="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1]).get("csrf_token",""))' "$AUTH_JSON")"

# index the seeded repositories
curl -sf -b "$DATA_DIR/cookies" -X POST "$URL/api/local-models/scan" \
  -H "X-CSRF-Token: $CSRF" > "$DATA_DIR/scan.json"

echo "READY http://127.0.0.1:$PORT  (pid $SERVER_PID, cookie jar $DATA_DIR/cookies)"
echo "server keeps running; kill with: kill $SERVER_PID"

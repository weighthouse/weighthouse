# Model storage

## The model folder

`MODEL_STORAGE_PATH` in `.env` is the host directory that holds model files. The container sees it as `/models`. Managed repositories live in a plain hierarchy:

```text
models/
  organization/
    repository/
      .modelmanifest.json
      config.json
      model.safetensors
      ...
```

That layout is portable — any tool that accepts a local Hugging Face repository path can read it.

## Adding models by hand

Copy a model folder anywhere within the first few directory levels of the model folder, then choose **Local library → Scan folder**. The scan recognizes common configs (`config.json`, `model_index.json`, `tokenizer.json`) and weight extensions (`.safetensors`, `.gguf`, `.onnx`, `.bin`, `.pt`, `.pth`, `.ckpt`). Manually copied models are indexed but never modified.

## Named locations

Additional host directories can be declared through `LOCATIONS_JSON` in `.env`:

```dotenv
LOCATIONS_JSON=[{"name":"archive","root":"/mnt/large-models"}]
```

Names are lowercase, 40 characters maximum; `primary` is reserved for `MODEL_STORAGE_PATH` itself. Copies can be moved between declared locations from the model page.

## S3-compatible storage

`MODEL_STORAGE_BACKEND=s3` keeps complete managed repositories in AWS S3, MinIO, Ceph, Cloudflare R2, or any compatible service. `/models` remains a working cache, because serving model weights locally needs filesystem paths.

```dotenv
MODEL_STORAGE_BACKEND=s3
MODEL_STORAGE_PATH=./models
S3_BUCKET=my-model-bucket
S3_PREFIX=models
S3_REGION=us-east-1
AWS_ACCESS_KEY_ID=replace-me
AWS_SECRET_ACCESS_KEY=replace-me
```

A custom endpoint:

```dotenv
S3_ENDPOINT_URL=http://minio:9000
S3_ADDRESSING_STYLE=path
S3_USE_SSL=false
```

Static keys are optional on AWS — boto3's credential chain, including attached IAM roles, works. The bucket identity needs `s3:ListBucket` on the bucket and `s3:GetObject`, `s3:PutObject`, `s3:DeleteObject` on the prefix.

Downloads and finalized uploads sync automatically. The manifest is written last, so a partially transferred repository is not indexed as complete. From the model page a cache copy can be evicted while the durable S3 copy stays, and restored when a tool needs the files again.

## Moving a deployment to another host

Copy the deployment directory (compose file, `.env`, model folder, `data` folder) and adjust `MODEL_STORAGE_PATH`. Synology Container Manager does not always create missing bind-mount sources, so create both directories before starting:

```bash
mkdir -p /volume1/docker/modelharbor/models
mkdir -p /volume1/docker/modelharbor/data
```

Common paths:

```dotenv
# Synology
MODEL_STORAGE_PATH=/volume1/AI/models

# TrueNAS
MODEL_STORAGE_PATH=/mnt/tank/ai/models

# QNAP
MODEL_STORAGE_PATH=/share/Container/models
```

If the NAS enforces Unix ownership, set the user and group IDs in `.env` (`PUID`, `PGID` — `id your-nas-user` shows them over SSH), then `docker compose up -d`.

## What to back up

- Model files: `MODEL_STORAGE_PATH` (and the S3 bucket when enabled).
- Accounts, sessions, saved models, collections, repository ownership, upload manifests, download history, and the local index: the `data` directory — `data/modelharbor.sqlite3` by default, or the database named by `DATABASE_URL`.
- Hub metadata cache: `data/hub-cache`.

Back up the model folder and the database together. The model index can be rebuilt from the files, but the database is the only copy of accounts and ownership. Its bytes include password and session hashes, so store backups where those are safe.

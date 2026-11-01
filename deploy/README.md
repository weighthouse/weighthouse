# Deployment

Every deployment keeps its state in two host directories: one for model files, one for the `/data` state directory (SQLite database, download cache, uploads).

## Route 1 — Compose directory (servers)

A production directory needs exactly two files: the compose file and the
environment template. No repository checkout.

```sh
mkdir weighthouse && cd weighthouse
# Substitute your instance's git host in both URLs:
curl -O https://GIT-HOST/Weighthouse/Weighthouse/raw/branch/main/docker-compose.yml
curl -o .env https://GIT-HOST/Weighthouse/Weighthouse/raw/branch/main/.env.example
```

Edit `.env`, starting with `MODEL_STORAGE_PATH` (the host directory that
holds model files), then:

```sh
docker compose up -d
```

The compose file pulls the published `main` image. With a source checkout in
the same directory you can rebuild it in place instead: `docker compose build`.

## Route 2 — Windows desktop helpers

`deploy/desktop/` holds two batch files for running Weighthouse on a
workstation with Docker Desktop. They wrap plain `docker compose` commands:
start (which creates `.env` from the template on first run, builds, and opens
the browser) and stop. Copy the whole repository directory, or use Route 1
plus these helpers; they work from any directory containing
`docker-compose.yml`.

## Route 3 — Source checkout

Clone the repository, then either build outright:

```sh
docker compose up --build -d
```

or run the backend and frontend directly — see
[docs/development.md](../docs/development.md).

## Notes

- Pulling the image from a plain-HTTP registry (for example an internal
  Forgejo) requires the host daemon to trust it:
  `{"insecure-registries": ["registry-host:port"]}` in `/etc/docker/daemon.json`,
  then restart Docker.
- State lives in the two host directories. Back them up; upgrades or container replacements don't touch.
- `MODEL_STORAGE_PATH` is bind-mounted read-write at `/models` in the
  container. Every model Weighthouse indexes lives there.

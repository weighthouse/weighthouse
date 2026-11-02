<p align="center">
  <img src="frontend/public/brand-mark.svg" width="92" alt="Weighthouse">
</p>

<h1 align="center">Weighthouse</h1>

<p align="center">
  <b>A self-hosted model library.</b><br>
  Download Hugging Face models onto storage you control. Pin revisions, verify local copies, track upstream changes, and move models between storage locations. Use the web interface yourself, or give AI agents scoped access through the built-in MCP server and API.
</p>

![Weighthouse Library filtered to speech models, showing local sizes, file counts, and available updates](docs/images/library-speech-models.png)

<table>
  <tr>
    <td width="50%" valign="top"><strong>Managed downloads</strong><br>Full repositories or selected files, with retries, restart recovery, and optional Xet.</td>
    <td width="50%" valign="top"><strong>Agent operations</strong><br>Built-in MCP and API, with scoped keys for model discovery, downloads, verification, and moves.</td>
  </tr>
  <tr>
    <td valign="top"><strong>Revision tracking</strong><br>Pin revisions, verify local copies, and review upstream file changes before updating.</td>
    <td valign="top"><strong>Storage locations</strong><br>Move managed models between named filesystem locations; optional S3-compatible backend.</td>
  </tr>
  <tr>
    <td valign="top"><strong>Model discovery</strong><br>Search and filter Hugging Face repositories; inspect GGUF metadata without downloading weights.</td>
    <td valign="top"><strong>Library organization</strong><br> models already on disk; save Hub models, organize collections, and keep per-model notes.</td>
  </tr>
  <tr>
    <td valign="top"><strong>Multi-user</strong><br>A shared managed library with personal collections, shared notes, and private uploads.</td>
    <td valign="top"><strong>One container</strong><br> Docker Compose deployment, with SQLite by default and optional PostgreSQL.</td>
  </tr>
</table>


## Run it

Weighthouse ships as a container. You need Docker and two host directories: one for model files, one for application state. The defaults are `./models` and `./data`.

```bash
mkdir weighthouse && cd weighthouse
curl -fLO https://raw.githubusercontent.com/weighthouse/weighthouse/main/docker-compose.yml
curl -fL -o .env https://raw.githubusercontent.com/weighthouse/weighthouse/main/.env.example
```

Edit `.env` — `MODEL_STORAGE_PATH` is the host directory that holds your models — then:

```bash
docker compose up -d
```

Open `http://localhost:7860`, or use your server's address if running remotely. On your first visit, you'll be prompted to create the administrator account. Additional accounts can be added from Settings.

Session cookies are marked Secure by default. For HTTP-only deployments on a trusted network, set `ALLOW_INSECURE_COOKIES=true` in `.env`. Use HTTPS for deployments accessible outside your trusted network.

Windows users can run Weighthouse with Docker Desktop using the helpers in [deploy/desktop](deploy/desktop).

## Upgrading from ModelHarbor

All you need to do is pull the new Docker image. If you use the default SQLite database, then Weighthouse automatically copies your library database (modelharbor.sqlite3) to library.sqlite3 (in the same data directory). The original modelharbor.sqlite3 stays as a backup; feel free to delete it whenever you're ready. If you use Postgres, no changes are required.

## Documentation

| | |
|---|---|
| [Deployment](deploy/README.md) | How to deploy Weighthouse using Docker Compose, Docker Desktop, or compiling from source |
| [Storage](docs/storage.md) | The model folder, hand-added models (uploads), named locations, S3 storage, moving a host, backups |
| [Downloads](docs/downloads.md) | Download modes, gated models, watchlist and updates, GGUF inspection |
| [Accounts](docs/accounts.md) | Members, saved models and notes, local uploads |
| [PostgreSQL](docs/postgres.md) | Running the metadata database on PostgreSQL |
| [Agent API](docs/agent-api.md) | API keys and the MCP endpoint for local agents |
| [Development](docs/development.md) | Running from a source checkout, tests |

## What's new in 2.0.1

- Clearer download errors, with compact failed rows, expandable details, and copyable diagnostics.
- Predictable download ordering: restarted jobs join the queue at the end, and their latest results appear at the top of History.
- State sorting in Explore and Library, plus sortable columns in Watched.
- Fixed model moves between filesystems placing files inside an extra directory.

See the [full changelog](CHANGELOG.md) for details and earlier releases.

## License

Weighthouse is released under the MIT License (see `LICENSE`). It is based on the open-source [HuggingHack](https://github.com/tyedalwaves/HuggingHack) project.

## AI assistance

toks.love developed Weighthouse with AI assistance. toks.love owns the code, as well as any mistakes or omissions.
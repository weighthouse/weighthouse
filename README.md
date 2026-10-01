<p align="center">
  <img src="frontend/public/brand-mark.svg" width="92" alt="ModelHarbor">
</p>

<h1 align="center">ModelHarbor</h1>

<p align="center">
  A model library on your own storage.<br>
  Find models on the Hugging Face Hub, keep exactly the files you want, and read them from a plain folder.
</p>

> ModelHarbor is self-hosted software for local model weights. It is not affiliated with or endorsed by Hugging Face.

![ModelHarbor model catalog with live model cards, filters, search, and download actions](docs/images/models-catalog.png)

<table>
  <tr>
    <td width="50%" valign="top"><strong>Multi-user</strong><br>Roles, per-account libraries, notes, and preferences.</td>
    <td width="50%" valign="top"><strong>Shared notes</strong><br>Model notes travel with the team, pinned or latest.</td>
  </tr>
  <tr>
    <td valign="top"><strong>Private uploads</strong><br>Your own weights, private by default, shareable within the install.</td>
    <td valign="top"><strong>Storage locations</strong><br>Move copies between cold, warm, and hot tiers on demand.</td>
  </tr>
  <tr>
    <td valign="top"><strong>Revision pinning</strong><br>The copy is the contract. Drift is reported, never applied.</td>
    <td valign="top"><strong>Agentic control</strong><br>Scoped keys and a stable API for research and ops.</td>
  </tr>
  <tr>
    <td colspan="2" valign="top"><strong>One container</strong><br>The whole service is a container and a volume. Nothing else to operate.</td>
  </tr>
</table>

## Run it

ModelHarbor ships as a container. Running it needs Docker and two host directories: one for model files, one for application state.

```bash
mkdir modelharbor && cd modelharbor
curl -O https://raw.githubusercontent.com/toks-love/modelharbor/main/docker-compose.yml
curl -o .env https://raw.githubusercontent.com/toks-love/modelharbor/main/.env.example
```

Edit `.env` — `MODEL_STORAGE_PATH` is the host directory that holds models — then:

```bash
docker compose up -d
```

Open port 7860. The first visit creates the owner account, which adds member accounts from Settings. `docker compose down` stops the service; model files and the database are preserved.

Session cookies are marked Secure by default, so sign-ins persist once HTTPS terminates in front of the app. On a plain-HTTP LAN, the template documents the escape hatch.

Windows users can run it with Docker Desktop through the helpers in [deploy/desktop](deploy); the same document covers the compose route and a source checkout in full.

## Documentation

| | |
|---|---|
| [Deployment](deploy/README.md) | Compose directory on a server, Windows desktop helpers, source checkout |
| [Storage](docs/storage.md) | The model folder, hand-added models, named locations, S3, moving a host, backups |
| [Downloads](docs/downloads.md) | Download modes, gated models, watch and updates, GGUF inspection |
| [Accounts](docs/accounts.md) | Members, saved models and notes, local uploads |
| [PostgreSQL](docs/postgres.md) | Running the metadata database on PostgreSQL |
| [Agent API](docs/agent-api.md) | API keys and the MCP endpoint for local agents |
| [Development](docs/development.md) | Running from a source checkout, tests |

## License

ModelHarbor is released under the MIT License (see `LICENSE`). It is based on the open-source
[HuggingHack](https://github.com/tyedalwaves/HuggingHack) project; attribution is preserved
there and in `THIRD-PARTY.md`.

## AI assistance

toks.love developed ModelHarbor with AI assistance: an agent wrote most of the code under
the careful direction and review of toks.love. toks.love owns the code, as well as any
mistakes or omissions; the MIT License above applies in full.

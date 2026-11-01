# Use PostgreSQL

SQLite is the zero-configuration default and is right for a single server. Point `DATABASE_URL` at a PostgreSQL server for a multi-user deployment or an externally managed database:

```dotenv
DATABASE_URL=postgresql://weighthouse:password@database-host:5432/weighthouse
```

Tables are created and upgraded at startup. Switching `DATABASE_URL` does not migrate an existing SQLite installation — copy what matters before switching.

An optional overlay runs PostgreSQL 17 beside Weighthouse. Add a long URL-safe password to `.env`:

```dotenv
POSTGRES_PASSWORD=replace-with-a-long-random-password
```

Then start both services:

```sh
docker compose -f docker-compose.yml -f docker-compose.postgres.yml up -d
```

The overlay stores PostgreSQL data in the `weighthouse-postgres-data` volume and waits for the database health check before starting the application container. Back that volume up separately from `data`.

## Coming from 1.3.x on the overlay

The compose project has a new name, and 1.3.x created its data volume under the old one (`modelharbor_postgres-data`). Copy that volume into the new explicitly named one, then start the renamed project:

```sh
docker compose -p modelharbor down
docker volume create weighthouse-postgres-data
docker run --rm \
  -v modelharbor_postgres-data:/from \
  -v weighthouse-postgres-data:/dst \
  alpine sh -c 'cp -a /from/. /dst/'
docker compose -f docker-compose.yml -f docker-compose.postgres.yml up -d
```

`docker volume ls | grep postgres` shows the exact old volume name if your setup differs. Anything already on a plain `DATABASE_URL` is unaffected.

Credentials stay in `.env` on the server and are never returned by the API.

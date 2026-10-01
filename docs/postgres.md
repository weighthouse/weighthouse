# Use PostgreSQL

SQLite is the zero-configuration default and is right for a single server. Point `DATABASE_URL` at a PostgreSQL server for a multi-user deployment or an externally managed database:

```dotenv
DATABASE_URL=postgresql://modelharbor:password@database-host:5432/modelharbor
```

Tables are created and upgraded at startup. Switching `DATABASE_URL` does not migrate an existing SQLite installation — copy what matters before switching.

An optional overlay runs PostgreSQL 17 beside ModelHarbor. Add a long URL-safe password to `.env`:

```dotenv
POSTGRES_PASSWORD=replace-with-a-long-random-password
```

Then start both services:

```sh
docker compose -f docker-compose.yml -f docker-compose.postgres.yml up -d
```

The overlay stores PostgreSQL data in the `postgres-data` volume and waits for the database health check before starting the application container. Back that volume up separately from `data`.

Credentials stay in `.env` on the server and are never returned by the API.

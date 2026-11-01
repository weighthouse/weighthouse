# Security policy

## Supported versions

| Version | Supported |
|---|---|
| 2.0.x | yes |
| < 2.0 | no — upgrade first |

## Reporting a vulnerability

Please do not open a public issue for something exploitable.

Email **security@weighthouse.ai**, or use GitHub's private vulnerability
reporting on this repository. Include what you found, how to reproduce it,
and what you think the impact is. You'll hear back within 72 hours with
either a fix plan or questions.

## Scope

In scope: the container image and its API surface (web UI, REST API, agent
keys, MCP endpoint), auth/session handling, the download pipeline, and
anything that would let one account see or modify another's data.

Out of scope: reports about self-hosted misconfiguration outside the
container (a wide-open host, unpatched OS), social engineering, and
automated scanner output with no demonstrated path.

## What to expect

Fixes in the next patch release and credit in the changelog (unless you
prefer otherwise).

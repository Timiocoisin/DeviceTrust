# Configuration

Run `python scripts/init_env.py` once, then edit the private `.env`. Existing files are never overwritten. The application and client load this file from the project root; process environment variables take precedence.

## Database and process

| Variable | Meaning |
| --- | --- |
| `MYSQL_HOST`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DB` | MySQL connection settings. No database credentials are provided. |
| `FLASK_HOST` | Listening interface; the example binds to loopback. |
| `FLASK_PORT` | Web entry point, default `7070`. |
| `AUTHORIZE_PORT` | Second entry point, default `8080`; serves the same Flask application. |
| `FLASK_DEBUG` | Debug flag. Keep disabled outside local development. |
| `AUTHORIZE_HOST` | Client destination, default `127.0.0.1`. |
| `PUBLIC_HOST` | Optional address displayed in startup output. Does not change interface binding or configure TLS. |
| `AUTH_CODE_BIN` | Client-side authorization-code path, default `auth_code.bin`. |

The default launcher initializes the MySQL schema. An account that can only perform ordinary row writes will not be sufficient for schema initialization or migration. Back up an existing database before deploying schema changes.

When listening on all interfaces and no public display address is configured, the launcher may attempt cloud metadata and public IP-echo endpoints to discover an address to print. Set `PUBLIC_HOST` explicitly if these discovery requests are unwanted.

## Private keys

`SECRET_KEY` protects Flask sessions. `AES_KEY` must be 16, 24 or 32 UTF-8 bytes; `DES_KEY` and `DES_IV` must each be exactly 8 UTF-8 bytes. Length is measured in bytes, not characters.

The initializer generates keys and writes them only into `.env`. There are no fallback protocol keys in the source. Missing/invalid AES/DES settings fail at import time with a variable-name/length error; values are not echoed.

Provision matching values to the server and client over a trusted channel. Rotating keys affects existing device clients and authorization codes. A copied `.env` is a secret, not a deployment artifact to commit.

## Policy

| Variable | Default | Behavior |
| --- | --- | --- |
| `DAILY_AUTH_LIMIT_PER_DEVICE` | `50` | Successful authorizations per physical fingerprint per day; zero disables the limit. |
| `RATE_LIMIT_WINDOW_SECONDS` | `60` | IP/CPU rate-limit window. |
| `RATE_LIMIT_MAX_PER_IP` | `30` | IP request threshold. |
| `RATE_LIMIT_MAX_PER_CPU` | `20` | CPU request threshold. |
| `AUTH_CODE_MAX_AGE_SECONDS` | `86400` | Code age limit; zero skips expiration validation. |
| `LOG_RETENTION_DAYS` | `90` | Authorization-log retention configuration. |
| `STALE_DAYS` | `7` | Inactivity threshold for device classification. |

These are operator settings. The console's policy dialog is read-only. Apply configuration changes by restarting the service; automatic template reloading does not reload policy or protocol keys.

## Deployment considerations

Keep console access on a trusted network until account registration/reset and authorization controls are hardened. Add TLS and appropriate Flask session settings before external exposure. Review the use of forwarded IP headers behind your reverse proxy, because rate limiting depends on client identity.

The built-in launcher uses Werkzeug, two listening ports, one in-process event hub, and in-memory rate-limit state. Do not assume a generic multi-worker WSGI command will preserve cross-port event delivery or global limits. A production topology requires a WebSocket-compatible server and shared state/event infrastructure as appropriate.

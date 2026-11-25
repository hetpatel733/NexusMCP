# Environment Variables Reference

NexusMCP configuration is driven by environment variables using `pydantic-settings`. Default values are configured for local Docker development.

| Variable                      | Description                                             | Default                                                          | Required in Production |
| ----------------------------- | ------------------------------------------------------- | ---------------------------------------------------------------- | ---------------------- |
| `ENV`                         | Environment mode (`development`, `production`, `test`)  | `development`                                                    | Yes                    |
| `LOG_LEVEL`                   | Logging verbosity (`debug`, `info`, `warning`, `error`) | `info`                                                           | No                     |
| `HOST`                        | Backend host address binding                            | `0.0.0.0`                                                        | No                     |
| `PORT`                        | Backend port binding                                    | `8000`                                                           | No                     |
| `MONGO_EXTERNAL_PORT`         | Host port exposed for MongoDB container                 | `27018`                                                          | No                     |
| `MONGO_URI`                   | MongoDB connection URI with authentication database     | `mongodb://root:example@mongodb:27017/nexusmcp?authSource=admin` | Yes                    |
| `MONGO_DB_NAME`               | Name of the application database                        | `nexusmcp`                                                       | Yes                    |
| `TEMPORAL_HOST`               | Temporal server gRPC endpoint (`host:port`)             | `temporal:7233`                                                  | Yes                    |
| `TEMPORAL_NAMESPACE`          | Temporal namespace for workflow routing                 | `default`                                                        | Yes                    |
| `POSTGRES_USER`               | PostgreSQL username for Temporal persistence            | `temporal`                                                       | Yes                    |
| `POSTGRES_PASSWORD`           | PostgreSQL password for Temporal persistence            | `temporal`                                                       | Yes                    |
| `POSTGRES_DB`                 | PostgreSQL database for Temporal persistence            | `temporal`                                                       | Yes                    |
| `POSTGRES_HOST`               | PostgreSQL host for Temporal container                  | `postgres`                                                       | Yes                    |
| `POSTGRES_PORT`               | PostgreSQL port                                         | `5432`                                                           | No                     |
| `JWT_SECRET_KEY`              | Secret key used to sign and verify JWT tokens           | `nexusmcp-dev-secret-key...`                                     | Yes                    |
| `JWT_ALGORITHM`               | JWT signing algorithm                                   | `HS256`                                                          | No                     |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Access token lifespan in minutes                        | `30`                                                             | No                     |
| `REFRESH_TOKEN_EXPIRE_DAYS`   | Refresh token lifespan in days                          | `7`                                                              | No                     |
| `AUTH_RATE_LIMIT_PER_MINUTE`  | Max auth attempts per minute per IP                     | `10`                                                             | No                     |
| `SECURE_COOKIES`              | Enforce HTTPS `Secure` flag on auth cookies             | `false`                                                          | In production          |
| `COMPOSIO_API_KEY`            | API key for Composio OAuth & Tool Integration           | `None`                                                           | Optional               |
| `COMPOSIO_ENTITY_ID`          | Default Composio entity identifier                      | `None`                                                           | Optional               |
| `COMPOSIO_BASE_URL`           | Composio API base URL                                   | `https://api.composio.dev`                                       | No                     |
| `COMPOSIO_CALLBACK_URL`       | Composio OAuth redirect callback URL                    | `None`                                                           | Optional               |
| `COMPOSIO_ENTITY_TYPE`        | Composio entity type (`user`, `workspace`, etc.)        | `user`                                                           | No                     |
| `COMPOSIO_ENABLED_APPS`       | Comma-separated allowlist of Composio applications      | `""`                                                             | Optional               |
| `MCP_ENABLED`                 | Toggle Model Context Protocol server capabilities       | `false`                                                          | Optional               |
| `MCP_ALLOWED_TRANSPORTS`      | Allowed transport protocols (`stdio,http`)              | `stdio,http`                                                     | No                     |
| `MCP_DEFAULT_TIMEOUT_SECONDS` | Timeout for MCP tool invocations in seconds             | `30`                                                             | No                     |
| `MCP_ALLOWED_STDIO_COMMANDS`  | Allowlist of CLI commands permitted for stdio MCP       | `npx,node,python,python3,uvx,pipx`                               | No                     |
| `VITE_API_URL`                | Base URL used by the frontend to communicate with API   | `http://localhost:8000`                                          | Yes                    |

## Single-Environment (`.env`) Architecture

NexusMCP maintains a **single root `.env` file** at the project root (`/NexusMCP/.env`).
All components fetch and respect this single environment file:
- **Backend (FastAPI & Worker):** `app.config.Settings` automatically resolves the root `.env` via `pydantic-settings`.
- **Frontend (React + Vite):** `vite.config.ts` configures `envDir` pointing to the repository root to load `VITE_*` variables.
- **Docker Compose:** Automatically interpolates variables from the root `.env` and mounts `env_file: ../.env` to backend and worker containers.


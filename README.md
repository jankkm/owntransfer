# OwnTransfer

Self-hosted file sharing for teams. Authenticated users send files to external recipients or collect uploads via inbound file requests. Public links are password-protected, time-limited, and configurable with download/upload caps.

Built with **FastAPI**, server-rendered HTML, and SQLite or PostgreSQL.

## Screenshots

<!-- Add screenshots here before publishing, e.g.:
![Dashboard](docs/screenshots/dashboard.png)
![Public download page](docs/screenshots/public-download.png)
![Admin panel](docs/screenshots/admin.png)
-->

_Screenshots coming soon._

## Features

### Transfers (outbound)

- Upload one or more files and share a public download link
- Optional password, expiry date, and download limit (`0` = unlimited)
- Email share links to recipients (when SMTP is configured)
- Optional notification when someone downloads
- Enable or disable a link without deleting it
- Edit transfers after creation, including extending expiry on expired links
- Regenerate public token (invalidates the old link)

### File requests (inbound)

- Create a link for external users to upload files to you
- Staged multi-file upload with optional password and upload limits
- Download received uploads as a ZIP from the dashboard
- Same enable/disable, edit, expiry, and link-regeneration workflow as transfers

### Public access

- Unguessable link tokens (`secrets.token_urlsafe(32)`)
- Password unlock with signed cookies (no account required for recipients)
- ZIP or per-file download for transfers
- Rate limiting on public endpoints (30 requests/minute per IP by default)
- Unknown tokens redirect to login; expired or disabled links return 403/410 without treating them as attacks

### Authentication

- **OAuth2** — Microsoft Entra ID out of the box (extensible to more providers)
- **Local login** — email/password backup on the login screen (can be disabled)
- First-boot setup wizard creates the initial admin account
- OAuth users are auto-provisioned on first login (matched by email)

### Admin panel

| Area | What you can configure |
|------|------------------------|
| **Branding** | App name, logo, primary/accent colors |
| **Limits** | Max file size, default expiry, max share lifetime, default download limit, purge grace period, file extension blocklist, local login, user-sent share emails |
| **SMTP** | Outbound mail for share links and notifications |
| **Email templates** | Editable Jinja2 subjects and HTML bodies for all notification types |
| **Shares** | Overview of all transfers and requests across users; edit or delete any share |
| **Users** | Create users, promote/demote admins, reset passwords, delete accounts |
| **Impressum** | Optional legal notice page (Markdown) |
| **Audit log** | Recent admin and system actions |

### Email notifications

When SMTP is configured, the app can send:

- Share link emails to transfer recipients
- File-request link emails
- Upload-received notifications to the request owner
- Download notifications (when enabled per transfer)
- **Expired unused** — when a transfer or file request expires with zero downloads/uploads
- **Deletion reminder** — configurable days before permanent purge (global setting; `0` = disabled)

All templates (subject and body) are editable under **Admin → Email templates**.

### Lifecycle and cleanup

A background job runs every 15 minutes to:

1. Mark shares past their expiry date as expired
2. Email owners when an expired share had no downloads or uploads
3. Email owners before auto-delete, if **Deletion reminder** is enabled in admin limits
4. Permanently delete expired shares after a configurable **purge grace period** (days; `0` = auto-delete disabled)

During the grace period, shares show an **Expired** and **Deletion pending** badge in the UI. Extending expiry on the edit page clears the expired state and resets notification flags.

### Status badges

Transfers and file requests display clear status badges: Active, Expired, Disabled, download/upload limit reached, Password protected, and Deletion pending.

## Quick start

```bash
git clone https://github.com/your-org/owntransfer.git
cd owntransfer
cp .env.example .env
docker compose up --build
```

Open http://localhost:8080 and complete the setup wizard to create your admin account.

## Configuration

Most settings live in the database and are managed from the admin panel after first boot. Environment variables seed defaults on startup.

See [.env.example](.env.example) for the full list. Important variables:

| Variable | Purpose |
|----------|---------|
| `SECRET_KEY` | Session signing — **change in production** |
| `BASE_URL` | Public URL used in share links and emails (e.g. `https://transfer.example.com`) |
| `DB_BACKEND` | `sqlite` (default) or `postgres` |
| `SQLITE_PATH` | SQLite database file (default `/data/owntransfer.db`) |
| `DATABASE_URL` | Optional full database URL override |
| `UPLOAD_DIR` | Local file storage path (default `/data/uploads`) |
| `DISPLAY_TIMEZONE` | IANA timezone for UI and emails (e.g. `Europe/Berlin`) |
| `ENTRA_TENANT_ID`, `ENTRA_CLIENT_ID`, `ENTRA_CLIENT_SECRET` | Microsoft OAuth (optional) |
| `SMTP_*` | Email defaults (overridable in admin after first boot) |
| `TRUST_PROXY_HEADERS` | Set `true` behind a reverse proxy so client IPs and URLs are correct |
| `TRUSTED_PROXY_HOPS` / `TRUSTED_PROXY_IPS` | Fine-tune which proxies to trust |

### PostgreSQL

```bash
docker compose -f docker-compose.yml -f docker-compose.postgres.yml up --build
```

Or set `DB_BACKEND=postgres` and `DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/db` in `.env`.

### Secrets from files

Any environment variable supports a `VAR_FILE` companion that points to a file whose contents become `VAR`. This works for all settings (for example `SECRET_KEY_FILE`, `POSTGRES_PASSWORD_FILE`, `ENTRA_CLIENT_SECRET_FILE`). When both are set, the file value takes precedence.

### Reverse proxy

Run OwnTransfer behind nginx, Caddy, or Traefik in production. Set `BASE_URL` to your public HTTPS URL and enable `TRUST_PROXY_HEADERS=true` so security logging and rate limiting see the real client IP.

## Microsoft Entra ID

1. Azure Portal → **App registrations** → **New registration**
2. Redirect URI (Web): `https://your-domain/auth/oauth/entra/callback`
3. Create a client secret under **Certificates & secrets**
4. API permissions: `openid`, `email`, `profile` (Microsoft Graph, delegated)
5. Add to `.env`:

   ```
   ENTRA_TENANT_ID=your-tenant-id
   ENTRA_CLIENT_ID=your-client-id
   ENTRA_CLIENT_SECRET=your-client-secret
   BASE_URL=https://your-domain
   ```

Promote OAuth users to admin in **Admin → Users** after their first login.

## Deployment

### Docker

The included `docker-compose.yml` mounts a named volume at `/data` for the database and uploads. For production, place a reverse proxy in front and set a strong `SECRET_KEY`.

### Persistent storage

Mount a volume (or bind mount) at `/data` so the SQLite database and uploaded files survive container restarts. Set `UPLOAD_DIR=/data/uploads` and `SQLITE_PATH=/data/owntransfer.db`.

The app uses a storage abstraction (`app/services/storage/`). Local disk is the default; S3-compatible backends can be wired in without changing business logic.

### Kubernetes

Run the same container image with a persistent volume claim for `/data`, configure secrets for `SECRET_KEY` and database credentials, and expose the service through an ingress controller.

## Security

- Public link tokens are cryptographically random; passwords are bcrypt-hashed
- Rate limiting on public download and upload routes
- Session cookies are HTTP-only
- File extension blocklist configurable in admin
- Audit log for administrative actions

### Fail2ban

OwnTransfer writes structured `WARNING` lines to stdout for events that indicate probing or brute force:

| Event | Trigger |
|-------|---------|
| `invalid_login` | Failed local login |
| `invalid_transfer_link` | Unknown `/d/{token}` |
| `invalid_request_link` | Unknown `/r/{token}` |

Expired, disabled, or limit-reached links are **not** logged — the token exists; access is denied with 403/410.

Example log line:

```
2026-06-22 12:00:00 WARNING [owntransfer.security] event=invalid_login ip=203.0.113.10 method=POST email=attacker@example.com
```

**Filter** — save as `/etc/fail2ban/filter.d/owntransfer.conf`:

```ini
[Definition]
failregex = ^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} WARNING \[owntransfer\.security\] event=(invalid_login|invalid_transfer_link|invalid_request_link) ip=<HOST>.*$
ignoreregex =
datepattern = ^%%Y-%%m-%%d %%H:%%M:%%S
```

**Jail** — save as `/etc/fail2ban/jail.d/owntransfer.conf` (adjust `logpath`):

```ini
[owntransfer]
enabled = true
port = http,https
filter = owntransfer
logpath = /var/log/owntransfer/app.log
maxretry = 5
findtime = 600
bantime = 3600
```

Point `logpath` at your application log file. With Docker, forward container stdout to a file or use your platform's log shipping. Enable `TRUST_PROXY_HEADERS` behind a reverse proxy so `ip=` is the real client address.

## Development

Requirements: Python 3.11+

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

export DB_BACKEND=sqlite
export SQLITE_PATH=./owntransfer.db
export UPLOAD_DIR=./uploads
export SECRET_KEY=dev-secret

uvicorn app.main:app --reload --port 8080
pytest
```

Schema migrations run automatically on startup via `ensure_schema` in the application lifespan.

## License

License TBD.

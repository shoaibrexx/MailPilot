# MailPilot

MailPilot is a private, single-workspace Flask application for recipient management, email drafts, campaigns, scheduled campaign execution, suppression, and reporting. It uses SQLite by default, Gmail SMTP for explicitly authorized sending, and a separate persistent worker for campaign jobs.

The existing MailPilot brand and dark/light themes are retained. The application is not publicly deployed by this project.

## Requirements

- Windows 11
- Python 3.14.6
- Dependencies pinned in `requirements.txt`

## Local setup

From the project directory, create and activate a virtual environment if needed:

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Generate a private secret key and set it as `MAILPILOT_SECRET_KEY` in `.env`:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Keep `.env`, real credentials, database backups, and production secrets out of source control. `.env.example` contains placeholders only. Local development uses HTTP and `MAILPILOT_SECURE_COOKIES=false`. Production requires a stable random `MAILPILOT_SECRET_KEY` of at least 32 bytes; startup rejects missing or short keys.

Create the first private administrator; the password is requested interactively and is stored as a scrypt hash:

```powershell
flask --app run.py create-admin
```

Run the local web application:

```powershell
python run.py
```

Open <http://127.0.0.1:5000/>. Authentication is enabled by default. There is no public registration route. To reset the admin password and revoke all existing sessions:

```powershell
flask --app run.py set-admin-password
```

The application factory creates missing SQLite tables at startup. New features in this implementation use additive tables; `create_all()` does not alter existing columns or rewrite existing records.

## Gmail SMTP and explicit sending

Configure these values in `.env` and restart the web application:

```dotenv
MAIL_SERVER=smtp.gmail.com
MAIL_PORT=587
MAIL_USERNAME=your-account@gmail.com
MAIL_PASSWORD=your-gmail-app-password
MAIL_DEFAULT_SENDER="MailPilot <your-account@gmail.com>"
MAIL_TIMEOUT=20
MAILPILOT_DAILY_RECIPIENT_LIMIT=100
```

Use a Google app password where required. The sender must be the Gmail account or an alias verified by that account. MailPilot uses STARTTLS on port 587. **Settings -> Gmail SMTP -> Test SMTP connection** authenticates and closes the connection; it does not send a message. The connection test has a persistent 30-second workspace cooldown.

Composer sending is a separate, explicit user action. It validates recipients, consent confirmation, and the local rolling 24-hour cap before the SMTP request. Draft saving never sends. The default MailPilot cap is 100 recipient attempts in a rolling 24-hour period across campaign and composer sends; each attempt counts even if SMTP fails transiently. Set a conservative value that fits your Gmail account's current limits. This local cap does not replace Google's account-specific policies or quota controls.

Campaign sends require saved active contacts, a recipient-consent confirmation, Gmail SMTP configuration, an HTTPS public URL for unsubscribe links, and a workspace postal address. Campaign messages are sent individually with recipients in Bcc. SMTP acceptance is recorded separately from inbox delivery. Ambiguous SMTP outcomes are not retried automatically.

## Campaign worker and scheduled execution

Run the worker in a second terminal. It shares the same `.env` and SQLite database with the web process:

```powershell
flask --app run.py campaign-worker
```

Keep this process running to execute campaign jobs and retry due transient failures. It stores jobs and per-recipient checkpoints in SQLite, uses leases to coordinate worker processes, and resumes jobs after restart. Run `flask --app run.py campaign-worker --once` to process currently due work once and exit.

An immediate campaign send must be explicitly queued from a campaign marked **Ready**. A campaign saved as **Scheduled** does not send merely because of its status: review and explicitly authorize its audience and scheduled time on the campaign page. The separate worker then waits until that authorized date and time, interpreted using the workspace timezone. Drafts are never scheduled or sent automatically.

MailPilot limits each campaign job to 100 recipients and defaults to a rolling 24-hour local cap of 100 recipient attempts. SMTP transient failures are retried with backoff. Addresses accepted by SMTP are not resent on campaign retries. Uncertain delivery outcomes require manual review.

## Unsubscribe and suppression

Campaign mail includes a unique opaque unsubscribe link, RFC 8058 one-click headers, and the configured postal address. Public unsubscribe requests do not require login and create idempotent global suppressions. Manual campaign-level and global suppressions are checked again immediately before SMTP submission. Recipient records remain separate from suppression history.

Before sending campaigns, set:

```dotenv
MAILPILOT_PUBLIC_BASE_URL=https://mail.example.com
```

Use an HTTPS origin you control and enter a valid workspace mailing address under Settings. The project does not configure or host a public domain. MailPilot provides the application controls; the workspace owner remains responsible for consent, lawful use, and email compliance.

## Optional bounce processing

Gmail SMTP does not provide an inbound bounce feed. If bounce collection is needed, enable Gmail IMAP and configure a mailbox that receives the sender's delivery-status notifications:

```dotenv
MAIL_IMAP_SERVER=imap.gmail.com
MAIL_IMAP_PORT=993
MAIL_IMAP_USERNAME=your-account@gmail.com
MAIL_IMAP_PASSWORD=your-gmail-app-password
MAIL_IMAP_FOLDER=INBOX
MAIL_IMAP_POLL_SECONDS=60
```

The worker connects over IMAP SSL, reads messages using `BODY.PEEK[]`, and stores a mailbox cursor and parsed RFC 3464 delivery-status events. It retains no raw bounce message body or SMTP password in the event records. Permanent matched bounces create global suppressions; temporary failures do not. Only DSNs matched to MailPilot campaign attempt message IDs are counted. Unmatched or unsupported notices, inbox delivery, opens, and clicks are not measured. Bounce processing is **awaiting real mailbox configuration and has not been validated against a live Gmail account**.

## Analytics

The Dashboard and Analytics report saved campaign/recipient data plus actual send records. Analytics distinguishes campaign recipient attempts, SMTP-accepted campaign messages, known failures, uncertain outcomes, current pending recipients, current suppression records, and matched permanent/temporary DSNs. Composer data is shown as message outcomes; older composer history does not contain a per-recipient accepted count, so partial composer sends remain a separate count.

SMTP acceptance does not prove that a recipient server delivered the message to an inbox. MailPilot does not fabricate delivered, opened, clicked, or delivery-rate values. Date filters apply to actual SMTP attempts and bounce timestamps; selected campaign-creation trends use campaign creation dates. Current queue and suppression counts are labelled as current.

CSV exports include formula-injection protection for user-controlled text fields.

## Database, upgrades, and backups

The default database is `instance/mailpilot.sqlite`. Keep the instance directory on persistent local storage and back it up regularly. Do not place SQLite on a network share or use it as a multi-host database. SQLite is suitable for a single-host local or small-workspace deployment; for multi-host or higher-write-concurrency use, plan and test a separate PostgreSQL migration before deployment. PostgreSQL is not bundled as a configured production database in this project.

Before upgrading an existing workspace, stop the web and worker processes and make a backup. New tables are created additively on startup. `create_all()` does not update existing tables, so future column changes require a migration strategy before rollout. This implementation did not drop or rebuild existing tables.

The **Reset workspace data** action blocks while a campaign worker or direct composer send is actively in progress. It cancels queued campaign jobs and clears contacts, groups, drafts, saved campaigns, and composer send-history rows. Campaign execution snapshots/delivery attempts, bounce records, unsubscribe tokens, and suppression records are retained; global suppressions remain in place so an opted-out address is not reintroduced by a later import. Review and protect those retained records as workspace data. Workspace settings, notifications, and security events are also preserved.

Create a consistent SQLite backup using its online backup API:

```powershell
python -c "import sqlite3; source=sqlite3.connect('instance/mailpilot.sqlite'); backup=sqlite3.connect('mailpilot-backup.sqlite'); source.backup(backup); backup.close(); source.close()"
```

Restore only while MailPilot and its worker are stopped. Keep backups encrypted and access-controlled; they include workspace contact and campaign data.

## Deployment preparation (not deployed)

For a single Windows host, Waitress is included as the production WSGI server. A production environment must have a stable `MAILPILOT_SECRET_KEY`, `MAILPILOT_ENV=production`, `FLASK_DEBUG=false`, and `MAILPILOT_SECURE_COOKIES=true`. The factory rejects production startup without the secret, with debug enabled, or without secure cookies.

After configuring HTTPS termination and persistent storage, the local-only WSGI command is:

```powershell
waitress-serve --listen=127.0.0.1:8000 wsgi:app
```

This binds Waitress to loopback. A trusted HTTPS reverse proxy may connect locally; do not bind publicly unless you have separately reviewed and authorized the network design. If the proxy overwrites and forwards `X-Forwarded-For` and `X-Forwarded-Proto`, set `MAILPILOT_TRUSTED_PROXY_HOPS` to the exact number of trusted proxies between it and Waitress. It defaults to zero, and forwarded headers are ignored unless explicitly enabled. Do not enable it if clients can reach Waitress directly or the proxy passes through client-supplied forwarding headers. Start the campaign worker as a separate supervised process. The health check is available at `/health`.

MailPilot adds CSP, clickjacking, MIME-sniffing, referrer, no-store, and production HSTS response headers. The reverse proxy must still terminate HTTPS, restrict network access, and redact unsubscribe token paths from any proxy access logs.

Production use still requires operator-managed HTTPS, secrets, firewalling, database backup/restore drills, an administrator account, Gmail configuration, and any optional IMAP configuration. No hosting, domain, public exposure, or deployment is performed here.

## Tests and JavaScript checks

Run the complete Python suite:

```powershell
python -m unittest discover -v
```

Run a JavaScript syntax check for every frontend script from PowerShell:

```powershell
Get-ChildItem mailpilot/static/js/*.js | ForEach-Object { node --check $_.FullName }
```

Automated SMTP tests use mocks and temporary SQLite databases. They do not send real email or read the workspace database.

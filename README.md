# Video Intelligence API

## Local development

```powershell
cd backend
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python manage.py migrate
python manage.py runserver 127.0.0.1:8000
```

Set `OPENAI_API_KEY` in `.env` before analyzing transcripts or videos. Keep `.env` out of source control. For public deployments, use a unique `DJANGO_SECRET_KEY`, set `DJANGO_DEBUG=false`, use HTTPS, and configure the exact frontend origin in `CORS_ALLOWED_ORIGINS`.

The SQLite database contains user accounts, video transcripts, and paid analysis results. It is local persistent data and must not be stored in Git. For deployment, set `DJANGO_DB_PATH` to a durable path outside the code checkout (for example `/var/lib/video-summarizer/db.sqlite3`) and make it readable/writable by both Django and the SharePoint worker. Back up the source database and copy it to that path before deploying a commit that removes the previously tracked `backend/db.sqlite3`; then run migrations. Do not replace the server database with a fresh checkout copy.

To carry the local `admin4@gmail.com` account and its library to a new server, make a consistent SQLite backup with Python's SQLite backup API, transfer that backup over SSH, and install it at `DJANGO_DB_PATH` before the first deploy. This copies the user table and owner-linked video rows together. Back up any database already on the server before replacing it; replacing the whole DB also replaces its other accounts and records. After deployment run `python manage.py migrate`; the analysis-version migration defaults existing rows to version `1`, and successful SharePoint analyses are frozen, so they are not sent to OpenAI again. With PM2, run one persistent worker from the backend directory using the same `.env` and `DJANGO_DB_PATH` as the API, for example `pm2 start ./venv/bin/python --name video-sharepoint-worker -- manage.py process_sharepoint_imports --poll-seconds 5`, then `pm2 save`.

## Accounts and authorization

- `POST /api/auth/register/` accepts `name`, `email`, and a password of at least 12 characters. Successful registration signs the user in.
- `POST /api/auth/login/` accepts `email` and `password`.
- `GET /api/auth/me/` returns the authenticated account.
- `POST /api/auth/logout/` revokes the current API token.
- Authenticated requests use `Authorization: Token <token>`.
- Every video is scoped to its owner. Videos created before owner accounts were added remain unassigned and are not included in a user's library.
- The configured SharePoint folder is shared across accounts. When a user's library is loaded, saved indexed SharePoint results missing from that account are copied by drive/item ID into its library; this reuses the transcript and AI result without calling OpenAI again.
- Registration and login are rate limited; AI analysis is limited to five requests per user per hour.

The health endpoint is public. Video, upload, transcription, analysis, and reanalysis endpoints require authentication.

## SharePoint video folder import

The configured folder uses Microsoft Graph app-only access. The folder is pinned by its drive and item IDs in the backend environment; the browser never receives the Microsoft app credential. The backend worker recursively discovers MP4, MOV, and WEBM files and automatically queues new videos. Once a video is successfully analyzed, automatic sync never analyzes it again, even if SharePoint later reports a changed file version. It analyzes at most two new videos concurrently and persists each result. A database uniqueness constraint on owner, drive ID, and item ID prevents duplicate SharePoint records. Graph cTag/eTag, size, and modification time are retained as source metadata. Temporary transcription and analysis failures retain useful checkpoints and receive bounded automatic retries; exhausted failures require the user's per-video Retry action.

1. In Microsoft Entra, rotate the client secret that was used for setup and grant the registered application read access to the target folder (or its dedicated site/library). Microsoft Graph app-only access requires admin consent and an explicit resource grant when using a selected permission.
2. Set `MS_GRAPH_TENANT_ID`, `MS_GRAPH_CLIENT_ID`, `MS_GRAPH_CLIENT_SECRET`, `MS_GRAPH_DRIVE_ID`, `MS_GRAPH_FOLDER_ITEM_ID`, and `MS_GRAPH_FOLDER_NAME` in `backend/.env` or the deployment's secret store. The tenant, app, drive, and folder IDs from the supplied setup guide are prefilled in the local `.env` and `.env.example`; enter only the newly rotated secret in the backend environment. Do not commit `.env` or place the secret in frontend variables.
3. Apply migrations: `python manage.py migrate`.
4. Start one import worker alongside Django: `python manage.py process_sharepoint_imports`. Keep one worker process for this SQLite-backed POC; it runs two analysis threads so the total concurrency stays at two. The worker polls the persistent database queue and continues processing even after the browser closes.
5. Start the worker before signing in: `python manage.py process_sharepoint_imports`. Signing in to an account with no SharePoint records marks the initial server-side sync due immediately. The worker scans the folder and queues eligible videos; no dashboard import click is needed. It processes work in pairs and continues after the browser closes. Individual failed videos can be retried from their library cards. Use `python manage.py process_sharepoint_imports --once` to process one queued job and exit.

### Daily automatic SharePoint synchronization

The `sync_sharepoint_folder` command compares stable SharePoint identity and version metadata with each active user's saved videos, then queues only new items for the same worker. Previously indexed videos are treated as complete regardless of later source changes. Each result records an analysis version for auditing. The dashboard's **Sync now** action marks the scan due immediately; the worker performs the Graph scan and processes any new items. A renewing database lease prevents overlapping scans, and repeated inventory items are deduplicated. Failed scans preserve the last successful checkpoint and retry after 15 minutes. `SHAREPOINT_SYNC_INTERVAL_HOURS` configures the successful scan interval (default 24); Graph retries honor `Retry-After` and use bounded backoff. The dashboard shows last/next sync and discovered, pending, analyzed, and failed counts. Scheduling runs in the backend worker and therefore does not depend on an open browser, but the worker service must remain running.

On Ubuntu/systemd, the import worker handles daily sync automatically. The optional timer unit can also run the sync independently if the import worker is temporarily unavailable. Update the paths and user in `deploy/video-sharepoint-sync.service` if enabling it, then install the timer:

```bash
sudo cp deploy/video-sharepoint-sync.service /etc/systemd/system/video-sharepoint-sync.service
sudo cp deploy/video-sharepoint-sync.timer /etc/systemd/system/video-sharepoint-sync.timer
sudo systemctl daemon-reload
sudo systemctl enable --now video-sharepoint-sync.timer
sudo systemctl list-timers video-sharepoint-sync.timer --no-pager
```

The timer service must share the backend `.env`, SQLite database, and virtual environment with Django and the import worker. Check its most recent run with `sudo journalctl -u video-sharepoint-sync.service -n 100 --no-pager`. The sync command persists queued jobs before exiting, so an unavailable worker does not lose discovered videos.

The dashboard's **Delete all** action removes only the signed-in user's video records. It requires confirmation and is disabled while a folder import is active; stop the import first. The backend also rejects a bulk delete while an import is active.

### Keep the production worker running (Ubuntu / EC2)

The Django web service only queues SharePoint imports. Run one separate worker process on the same backend host, with access to the same SQLite database, `.env`, and video storage. Without it, imports remain at **Queued · waiting for the SharePoint worker**.

The example unit at `deploy/video-sharepoint-worker.service` assumes the repository is checked out at `/home/ubuntu/actions-runner/_work/Video-Summarizer/Video-Summarizer` and that Django files, `.env`, and the virtual environment are in its `backend/` subdirectory. Update both paths in the unit if your deployment differs. After deploying backend changes, install the unit from the backend directory and restart it:

```bash
cd /home/ubuntu/actions-runner/_work/Video-Summarizer/Video-Summarizer/backend
sudo cp deploy/video-sharepoint-worker.service /etc/systemd/system/video-sharepoint-worker.service
sudo systemctl daemon-reload
sudo systemctl enable --now video-sharepoint-worker
sudo systemctl restart video-sharepoint-worker
sudo systemctl status video-sharepoint-worker --no-pager
sudo journalctl -u video-sharepoint-worker -n 100 --no-pager
```

The Django web service also needs the new backend code and migrations. From the same `backend/` directory, run `venv/bin/python manage.py migrate`, then restart the existing Gunicorn service (find its exact unit name with `systemctl list-units --type=service | grep -i gunicorn`). Queued imports resume automatically when the worker is healthy. The worker loads Microsoft Graph and OpenAI configuration from `backend/.env`; do not copy either secret into the frontend or the unit file.

Only MP4, MOV, and WEBM are imported. SharePoint videos are streamed to temporary storage and audio is split into 8-minute MP3 segments before transcription, keeping each OpenAI transcription request well below its 25 MB file limit. The default SharePoint source-video cap is 5 GB and can be changed with `MAX_VIDEO_SIZE_GB`; make sure the backend host has enough temporary disk space for the source file and extracted audio. The individual browser upload form still has its separate 500 MB reverse-proxy/request-size limit. If transcription succeeds but summarization temporarily fails, the transcript is saved and reused on retry so the large video does not need another download/transcription pass. The Graph `/content` redirect's short-lived download URL is used only in memory and is never saved or logged. For a multi-instance deployment, move the queue and concurrency limiter from SQLite to a dedicated task broker/database before scaling beyond one worker service.

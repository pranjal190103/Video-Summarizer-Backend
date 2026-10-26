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

## Accounts and authorization

- `POST /api/auth/register/` accepts `name`, `email`, and a password of at least 12 characters. Successful registration signs the user in.
- `POST /api/auth/login/` accepts `email` and `password`.
- `GET /api/auth/me/` returns the authenticated account.
- `POST /api/auth/logout/` revokes the current API token.
- Authenticated requests use `Authorization: Token <token>`.
- Every video is scoped to its owner. Videos created before owner accounts were added remain unassigned and are not included in a user's library.
- Registration and login are rate limited; AI analysis is limited to five requests per user per hour.

The health endpoint is public. Video, upload, transcription, analysis, and reanalysis endpoints require authentication.

## SharePoint video folder import

The configured folder import uses Microsoft Graph app-only access. The folder is pinned by its drive and item IDs in the backend environment; the browser never receives the Microsoft app credential. The importer recursively finds MP4, MOV, and WEBM files and queues **all remaining videos in one click**. It skips videos already indexed in the signed-in user's library, saves each video's result as it completes, and updates the dashboard during processing. The worker keeps the folder listing in memory for five minutes. The user can stop the import after the current video finishes.

1. In Microsoft Entra, rotate the client secret that was used for setup and grant the registered application read access to the target folder (or its dedicated site/library). Microsoft Graph app-only access requires admin consent and an explicit resource grant when using a selected permission.
2. Set `MS_GRAPH_TENANT_ID`, `MS_GRAPH_CLIENT_ID`, `MS_GRAPH_CLIENT_SECRET`, `MS_GRAPH_DRIVE_ID`, `MS_GRAPH_FOLDER_ITEM_ID`, and `MS_GRAPH_FOLDER_NAME` in `backend/.env` or the deployment's secret store. The tenant, app, drive, and folder IDs from the supplied setup guide are prefilled in the local `.env` and `.env.example`; enter only the newly rotated secret in the backend environment. Do not commit `.env` or place the secret in frontend variables.
3. Apply migrations: `python manage.py migrate`.
4. Start one import worker alongside Django: `python manage.py process_sharepoint_imports`. Keep a single worker process for this SQLite-backed POC. The worker polls the database queue and continues processing even after the browser closes.
5. Sign in, choose a batch size from **1 to 50**, and click **Import next N videos** in the SharePoint folder panel. The dashboard shows batch progress and adds each completed video to the library while the worker continues in the background. Click again when ready for the next batch. **Stop import** requests a stop after the video currently being processed; completed videos remain saved. Use `python manage.py process_sharepoint_imports --once` to process one queued batch and exit.

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

The Django web service also needs the new backend code and migrations. From the same `backend/` directory, run `venv/bin/python manage.py migrate`, then restart the existing Gunicorn service (find its exact unit name with `systemctl list-units --type=service | grep -i gunicorn`). An import already waiting in the production queue should start automatically when the worker is healthy. The worker loads Microsoft Graph and OpenAI configuration from `backend/.env`; do not copy either secret into the frontend or the unit file.

Only MP4, MOV, and WEBM are imported. SharePoint videos are streamed to temporary storage and audio is split into 8-minute MP3 segments before transcription, keeping each OpenAI transcription request well below its 25 MB file limit. The default source-video cap is 5 GB and can be changed with `MAX_VIDEO_SIZE_GB`; make sure the backend host has enough temporary disk space for the source file and extracted audio. The individual browser upload form still has its separate 500 MB reverse-proxy/request-size limit. The Graph `/content` redirect's short-lived download URL is used only in memory and is never saved or logged. For a multi-process production deployment, move this queue from SQLite to a dedicated task broker/database and run the worker under a service manager.

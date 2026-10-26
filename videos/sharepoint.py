"""App-only Microsoft Graph access for the configured SharePoint video folder."""
import os
import random
import tempfile
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote, urlparse

import httpx
from django.conf import settings

MAX_VIDEO_BYTES = settings.MAX_VIDEO_SIZE_BYTES
VIDEO_EXTENSIONS = {'.mp4', '.mov', '.webm'}
GRAPH_ROOT = 'https://graph.microsoft.com/v1.0'
GRAPH_RETRY_STATUSES = {429, 500, 502, 503, 504}
GRAPH_MAX_RETRIES = 5


class SharePointError(Exception):
    """A safe-to-display SharePoint integration error."""


def is_configured():
    return all((settings.MS_GRAPH_TENANT_ID, settings.MS_GRAPH_CLIENT_ID,
                settings.MS_GRAPH_CLIENT_SECRET, settings.MS_GRAPH_DRIVE_ID,
                settings.MS_GRAPH_FOLDER_ITEM_ID))


class GraphClient:
    def __init__(self):
        if not is_configured():
            raise SharePointError('SharePoint access is not configured on the backend.')
        self.drive_id = settings.MS_GRAPH_DRIVE_ID
        self.folder_id = settings.MS_GRAPH_FOLDER_ITEM_ID
        self.http = httpx.Client(timeout=httpx.Timeout(60, connect=20), follow_redirects=False)
        self.token = ''
        self.token_expires_at = 0
        self._refresh_token()

    def _refresh_token(self):
        token_url = f'https://login.microsoftonline.com/{quote(settings.MS_GRAPH_TENANT_ID, safe="")}/oauth2/v2.0/token'
        try:
            response = self.http.post(token_url, data={
                'client_id': settings.MS_GRAPH_CLIENT_ID,
                'client_secret': settings.MS_GRAPH_CLIENT_SECRET,
                'scope': 'https://graph.microsoft.com/.default',
                'grant_type': 'client_credentials',
            })
            if response.status_code != 200:
                raise SharePointError('Microsoft sign-in failed. Check the tenant, app ID, and rotated app credential in backend configuration.')
            body = response.json()
            self.token = body.get('access_token')
            if not self.token:
                raise SharePointError('Microsoft sign-in did not return an access token.')
            self.token_expires_at = time.monotonic() + int(body.get('expires_in', 3600)) - 60
        except httpx.HTTPError as error:
            raise SharePointError('Could not connect to Microsoft sign-in. Check backend network access.') from error

    @property
    def headers(self):
        if time.monotonic() >= self.token_expires_at:
            self._refresh_token()
        return {'Authorization': f'Bearer {self.token}', 'Accept': 'application/json'}

    @staticmethod
    def _retry_delay(response, attempt):
        """Respect Graph's Retry-After header and use capped backoff otherwise."""
        retry_after = response.headers.get('Retry-After', '') if response else ''
        try:
            return min(300.0, max(0.0, float(retry_after)))
        except (TypeError, ValueError):
            try:
                retry_at = parsedate_to_datetime(retry_after)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                return min(300.0, max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds()))
            except (TypeError, ValueError, OverflowError):
                return min(30.0, 2.0 ** attempt) + random.uniform(0.0, 0.25)

    def _get_graph(self, url):
        """Retry transient Graph throttling and service errors without request bursts."""
        response = None
        for attempt in range(GRAPH_MAX_RETRIES + 1):
            try:
                response = self.http.get(url, headers=self.headers)
            except httpx.HTTPError:
                if attempt >= GRAPH_MAX_RETRIES:
                    raise
                time.sleep(min(30.0, 2.0 ** attempt) + random.uniform(0.0, 0.25))
                continue
            if response.status_code not in GRAPH_RETRY_STATUSES or attempt >= GRAPH_MAX_RETRIES:
                return response
            delay = self._retry_delay(response, attempt)
            response.close()
            time.sleep(delay)
        return response

    def children(self, folder_id):
        path = f'{GRAPH_ROOT}/drives/{quote(self.drive_id, safe="")}/items/{quote(folder_id, safe="")}/children'
        next_url = path + '?$select=id,name,size,file,folder,webUrl,lastModifiedDateTime&$top=200'
        while next_url:
            if urlparse(next_url).scheme != 'https' or urlparse(next_url).hostname != 'graph.microsoft.com':
                raise SharePointError('Microsoft Graph returned an unexpected paging URL.')
            try:
                response = self._get_graph(next_url)
            except httpx.HTTPError as error:
                raise SharePointError('Could not list files in the configured SharePoint folder.') from error
            if response.status_code != 200:
                if response.status_code == 401:
                    raise SharePointError('Microsoft Graph rejected the access token. Check the tenant ID, client ID, and client secret.')
                if response.status_code == 403:
                    raise SharePointError('Microsoft Graph denied folder access. Ask your administrator to grant this app read access to the configured folder.')
                if response.status_code == 404:
                    raise SharePointError('Microsoft Graph could not find the configured drive or folder. Verify MS_GRAPH_DRIVE_ID and MS_GRAPH_FOLDER_ITEM_ID.')
                if response.status_code == 429:
                    raise SharePointError('Microsoft Graph is still throttling folder access after several retries. Wait a few minutes and try again.')
                raise SharePointError(f'Microsoft Graph could not list the folder (HTTP {response.status_code}).')
            try:
                body = response.json()
            except ValueError as error:
                raise SharePointError('Microsoft Graph returned an invalid folder listing response.') from error
            if not isinstance(body, dict) or not isinstance(body.get('value'), list):
                raise SharePointError('Microsoft Graph returned an unexpected folder listing response.')
            yield from body.get('value', [])
            next_url = body.get('@odata.nextLink')

    def list_videos(self):
        videos = []
        pending_folders = [self.folder_id]
        visited = set()
        while pending_folders:
            folder_id = pending_folders.pop()
            if folder_id in visited:
                continue
            visited.add(folder_id)
            for item in self.children(folder_id):
                if item.get('folder') is not None:
                    pending_folders.append(item['id'])
                    continue
                ext = os.path.splitext(item.get('name', ''))[1].lower()
                if item.get('file') is not None and ext in VIDEO_EXTENSIONS:
                    videos.append(item)
        return videos

    def download(self, item, destination):
        item_id = quote(item['id'], safe='')
        drive_id = quote(self.drive_id, safe='')
        url = f'{GRAPH_ROOT}/drives/{drive_id}/items/{item_id}/content'
        try:
            response = self._get_graph(url)
        except httpx.HTTPError as error:
            raise SharePointError('Could not request the video from SharePoint.') from error
        if response.status_code not in (301, 302, 303, 307, 308) or not response.headers.get('location'):
            if response.status_code == 403:
                raise SharePointError('Microsoft Graph denied access to this video. Check the app folder permission.')
            if response.status_code == 429:
                raise SharePointError('Microsoft Graph is still throttling video access after several retries. Wait a few minutes and try again.')
            raise SharePointError(f'Microsoft Graph could not open the video (HTTP {response.status_code}).')

        # Graph returns a short-lived preauthorized download URL. Request it without
        # the Graph bearer token and never persist or log the signed URL.
        try:
            with self.http.stream('GET', response.headers['location']) as download:
                if download.status_code != 200:
                    raise SharePointError(f'SharePoint video download failed (HTTP {download.status_code}).')
                length = int(download.headers.get('content-length', 0) or 0)
                if length > MAX_VIDEO_BYTES:
                    raise SharePointError(f'Video exceeds the {settings.MAX_VIDEO_SIZE_GB} GB processing limit.')
                written = 0
                for chunk in download.iter_bytes(1024 * 1024):
                    written += len(chunk)
                    if written > MAX_VIDEO_BYTES:
                        raise SharePointError(f'Video exceeds the {settings.MAX_VIDEO_SIZE_GB} GB processing limit.')
                    destination.write(chunk)
        except httpx.HTTPError as error:
            raise SharePointError('Could not download the video from SharePoint.') from error
        if not written:
            raise SharePointError('The SharePoint video file was empty.')

    def close(self):
        self.http.close()


def download_to_temp(graph, item):
    suffix = os.path.splitext(item.get('name', ''))[1].lower()
    if suffix not in VIDEO_EXTENSIONS:
        raise SharePointError('This video format is not supported. Use MP4, MOV, or WEBM.')
    handle = tempfile.NamedTemporaryFile(prefix='sharepoint-video-', suffix=suffix, delete=False)
    path = handle.name
    try:
        with handle:
            graph.download(item, handle)
        return path
    except Exception:
        try:
            os.unlink(path)
        except OSError:
            pass
        raise

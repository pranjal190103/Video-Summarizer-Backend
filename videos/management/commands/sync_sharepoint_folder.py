import time
from datetime import timedelta
import os
import logging
import threading
import uuid
from datetime import timedelta
from django.db.models import Q

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections
from django.utils import timezone

from videos.models import SharePointImport, SharePointSyncState, Video
from videos.library import copy_analyzed_sharepoint_results
from videos.sharepoint import GraphClient, SharePointError, item_version, source_version_changed

logger = logging.getLogger(__name__)
SYNC_LEASE_SECONDS = 180
SYNC_LEASE_HEARTBEAT_SECONDS = 30


def _renew_sync_lease(lease_token, stop_event):
    while not stop_event.wait(SYNC_LEASE_HEARTBEAT_SECONDS):
        close_old_connections()
        renewed = SharePointSyncState.objects.filter(pk=1, lease_token=lease_token).update(
            lease_expires_at=timezone.now() + timedelta(seconds=SYNC_LEASE_SECONDS),
        )
        if not renewed:
            logger.error('SharePoint sync lease was lost while the scan was running.')
            break
    close_old_connections()


def item_is_current(item, videos_by_identity, videos_by_url):
    drive_id = str(item.get('driveId') or settings.MS_GRAPH_DRIVE_ID)
    item_id = str(item.get('id') or '')
    video = videos_by_identity.get((drive_id, item_id)) or videos_by_url.get(str(item.get('webUrl') or ''))
    if not video:
        return False
    # A permanent failure is no longer an automatic daily retry candidate.
    # The user's explicit per-video Retry action can still run it again.
    if (video.status == 'failed'
            and video.retry_count >= settings.SHAREPOINT_MAX_VIDEO_RETRIES + 1):
        return True
    # Successful analyses are immutable for automatic sync purposes. Even if a
    # SharePoint file changes later, only a new item may incur another AI run.
    if video.status == 'indexed':
        return True
    return False


class Command(BaseCommand):
    help = 'Discover SharePoint videos and persist import jobs for application users.'

    def handle(self, *args, **options):
        state, _ = SharePointSyncState.objects.get_or_create(pk=1)
        now = timezone.now()
        lease_token = uuid.uuid4()
        claimed = SharePointSyncState.objects.filter(pk=1).filter(
            Q(lease_expires_at__isnull=True) | Q(lease_expires_at__lte=now)
        ).update(
            lease_token=lease_token,
            lease_expires_at=now + timedelta(seconds=SYNC_LEASE_SECONDS),
            last_attempt_at=now,
            last_error='',
        )
        if not claimed:
            self.stdout.write('SharePoint synchronization is already running; skipping this invocation.')
            return
        stop_heartbeat = threading.Event()
        heartbeat = threading.Thread(
            target=_renew_sync_lease,
            args=(lease_token, stop_heartbeat),
            daemon=True,
        )
        heartbeat.start()
        state.refresh_from_db()
        retries = max(0, settings.SHAREPOINT_SYNC_MAX_RETRIES)
        graph = None
        try:
            for attempt in range(retries + 1):
                try:
                    graph = GraphClient()
                    items = graph.list_videos()
                    break
                except SharePointError:
                    if graph:
                        graph.close()
                        graph = None
                    if attempt >= retries:
                        raise
                    time.sleep(settings.SHAREPOINT_SYNC_RETRY_DELAY_SECONDS)

            User = get_user_model()
            discovered = 0
            queued_jobs = 0
            for user in User.objects.filter(is_active=True).iterator():
                # Reuse the team's persisted analyses before deciding which
                # items this account needs to process.
                copy_analyzed_sharepoint_results(user)
                videos = list(Video.objects.filter(owner=user, source_type='sharepoint'))
                by_identity = {
                    (video.source_drive_id, video.source_item_id): video
                    for video in videos if video.source_drive_id and video.source_item_id
                }
                by_url = {video.source_url: video for video in videos if video.source_url}
                for item in items:
                    video = by_identity.get((
                        str(item.get('driveId') or settings.MS_GRAPH_DRIVE_ID),
                        str(item.get('id') or ''),
                    )) or by_url.get(str(item.get('webUrl') or ''))
                    if video and video.status == 'indexed' and not source_version_changed(video, item) and (
                        not video.source_item_id or not video.source_modified_at
                        or (item.get('size') is not None and video.source_size is None)
                        or (item.get('eTag') and not video.source_etag)
                        or (item.get('cTag') and not video.source_ctag)
                    ):
                        video.source_drive_id = str(item.get('driveId') or settings.MS_GRAPH_DRIVE_ID)
                        video.source_item_id = str(item.get('id') or video.source_item_id)
                        version = item_version(item)
                        video.source_modified_at = version['source_modified_at'] or video.source_modified_at
                        video.source_size = version['source_size'] if version['source_size'] is not None else video.source_size
                        video.source_etag = version['source_etag'] or video.source_etag
                        video.source_ctag = version['source_ctag'] or video.source_ctag
                        video.save(update_fields=[
                        'source_drive_id', 'source_item_id', 'source_modified_at',
                        'source_size', 'source_etag', 'source_ctag', 'updated_at',
                        ])
                        if video.source_item_id:
                            by_identity[(video.source_drive_id, video.source_item_id)] = video
                candidates = [
                    item for item in items
                    if item.get('webUrl') and not item_is_current(item, by_identity, by_url)
                ]
                discovered = len(items)
                for item in candidates:
                    drive_id = str(item.get('driveId') or settings.MS_GRAPH_DRIVE_ID)
                    item_id = str(item.get('id') or '')
                    video = by_identity.get((drive_id, item_id)) or by_url.get(str(item.get('webUrl') or ''))
                    if not video:
                        video = Video.objects.create(
                            owner=user,
                            title=os.path.splitext(str(item.get('name') or 'SharePoint video'))[0][:300],
                            source_url=str(item.get('webUrl') or ''),
                            source_type='sharepoint',
                            source_folder=str(item.get('sourceFolder') or settings.MS_GRAPH_FOLDER_NAME)[:500],
                            source_drive_id=drive_id,
                            source_item_id=item_id,
                            **item_version(item),
                            status='pending',
                            analysis_mode='ai',
                        )
                        if item_id:
                            by_identity[(drive_id, item_id)] = video
                        by_url[video.source_url] = video
                    elif video.status == 'indexed' and source_version_changed(video, item):
                        video.status = 'pending'
                        video.error_message = ''
                        video.save(update_fields=['status', 'error_message', 'updated_at'])
                if not candidates or SharePointImport.objects.filter(
                    owner=user, status__in=['queued', 'processing', 'cancelling']
                ).exists():
                    continue
                SharePointImport.objects.create(
                    owner=user,
                    folder_name=settings.MS_GRAPH_FOLDER_NAME,
                    batch_size=settings.SHAREPOINT_BATCH_SIZE,
                    trigger='scheduled',
                )
                queued_jobs += 1

            now = timezone.now()
            state.last_success_at = now
            state.next_sync_at = now + timedelta(hours=settings.SHAREPOINT_SYNC_INTERVAL_HOURS)
            state.discovered_count = discovered
            state.last_error = ''
            state.save(update_fields=[
                'last_success_at', 'next_sync_at', 'discovered_count', 'last_error', 'updated_at',
            ])
            self.stdout.write(self.style.SUCCESS(
                f'SharePoint sync succeeded: {discovered} videos discovered; {queued_jobs} user jobs queued.'
            ))
        except SharePointError as error:
            state.last_error = str(error)[:500]
            # Retry transient or configuration failures soon; retain last_success_at.
            state.next_sync_at = timezone.now() + timedelta(minutes=15)
            state.save(update_fields=['last_error', 'next_sync_at', 'updated_at'])
            raise CommandError(str(error)) from error
        finally:
            if graph:
                graph.close()
            stop_heartbeat.set()
            heartbeat.join(timeout=SYNC_LEASE_HEARTBEAT_SECONDS + 1)
            SharePointSyncState.objects.filter(pk=1, lease_token=lease_token).update(
                lease_token=None, lease_expires_at=None,
            )
            close_old_connections()

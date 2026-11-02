import logging
import os
import subprocess
import time
import tempfile
import threading
import uuid
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

from django.core.files import File
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from django.db import close_old_connections, transaction
from django.db.models import Q
from django.utils import timezone
from openai import OpenAIError

from videos.models import SharePointAnalysisSlot, SharePointImport, SharePointSyncState, Video
from videos.library import copy_analyzed_sharepoint_results
from videos.services import analyze_transcript, transcribe_video
from videos.sharepoint import GraphClient, SharePointError, download_to_temp, item_version, source_version_changed
from videos.openai_errors import openai_rate_limit_message

logger = logging.getLogger(__name__)
LISTING_CACHE_SECONDS = 300
BATCH_PAUSE_SECONDS = 1
ANALYSIS_SLOT_LEASE_SECONDS = 120
ANALYSIS_SLOT_HEARTBEAT_SECONDS = 30
_video_listing_cache = {'key': None, 'loaded_at': 0.0, 'items': None}
_slot_claim_lock = threading.Lock()


def iter_batches(items, size):
    """Yield sequential groups while allowing one job to continue automatically."""
    size = max(1, int(size))
    for start in range(0, len(items), size):
        yield items[start:start + size]


def cached_video_listing(graph, force_refresh=False):
    """Keep the expensive recursive Graph listing warm between batch clicks."""
    key = (graph.drive_id, graph.folder_id)
    now = time.monotonic()
    if (not force_refresh and _video_listing_cache['key'] == key
            and _video_listing_cache['items'] is not None
            and now - _video_listing_cache['loaded_at'] < LISTING_CACHE_SECONDS):
        return _video_listing_cache['items']
    items = graph.list_videos()
    _video_listing_cache.update(key=key, loaded_at=now, items=items)
    return items


def analyze_item(job, graph, item):
    name = str(item.get('name') or 'SharePoint video')[:300]
    source_url = str(item.get('webUrl') or '')
    drive_id = str(item.get('driveId') or getattr(graph, 'drive_id', ''))
    item_id = str(item.get('id') or '')
    version = item_version(item)
    existing = Video.objects.filter(
        owner=job.owner, source_type='sharepoint', source_drive_id=drive_id, source_item_id=item_id,
    ).first() if item_id else None
    if not existing and source_url:
        existing = Video.objects.filter(owner=job.owner, source_url=source_url).first()
    if existing and existing.status == 'indexed':
        return 'skipped', None

    video = existing or Video.objects.create(
        owner=job.owner, title=os.path.splitext(name)[0][:300], source_url=source_url,
        source_type='sharepoint', source_folder=str(item.get('sourceFolder') or job.folder_name)[:500],
        status='processing', analysis_mode='ai',
    )
    changed_source = bool(existing and source_version_changed(existing, item))
    video.title = os.path.splitext(name)[0][:300]
    video.source_url = source_url
    video.source_drive_id = drive_id
    video.source_item_id = item_id
    video.source_modified_at = version['source_modified_at'] or video.source_modified_at
    video.source_size = version['source_size'] if version['source_size'] is not None else video.source_size
    video.source_etag = version['source_etag'] or video.source_etag
    video.source_ctag = version['source_ctag'] or video.source_ctag
    if changed_source:
        video.transcript = ''
    video.status = 'processing'
    video.error_message = ''
    video.source_folder = str(item.get('sourceFolder') or job.folder_name)[:500]
    video.save(update_fields=[
        'title', 'source_url', 'source_drive_id', 'source_item_id', 'source_modified_at',
        'source_size', 'source_etag', 'source_ctag', 'transcript', 'status',
        'error_message', 'source_folder', 'updated_at',
    ])
    path = None
    try:
        transcript = video.transcript.strip()
        if not transcript:
            size = int(item.get('size') or 0)
            if size > settings.MAX_VIDEO_SIZE_BYTES:
                raise SharePointError(f'Video exceeds the configured {settings.MAX_VIDEO_SIZE_GB} GB SharePoint processing limit.')
            path = download_to_temp(graph, item)
            with open(path, 'rb') as stream:
                uploaded = File(stream, name=os.path.basename(name))
                transcript = transcribe_video(uploaded)
            # Keep the expensive transcription as a checkpoint. If AI analysis
            # is temporarily unavailable, a retry can summarize without another
            # SharePoint download or transcription pass.
            video.transcript = transcript
            video.save(update_fields=['transcript', 'updated_at'])
        result = analyze_transcript(transcript, video.title)
        video.transcript = transcript
        video.summary = result['summary']
        video.categories = result['categories']
        video.key_topics = result['keyTopics']
        video.kb_score = result['kbScore']
        video.training_ready = result['trainingReady']
        video.analysis_mode = result['analysisMode']
        video.analysis_version = settings.VIDEO_ANALYSIS_VERSION
        video.status = 'indexed'
        video.retry_count = 0
        video.analysis_completed_at = timezone.now()
        video.save()
        return 'imported', None
    except (SharePointError, OpenAIError, RuntimeError, ValueError, OSError, subprocess.TimeoutExpired) as error:
        video.status = 'failed'
        video.retry_count += 1
        if isinstance(error, OpenAIError):
            code = getattr(error, 'status_code', None)
            error_type = type(error).__name__
            if error_type == 'AuthenticationError' or code == 401:
                message = 'OpenAI rejected the backend API key. Replace it with a valid key and restart the worker.'
            elif error_type == 'PermissionDeniedError' or code == 403:
                message = 'OpenAI denied this request. Check project access and transcription model permissions.'
            elif error_type == 'RateLimitError' or code == 429:
                message = openai_rate_limit_message(error)
            elif error_type in {'APIConnectionError', 'APITimeoutError'}:
                message = 'The worker could not connect to OpenAI. Check outbound network, DNS, and proxy access from the worker host.'
            elif code and code >= 500:
                message = 'OpenAI returned a temporary service error. Retry this video later.'
            else:
                message = f'OpenAI request failed ({error_type}{f", HTTP {code}" if code else ""}). Check worker connectivity and model configuration.'
        else:
            message = str(error) if isinstance(error, (SharePointError, RuntimeError, ValueError)) else 'Video analysis failed. Check the backend configuration and connectivity.'
        video.error_message = message[:500]
        video.save(update_fields=['status', 'error_message', 'updated_at'])
        logger.warning('SharePoint video analysis failed for item id=%s exception=%s status=%s', item.get('id', ''), type(error).__name__, getattr(error, 'status_code', None))
        retryable = (
            isinstance(error, (OSError, subprocess.TimeoutExpired))
            or isinstance(error, SharePointError) and error.retryable
            or isinstance(error, OpenAIError) and (
                type(error).__name__ in {'APIConnectionError', 'APITimeoutError', 'RateLimitError'}
                or getattr(error, 'status_code', 0) in (408, 429)
                or (getattr(error, 'status_code', 0) or 0) >= 500
            )
            or isinstance(error, SharePointError) and any(
                marker in str(error).lower() for marker in ('throttl', 'temporar', 'connect', 'timeout')
            )
        )
        return 'failed', {'title': name, 'detail': message[:300], 'retryable': retryable}
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass


def register_pending_items(job, graph, items, videos_by_identity, videos_by_url):
    for item in items:
        identity = (str(item.get('driveId') or getattr(graph, 'drive_id', '')), str(item.get('id') or ''))
        video = videos_by_identity.get(identity) or videos_by_url.get(str(item.get('webUrl') or ''))
        if not video:
            Video.objects.create(
                owner=job.owner,
                title=os.path.splitext(str(item.get('name') or 'SharePoint video'))[0][:300],
                source_url=str(item.get('webUrl') or ''),
                source_type='sharepoint',
                source_folder=str(item.get('sourceFolder') or job.folder_name)[:500],
                source_drive_id=identity[0],
                source_item_id=identity[1],
                **item_version(item),
                status='pending',
                analysis_mode='ai',
            )
            continue
        if video.status == 'indexed' and source_version_changed(video, item):
            video.status = 'pending'
            video.error_message = ''
            video.save(update_fields=['status', 'error_message', 'updated_at'])


def process_job(job_id):
    close_old_connections()
    ensure_analysis_slots()
    try:
        with transaction.atomic():
            claimed = SharePointImport.objects.filter(pk=job_id, status='queued').update(
                status='processing', updated_at=timezone.now(),
            )
            if not claimed:
                return
            job = SharePointImport.objects.select_related('owner').get(pk=job_id)

        # Covers queued/manual jobs too, including jobs created before the
        # account's first dashboard request.
        copy_analyzed_sharepoint_results(job.owner)

        graph = GraphClient()
        try:
            items = cached_video_listing(graph)
            # Keep existing SharePoint records attributable to their folder too.
            # This backfills paths on older cards the next time an import runs.
            sources = {str(item.get('webUrl')): str(item.get('sourceFolder') or job.folder_name)[:500]
                       for item in items if item.get('webUrl')}
            for source_url, source_folder in sources.items():
                Video.objects.filter(owner=job.owner, source_url=source_url, source_type='sharepoint').exclude(
                    source_folder=source_folder
                ).update(source_folder=source_folder)
            job.folder_total = len(items)
            job.save(update_fields=['folder_total', 'updated_at'])
            existing_by_identity = {
                (video.source_drive_id, video.source_item_id): video
                for video in Video.objects.filter(owner=job.owner, source_type='sharepoint')
                if video.source_drive_id and video.source_item_id
            }
            existing_by_url = {
                video.source_url: video for video in Video.objects.filter(
                    owner=job.owner, source_type='sharepoint'
                ).exclude(source_url='')
            }
            for item in items:
                video = existing_by_identity.get((
                    str(item.get('driveId') or graph.drive_id), str(item.get('id') or ''),
                )) or existing_by_url.get(str(item.get('webUrl') or ''))
                if video and video.status == 'indexed' and not source_version_changed(video, item) and (
                    not video.source_item_id or not video.source_modified_at
                    or (item.get('size') is not None and video.source_size is None)
                    or (item.get('eTag') and not video.source_etag)
                    or (item.get('cTag') and not video.source_ctag)
                ):
                    video.source_drive_id = str(item.get('driveId') or graph.drive_id)
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
                        existing_by_identity[(video.source_drive_id, video.source_item_id)] = video

            def needs_analysis(item):
                video = existing_by_identity.get((
                    str(item.get('driveId') or graph.drive_id), str(item.get('id') or ''),
                )) or existing_by_url.get(str(item.get('webUrl') or ''))
                if not video:
                    return True
                if job.target_video_id and video.pk != job.target_video_id:
                    return False
                if (video.status == 'failed'
                        and video.retry_count >= settings.SHAREPOINT_MAX_VIDEO_RETRIES + 1
                        and job.target_video_id != video.pk):
                    return False
                # Keep successfully analyzed SharePoint items immutable during
                # automatic folder sync. Explicitly failed items remain eligible.
                return video.status != 'indexed'

            pending = [item for item in items if item.get('webUrl') and needs_analysis(item)]
            if job.target_video_id:
                pending = [item for item in pending if (
                    existing_by_identity.get((
                        str(item.get('driveId') or graph.drive_id), str(item.get('id') or ''),
                    )) or existing_by_url.get(str(item.get('webUrl') or ''))
                ) and (
                    (existing_by_identity.get((
                        str(item.get('driveId') or graph.drive_id), str(item.get('id') or ''),
                    )) or existing_by_url.get(str(item.get('webUrl') or ''))).pk == job.target_video_id
                )]
            if not pending and not job.target_video_id:
                # A completed folder can receive new files; refresh once on the next click.
                items = cached_video_listing(graph, force_refresh=True)
                job.folder_total = len(items)
                job.save(update_fields=['folder_total', 'updated_at'])
                pending = [item for item in items if item.get('webUrl') and needs_analysis(item)]
            register_pending_items(job, graph, pending, existing_by_identity, existing_by_url)
            # A single click processes the entire remaining folder. The selected
            # batch size controls how often we pause and report group progress.
            job.total = job.processed + len(pending)
            job.remaining = len(pending)
            job.has_more = bool(pending)
            job.save(update_fields=['total', 'remaining', 'has_more', 'updated_at'])
            batch_size = min(2, max(1, job.batch_size))
            batches = iter_batches(pending, batch_size)
            batch_count = (len(pending) + batch_size - 1) // batch_size
            for batch_number, batch in enumerate(batches, start=1):
                logger.info('SharePoint import %s starting batch %s/%s (%s videos).', job_id, batch_number, batch_count, len(batch))
                results = []
                with ThreadPoolExecutor(max_workers=min(2, len(batch))) as executor:
                    futures = {
                        executor.submit(_analyze_with_retries, job, graph, item): item
                        for item in batch
                    }
                    for future in as_completed(futures):
                        item = futures[future]
                        outcome, error = future.result()
                        results.append((item, outcome, error))
                for item, outcome, error in results:
                    job.processed += 1
                    if outcome == 'imported':
                        job.imported += 1
                    elif outcome == 'failed':
                        job.failed += 1
                        if len(job.errors) < 50:
                            job.errors.append(error)
                    job.remaining = max(0, job.total - job.processed)
                    job.has_more = job.remaining > 0
                    job.save(update_fields=['processed', 'imported', 'failed', 'errors', 'remaining', 'has_more', 'updated_at'])
                if SharePointImport.objects.filter(pk=job_id, status='cancelling').exists():
                    break
                if job.remaining and job.processed % max(1, job.batch_size) == 0:
                    logger.info('SharePoint import %s completed batch %s/%s; continuing automatically.', job_id, batch_number, batch_count)
                    time.sleep(BATCH_PAUSE_SECONDS)
            job.remaining = max(0, job.total - job.processed)
            job.has_more = job.remaining > 0
            job.save(update_fields=['remaining', 'has_more', 'updated_at'])
        finally:
            graph.close()

        job.refresh_from_db(fields=['status'])
        if job.status == 'cancelling':
            job.status = 'cancelled'
        else:
            job.status = 'completed_with_errors' if job.failed else 'completed'
        job.save(update_fields=['status', 'updated_at'])
    except SharePointError as error:
        job = SharePointImport.objects.filter(pk=job_id).first()
        if job:
            job.status = 'failed'
            job.errors = (job.errors or []) + [{'detail': str(error)[:300]}]
            job.save(update_fields=['status', 'errors', 'updated_at'])
    except Exception as error:
        logger.exception('SharePoint import failed: job=%s error=%s', job_id, type(error).__name__)
        job = SharePointImport.objects.filter(pk=job_id).first()
        if job:
            job.status = 'failed'
            job.errors = (job.errors or []) + [{
                'detail': f'The worker stopped unexpectedly ({type(error).__name__}). Check the worker log, then retry this batch.'
            }]
            job.save(update_fields=['status', 'errors', 'updated_at'])
    finally:
        close_old_connections()


def _analyze_with_retries(job, graph, item):
    close_old_connections()
    slot_number, lease_token = _acquire_analysis_slot()
    stop_heartbeat = threading.Event()
    heartbeat = threading.Thread(
        target=_heartbeat_analysis_slot,
        args=(slot_number, lease_token, stop_heartbeat),
        daemon=True,
    )
    heartbeat.start()
    try:
        current_job = SharePointImport.objects.select_related('owner').get(pk=job.pk)
        max_retries = settings.SHAREPOINT_MAX_VIDEO_RETRIES
        for attempt in range(max_retries + 1):
            outcome, error = analyze_item(current_job, graph, item)
            if outcome != 'failed' or not error or not error.get('retryable') or attempt >= max_retries:
                return outcome, error
            time.sleep(settings.SHAREPOINT_RETRY_DELAY_SECONDS)
        return outcome, error
    finally:
        stop_heartbeat.set()
        heartbeat.join(timeout=ANALYSIS_SLOT_HEARTBEAT_SECONDS + 1)
        _release_analysis_slot(slot_number, lease_token)
        close_old_connections()


def _acquire_analysis_slot():
    while True:
        with _slot_claim_lock:
            now = timezone.now()
            token = uuid.uuid4()
            for slot_number in (1, 2):
                claimed = SharePointAnalysisSlot.objects.filter(
                    Q(lease_expires_at__isnull=True) | Q(lease_expires_at__lte=now),
                    slot_number=slot_number,
                ).update(
                    lease_token=token,
                    lease_expires_at=now + timedelta(seconds=ANALYSIS_SLOT_LEASE_SECONDS),
                )
                if claimed:
                    return slot_number, token
        time.sleep(0.5)


def ensure_analysis_slots():
    for slot_number in (1, 2):
        SharePointAnalysisSlot.objects.get_or_create(slot_number=slot_number)


def _heartbeat_analysis_slot(slot_number, lease_token, stop_event):
    while not stop_event.wait(ANALYSIS_SLOT_HEARTBEAT_SECONDS):
        close_old_connections()
        renewed = SharePointAnalysisSlot.objects.filter(
            slot_number=slot_number, lease_token=lease_token,
        ).update(
            lease_expires_at=timezone.now() + timedelta(seconds=ANALYSIS_SLOT_LEASE_SECONDS),
        )
        if not renewed:
            logger.error('SharePoint analysis slot lease was lost: slot=%s', slot_number)
            return
    close_old_connections()


def _release_analysis_slot(slot_number, lease_token):
    SharePointAnalysisSlot.objects.filter(
        slot_number=slot_number, lease_token=lease_token,
    ).update(lease_token=None, lease_expires_at=None)


def recover_interrupted_jobs():
    now = timezone.now()
    if SharePointAnalysisSlot.objects.filter(lease_expires_at__gt=now).exists():
        return
    interrupted = SharePointImport.objects.filter(
        status='processing',
        updated_at__lt=now - timedelta(minutes=15),
    )
    if not interrupted.exists():
        return
    Video.objects.filter(status='processing').update(
        status='failed', error_message='Worker restarted during analysis. This video will be retried.',
    )
    interrupted.update(status='queued', updated_at=now)
    active_owner_ids = SharePointImport.objects.filter(
    status__in=['queued', 'processing', 'cancelling'],
    ).values('owner_id')
    Video.objects.filter(status='processing').exclude(owner_id__in=active_owner_ids).update(
    status='failed',
    error_message='The previous import stopped during analysis. Retry this video to resume.',
    )


def run_scheduled_sync_if_due():
    state = SharePointSyncState.objects.filter(pk=1).first()
    if state and state.next_sync_at and state.next_sync_at > timezone.now():
        return
    try:
        call_command('sync_sharepoint_folder')
    except CommandError:
        logger.exception('Scheduled SharePoint folder sync failed.')


class Command(BaseCommand):
    help = 'Process queued SharePoint video folder imports.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true', help='Process at most one queued import and exit.')
        parser.add_argument('--poll-seconds', type=int, default=5, help='Seconds between queue checks.')

    def handle(self, *args, **options):
        poll_seconds = max(1, options['poll_seconds'])
        recover_interrupted_jobs()
        self.stdout.write('SharePoint import worker started.')
        while True:
            close_old_connections()
            run_scheduled_sync_if_due()
            recover_interrupted_jobs()
            job = SharePointImport.objects.filter(status='queued').order_by('created_at').first()
            if job:
                self.stdout.write(f'Processing SharePoint import {job.id}.')
                process_job(job.id)
                self.stdout.write(f'Finished SharePoint import {job.id}.')
                if options['once']:
                    return
            elif options['once']:
                self.stdout.write('No queued SharePoint imports.')
                return
            else:
                time.sleep(poll_seconds)

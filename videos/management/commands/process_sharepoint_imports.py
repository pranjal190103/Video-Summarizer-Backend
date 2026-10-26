import logging
import os
import subprocess
import time
import tempfile

from django.core.files import File
from django.core.management.base import BaseCommand
from django.conf import settings
from django.db import close_old_connections, transaction
from django.utils import timezone
from openai import OpenAIError

from videos.models import SharePointImport, Video
from videos.services import analyze_transcript, transcribe_video
from videos.sharepoint import GraphClient, SharePointError, download_to_temp
from videos.openai_errors import openai_rate_limit_message

logger = logging.getLogger(__name__)
LISTING_CACHE_SECONDS = 300
_video_listing_cache = {'key': None, 'loaded_at': 0.0, 'items': None}


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
    existing = Video.objects.filter(owner=job.owner, source_url=source_url).first() if source_url else None
    if existing and existing.status == 'indexed':
        return 'skipped', None

    video = existing or Video.objects.create(
        owner=job.owner, title=os.path.splitext(name)[0][:300], source_url=source_url,
        source_type='sharepoint', status='processing', analysis_mode='ai',
    )
    video.status = 'processing'
    video.error_message = ''
    video.save(update_fields=['status', 'error_message', 'updated_at'])
    path = None
    try:
        size = int(item.get('size') or 0)
        if size > settings.MAX_VIDEO_SIZE_BYTES:
            raise SharePointError(f'Video exceeds the {settings.MAX_VIDEO_SIZE_GB} GB processing limit.')
        path = download_to_temp(graph, item)
        with open(path, 'rb') as stream:
            uploaded = File(stream, name=os.path.basename(name))
            transcript = transcribe_video(uploaded)
        result = analyze_transcript(transcript, video.title)
        video.transcript = transcript
        video.summary = result['summary']
        video.categories = result['categories']
        video.key_topics = result['keyTopics']
        video.kb_score = result['kbScore']
        video.training_ready = result['trainingReady']
        video.analysis_mode = result['analysisMode']
        video.status = 'indexed'
        video.save()
        return 'imported', None
    except (SharePointError, OpenAIError, RuntimeError, ValueError, OSError, subprocess.TimeoutExpired) as error:
        video.status = 'failed'
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
        return 'failed', {'title': name, 'detail': message[:300]}
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass


def process_job(job_id):
    close_old_connections()
    try:
        with transaction.atomic():
            claimed = SharePointImport.objects.filter(pk=job_id, status='queued').update(
                status='processing', updated_at=timezone.now(),
            )
            if not claimed:
                return
            job = SharePointImport.objects.select_related('owner').get(pk=job_id)

        graph = GraphClient()
        try:
            items = cached_video_listing(graph)
            job.folder_total = len(items)
            job.save(update_fields=['folder_total', 'updated_at'])
            already_seen = set(Video.objects.filter(
                owner=job.owner, status='indexed'
            ).exclude(source_url='').values_list('source_url', flat=True))
            pending = [item for item in items
                       if item.get('webUrl') and item['webUrl'] not in already_seen]
            if not pending:
                # A completed folder can receive new files; refresh once on the next click.
                items = cached_video_listing(graph, force_refresh=True)
                job.folder_total = len(items)
                job.save(update_fields=['folder_total', 'updated_at'])
                pending = [item for item in items
                           if item.get('webUrl') and item['webUrl'] not in already_seen]
            # Process only the user-selected number of videos per click. Each
            # result is persisted before continuing, keeping slow servers responsive.
            batch = pending[:job.batch_size]
            job.total = len(batch)
            job.save(update_fields=['total', 'updated_at'])
            for item in batch:
                if SharePointImport.objects.filter(pk=job_id, status='cancelling').exists():
                    break
                outcome, error = analyze_item(job, graph, item)
                job.processed += 1
                if outcome == 'imported':
                    job.imported += 1
                elif outcome == 'failed':
                    job.failed += 1
                    if len(job.errors) < 50:
                        job.errors.append(error)
                job.save(update_fields=['processed', 'imported', 'failed', 'errors', 'updated_at'])
                source_url = str(item.get('webUrl') or '')
                if source_url:
                    already_seen.add(source_url)
            job.remaining = max(0, len(pending) - job.processed)
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
            job.errors = (job.errors or []) + [{'detail': 'The worker stopped unexpectedly. Check the worker log, then retry this batch.'}]
            job.save(update_fields=['status', 'errors', 'updated_at'])
    finally:
        close_old_connections()


class Command(BaseCommand):
    help = 'Process queued SharePoint video folder imports.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true', help='Process at most one queued import and exit.')
        parser.add_argument('--poll-seconds', type=int, default=5, help='Seconds between queue checks.')

    def handle(self, *args, **options):
        poll_seconds = max(1, options['poll_seconds'])
        self.stdout.write('SharePoint import worker started.')
        while True:
            close_old_connections()
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

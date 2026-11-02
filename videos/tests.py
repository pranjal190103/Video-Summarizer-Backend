from unittest.mock import patch
from io import StringIO
import os
import threading
import tempfile
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

import threading
import time
import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TransactionTestCase
from rest_framework.test import APIClient
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from videos.management.commands.process_sharepoint_imports import (
    _acquire_analysis_slot, _analyze_with_retries, _release_analysis_slot, analyze_item,
    ensure_analysis_slots, iter_batches, process_job,
)
from videos.management.commands.sync_sharepoint_folder import item_is_current
from videos.models import SharePointImport, SharePointSyncState, Video
from videos.sharepoint import SharePointError


class SharePointAutomaticBatchTests(TransactionTestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(
            username='batch-test-user', password='test-password-12345',
        )

    def test_selected_sizes_split_all_items_into_sequential_groups(self):
        items = list(range(7))
        self.assertEqual(list(iter_batches(items, 2)), [[0, 1], [2, 3], [4, 5], [6]])
        self.assertEqual(list(iter_batches(items, 3)), [[0, 1, 2], [3, 4, 5], [6]])
        self.assertEqual(list(iter_batches(items, 5)), [[0, 1, 2, 3, 4], [5, 6]])

    def test_analysis_slot_limit_is_shared_across_worker_threads(self):
        ensure_analysis_slots()
        with ThreadPoolExecutor(max_workers=2) as executor:
            claims = list(executor.map(lambda _index: _acquire_analysis_slot(), range(2)))
            self.assertEqual({slot for slot, _token in claims}, {1, 2})
            third_claim = executor.submit(_acquire_analysis_slot)
            completed, _pending = wait([third_claim], timeout=0.1, return_when=FIRST_COMPLETED)
            self.assertFalse(completed)
            _release_analysis_slot(*claims[0])
            third = third_claim.result(timeout=2)
            self.assertEqual(third[0], claims[0][0])
            _release_analysis_slot(*third)
            _release_analysis_slot(*claims[1])

    @patch('videos.management.commands.sync_sharepoint_folder.GraphClient')
    def test_scheduled_sync_registers_discovered_videos_and_persists_a_job(self, graph_client):
        graph_client.return_value.list_videos.return_value = [{
            'id': 'new-item',
            'driveId': 'test-drive',
            'name': 'new-training.mp4',
            'webUrl': 'https://example.test/new-training',
            'sourceFolder': 'Training videos',
            'lastModifiedDateTime': '2026-10-28T12:00:00Z',
        }]

        call_command('sync_sharepoint_folder', stdout=StringIO())

        video = Video.objects.get(owner=self.owner, source_item_id='new-item')
        job = SharePointImport.objects.get(owner=self.owner)
        state = SharePointSyncState.objects.get(pk=1)
        self.assertEqual(video.status, 'pending')
        self.assertEqual(job.status, 'queued')
        self.assertEqual(job.trigger, 'scheduled')
        self.assertEqual(state.discovered_count, 1)
        self.assertIsNotNone(state.last_success_at)
        self.assertIsNotNone(state.next_sync_at)

        call_command('sync_sharepoint_folder', stdout=StringIO())
        self.assertEqual(SharePointImport.objects.filter(owner=self.owner).count(), 1)

    @patch('videos.management.commands.sync_sharepoint_folder.GraphClient')
    def test_active_sync_lease_prevents_overlapping_discovery(self, graph_client):
        SharePointSyncState.objects.create(
            pk=1, lease_token=uuid.uuid4(), lease_expires_at=timezone.now() + timedelta(minutes=5),
        )
        call_command('sync_sharepoint_folder', stdout=StringIO())
        graph_client.assert_not_called()

    @patch('videos.management.commands.sync_sharepoint_folder.GraphClient')
    def test_failed_sync_preserves_last_success_checkpoint(self, graph_client):
        last_success = timezone.now() - timedelta(hours=2)
        SharePointSyncState.objects.create(pk=1, last_success_at=last_success)
        graph_client.side_effect = SharePointError('Temporary Graph outage.', retryable=True)
        with self.settings(SHAREPOINT_SYNC_MAX_RETRIES=0):
            with self.assertRaises(CommandError):
                call_command('sync_sharepoint_folder', stdout=StringIO())
        state = SharePointSyncState.objects.get(pk=1)
        self.assertEqual(state.last_success_at, last_success)
        self.assertTrue(state.last_error)
        self.assertIsNone(state.lease_token)

    def test_exhausted_video_failure_is_not_retried_by_daily_sync(self):
        video = Video.objects.create(
            owner=self.owner, title='Needs manual retry', source_type='sharepoint',
            source_drive_id='test-drive', source_item_id='failed-item',
            status='failed', retry_count=3,
        )
        item = {'id': 'failed-item', 'driveId': 'test-drive', 'webUrl': 'https://example.test/failed'}
        with self.settings(SHAREPOINT_MAX_VIDEO_RETRIES=2):
            self.assertTrue(item_is_current(item, {('test-drive', 'failed-item'): video}, {}))

    @patch('videos.management.commands.process_sharepoint_imports.analyze_transcript')
    @patch('videos.management.commands.process_sharepoint_imports.transcribe_video')
    @patch('videos.management.commands.process_sharepoint_imports.download_to_temp')
    def test_sharepoint_video_over_500mb_is_processed_with_audio_chunking(
        self, download_to_temp_mock, transcribe_mock, analyze_mock,
    ):
        job = SharePointImport.objects.create(
            owner=self.owner, folder_name='Training videos', batch_size=2,
        )
        with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as video_file:
            video_path = video_file.name
        self.addCleanup(lambda: os.path.exists(video_path) and os.unlink(video_path))
        download_to_temp_mock.return_value = video_path
        transcribe_mock.return_value = 'A substantive training transcript.'
        analyze_mock.return_value = {
            'summary': 'A useful training summary.',
            'categories': ['Content'],
            'keyTopics': ['Training'],
            'kbScore': 85,
            'trainingReady': True,
            'analysisMode': 'ai',
        }
        item = {
            'id': 'large-video',
            'name': 'large-training-video.mp4',
            'webUrl': 'https://example.test/large-training-video',
            'size': 600 * 1024 * 1024,
        }

        outcome, error = analyze_item(job, object(), item)

        self.assertEqual((outcome, error), ('imported', None))
        video = Video.objects.get(owner=self.owner, source_url=item['webUrl'])
        self.assertEqual(video.status, 'indexed')
        self.assertTrue(video.training_ready)
        transcribe_mock.assert_called_once()
        analyze_mock.assert_called_once_with('A substantive training transcript.', video.title)

    @patch('videos.management.commands.process_sharepoint_imports.analyze_transcript')
    @patch('videos.management.commands.process_sharepoint_imports.transcribe_video')
    @patch('videos.management.commands.process_sharepoint_imports.download_to_temp')
    def test_retry_reuses_saved_transcript_after_analysis_failure(
        self, download_to_temp_mock, transcribe_mock, analyze_mock,
    ):
        job = SharePointImport.objects.create(
            owner=self.owner, folder_name='Training videos', batch_size=2,
        )
        video = Video.objects.create(
            owner=self.owner,
            title='Previously transcribed video',
            source_url='https://example.test/retry-video',
            source_type='sharepoint',
            status='failed',
            transcript='Transcript saved before the temporary AI error.',
        )
        analyze_mock.return_value = {
            'summary': 'The recovered training summary.',
            'categories': ['Content'],
            'keyTopics': ['Retry'],
            'kbScore': 82,
            'trainingReady': True,
            'analysisMode': 'ai',
        }
        item = {
            'id': 'retry-video',
            'name': 'retry-video.mp4',
            'webUrl': video.source_url,
            'size': 700 * 1024 * 1024,
        }

        outcome, error = analyze_item(job, object(), item)

        self.assertEqual((outcome, error), ('imported', None))
        video.refresh_from_db()
        self.assertEqual(video.status, 'indexed')
        self.assertTrue(video.training_ready)
        download_to_temp_mock.assert_not_called()
        transcribe_mock.assert_not_called()
        analyze_mock.assert_called_once_with(video.transcript, video.title)

    @patch('videos.management.commands.process_sharepoint_imports.time.sleep')
    @patch('videos.management.commands.process_sharepoint_imports.analyze_item')
    def test_transient_video_failure_is_retried_automatically(self, analyze_item_mock, _sleep):
        job = SharePointImport.objects.create(
            owner=self.owner, folder_name='Training videos', batch_size=2,
        )
        ensure_analysis_slots()
        analyze_item_mock.side_effect = [
            ('failed', {'title': 'temporary.mp4', 'detail': 'Temporary AI outage.', 'retryable': True}),
            ('imported', None),
        ]

        with self.settings(SHAREPOINT_MAX_VIDEO_RETRIES=1, SHAREPOINT_RETRY_DELAY_SECONDS=0):
            outcome, error = _analyze_with_retries(job, object(), {'id': 'temporary'})

        self.assertEqual((outcome, error), ('imported', None))
        self.assertEqual(analyze_item_mock.call_count, 2)

    @patch('videos.management.commands.process_sharepoint_imports.analyze_transcript')
    @patch('videos.management.commands.process_sharepoint_imports.transcribe_video')
    @patch('videos.management.commands.process_sharepoint_imports.download_to_temp')
    def test_changed_sharepoint_source_that_was_analyzed_is_not_reanalyzed(
        self, download_to_temp_mock, transcribe_mock, analyze_mock,
    ):
        job = SharePointImport.objects.create(owner=self.owner, folder_name='Training videos')
        video = Video.objects.create(
            owner=self.owner,
            title='Changed training video',
            source_url='https://example.test/changed-video',
            source_type='sharepoint',
            source_drive_id='test-drive',
            source_item_id='changed-video',
            source_modified_at='2026-10-27T12:00:00Z',
            source_ctag='old-content-version',
            transcript='Old transcript.',
            status='indexed',
        )
        outcome, error = analyze_item(job, type('Graph', (), {'drive_id': 'test-drive'})(), {
            'id': 'changed-video',
            'driveId': 'test-drive',
            'name': 'changed-training.mp4',
            'webUrl': video.source_url,
            'cTag': 'new-content-version',
            'lastModifiedDateTime': '2026-10-28T12:00:00Z',
        })

        video.refresh_from_db()
        self.assertEqual((outcome, error), ('skipped', None))
        self.assertEqual(video.transcript, 'Old transcript.')
        self.assertEqual(video.summary, '')
        self.assertEqual(video.status, 'indexed')
        download_to_temp_mock.assert_not_called()
        transcribe_mock.assert_not_called()
        analyze_mock.assert_not_called()

    @patch('videos.management.commands.process_sharepoint_imports.analyze_transcript')
    @patch('videos.management.commands.process_sharepoint_imports.transcribe_video')
    @patch('videos.management.commands.process_sharepoint_imports.download_to_temp')
    def test_unchanged_sharepoint_ctag_skips_download_and_ai(self, download_mock, transcribe_mock, analyze_mock):
        job = SharePointImport.objects.create(owner=self.owner, folder_name='Training videos')
        video = Video.objects.create(
            owner=self.owner,
            title='Already analyzed',
            source_url='https://example.test/unchanged',
            source_type='sharepoint',
            source_drive_id='test-drive',
            source_item_id='unchanged',
            source_modified_at='2026-10-28T12:00:00Z',
            source_size=1024,
            source_ctag='content-version-1',
            transcript='Saved transcript.',
            summary='Saved result.',
            status='indexed',
        )

        outcome, error = analyze_item(job, type('Graph', (), {'drive_id': 'test-drive'})(), {
            'id': 'unchanged', 'driveId': 'test-drive', 'name': 'unchanged.mp4',
            'webUrl': video.source_url, 'size': 1024,
            'cTag': 'content-version-1', 'lastModifiedDateTime': '2026-10-28T12:00:00Z',
        })

        self.assertEqual((outcome, error), ('skipped', None))
        download_mock.assert_not_called()
        transcribe_mock.assert_not_called()
        analyze_mock.assert_not_called()

    @patch('videos.views.analyze_transcript')
    def test_reanalyze_endpoint_rejects_successful_sharepoint_video(self, analyze_mock):
        video = Video.objects.create(
            owner=self.owner, title='Do not reanalyze', source_type='sharepoint',
            source_drive_id='test-drive', source_item_id='already-indexed',
            transcript='Saved transcript.', summary='Saved summary.', status='indexed',
        )
        client = APIClient()
        client.force_authenticate(user=self.owner)

        response = client.post(f'/api/videos/{video.id}/reanalyze/')

        self.assertEqual(response.status_code, 409)
        analyze_mock.assert_not_called()

    def test_local_vite_origin_is_allowed_for_auth_preflight(self):
        client = APIClient()
        response = client.options(
            '/api/auth/login/',
            HTTP_ORIGIN='http://127.0.0.1:5175',
            HTTP_ACCESS_CONTROL_REQUEST_METHOD='POST',
            HTTP_ACCESS_CONTROL_REQUEST_HEADERS='content-type,authorization',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Access-Control-Allow-Origin'], 'http://127.0.0.1:5175')

    def test_sync_now_endpoint_marks_the_next_scan_due(self):
        state = SharePointSyncState.objects.create(
            pk=1, next_sync_at=timezone.now() + timedelta(hours=12),
        )
        client = APIClient()
        client.force_authenticate(user=self.owner)

        with self.settings(
            MS_GRAPH_TENANT_ID='tenant', MS_GRAPH_CLIENT_ID='client',
            MS_GRAPH_CLIENT_SECRET='secret', MS_GRAPH_DRIVE_ID='drive',
            MS_GRAPH_FOLDER_ITEM_ID='folder',
        ):
            response = client.post('/api/sharepoint/sync/now/', {}, format='json')

        state.refresh_from_db()
        self.assertEqual(response.status_code, 202)
        self.assertLessEqual(state.next_sync_at, timezone.now())
        self.assertTrue(response.data['queued'])


    @patch('videos.management.commands.process_sharepoint_imports.time.sleep')
    @patch('videos.management.commands.process_sharepoint_imports.analyze_item')
    @patch('videos.management.commands.process_sharepoint_imports.cached_video_listing')
    @patch('videos.management.commands.process_sharepoint_imports.GraphClient')
    def test_one_job_processes_all_videos_and_persists_each_result(
        self, graph_client, cached_listing, analyze_item_mock, _sleep,
    ):
        job = SharePointImport.objects.create(
            owner=self.owner, folder_name='Training videos', batch_size=2,
        )
        items = [
            {'id': str(index), 'name': f'video-{index}.mp4', 'webUrl': f'https://example.test/{index}'}
            for index in range(5)
        ]
        cached_listing.return_value = items
        activity = {'current': 0, 'maximum': 0}
        activity_lock = threading.Lock()
        batch_barrier = threading.Barrier(2)

        def analyze_concurrently(_job, _graph, item):
            with activity_lock:
                activity['current'] += 1
                activity['maximum'] = max(activity['maximum'], activity['current'])
            if item['id'] in {'0', '1', '2', '3'}:
                batch_barrier.wait(timeout=2)
            with activity_lock:
                activity['current'] -= 1
            return 'imported', None

        analyze_item_mock.side_effect = analyze_concurrently

        process_job(job.id)

        job.refresh_from_db()
        self.assertEqual(job.status, 'completed')
        self.assertEqual(job.total, 5)
        self.assertEqual(job.processed, 5)
        self.assertEqual(job.imported, 5)
        self.assertEqual(job.remaining, 0)
        self.assertFalse(job.has_more)
        self.assertEqual(analyze_item_mock.call_count, 5)
        self.assertEqual(activity['maximum'], 2)
        self.assertEqual(
            {call.args[2]['id'] for call in analyze_item_mock.call_args_list},
            {'0', '1', '2', '3', '4'},
        )
        graph_client.return_value.close.assert_called_once()

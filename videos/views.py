import subprocess
import logging
from openai import OpenAIError
from rest_framework import generics, status
from rest_framework.decorators import api_view, permission_classes, throttle_classes, throttle_scope
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from django.db import transaction
from django.conf import settings
from .models import SharePointImport, Video
from .serializers import VideoDetailSerializer, VideoSerializer
from .services import analyze_transcript, transcribe_video
from .sharepoint import is_configured
from .openai_errors import openai_rate_limit_message

logger = logging.getLogger(__name__)

def openai_failure(error, stage):
    error_type = type(error).__name__
    code = getattr(error, 'status_code', None)
    logger.error('OpenAI %s failed: exception=%s status=%s', stage, error_type, code)
    if error_type == 'AuthenticationError' or code == 401:
        detail = 'OpenAI rejected the API key. Verify OPENAI_API_KEY in backend/.env, then restart Django.'
    elif error_type == 'PermissionDeniedError' or code == 403:
        detail = f'OpenAI denied access during {stage}. Check project access and model permissions.'
    elif error_type == 'RateLimitError' or code == 429:
        detail = f'{openai_rate_limit_message(error)} This occurred during {stage}.'
    elif code == 400:
        detail = f'OpenAI rejected the {stage} request. Check the media format, duration, and model settings.'
    elif code and code >= 500:
        detail = f'OpenAI had a temporary service error during {stage}. Try again shortly.'
    else:
        detail = f'OpenAI {stage} failed ({error_type}{f", HTTP {code}" if code else ""}). Check backend connectivity and configuration.'
    return Response({'detail': detail}, status=status.HTTP_502_BAD_GATEWAY)

@api_view(['GET'])
@permission_classes([AllowAny])
def health(request):
    return Response({'status': 'ok', 'service': 'video-intelligence-api', 'openaiConfigured': bool(settings.OPENAI_API_KEY), 'sharepointConfigured': is_configured(), 'sharepointFolderName': settings.MS_GRAPH_FOLDER_NAME})


def sharepoint_import_payload(job):
    return {
        'id': str(job.id), 'folderName': job.folder_name, 'status': job.status,
        'total': job.total, 'folderTotal': job.folder_total, 'processed': job.processed, 'imported': job.imported,
        'failed': job.failed, 'errors': job.errors, 'batchSize': job.batch_size,
        'remaining': job.remaining, 'hasMore': job.has_more,
        'createdAt': job.created_at, 'updatedAt': job.updated_at,
    }


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def sharepoint_import_list(request):
    jobs = SharePointImport.objects.filter(owner=request.user)[:10]
    return Response([sharepoint_import_payload(job) for job in jobs])


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([ScopedRateThrottle])
@throttle_scope('sharepoint-start')
def sharepoint_imports(request):
    if not is_configured():
        return Response({'detail': 'SharePoint is not configured. Add the Microsoft Graph settings to the Django backend.'}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
    active = SharePointImport.objects.filter(owner=request.user, status__in=['queued', 'processing']).first()
    if active:
        return Response(sharepoint_import_payload(active), status=status.HTTP_202_ACCEPTED)
    try:
        batch_size = int(request.data.get('batchSize', 2))
    except (TypeError, ValueError):
        return Response({'detail': 'Batch size must be a number between 1 and 50.'}, status=status.HTTP_400_BAD_REQUEST)
    if not 1 <= batch_size <= 50:
        return Response({'detail': 'Batch size must be between 1 and 50 videos.'}, status=status.HTTP_400_BAD_REQUEST)
    job = SharePointImport.objects.create(
        owner=request.user, folder_name=settings.MS_GRAPH_FOLDER_NAME, batch_size=batch_size,
    )
    return Response(sharepoint_import_payload(job), status=status.HTTP_202_ACCEPTED)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def sharepoint_import_detail(request, pk):
    try:
        job = SharePointImport.objects.get(pk=pk, owner=request.user)
    except SharePointImport.DoesNotExist:
        return Response({'detail': 'SharePoint import was not found.'}, status=status.HTTP_404_NOT_FOUND)
    return Response(sharepoint_import_payload(job))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def cancel_sharepoint_import(request, pk):
    try:
        job = SharePointImport.objects.get(pk=pk, owner=request.user)
    except SharePointImport.DoesNotExist:
        return Response({'detail': 'SharePoint import was not found.'}, status=status.HTTP_404_NOT_FOUND)
    if job.status == 'queued':
        job.status = 'cancelled'
        job.save(update_fields=['status', 'updated_at'])
    elif job.status == 'processing':
        job.status = 'cancelling'
        job.save(update_fields=['status', 'updated_at'])
    code = status.HTTP_202_ACCEPTED if job.status in ('cancelling', 'cancelled') else status.HTTP_200_OK
    return Response(sharepoint_import_payload(job), status=code)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def clear_video_library(request):
    if SharePointImport.objects.filter(
            owner=request.user, status__in=['queued', 'processing', 'cancelling']).exists():
        return Response(
            {'detail': 'Stop the active SharePoint import and wait for it to stop before clearing the library.'},
            status=status.HTTP_409_CONFLICT,
        )
    queryset = Video.objects.filter(owner=request.user)
    with transaction.atomic():
        deleted = queryset.count()
        queryset.delete()
    return Response({'deleted': deleted})

class VideoListCreateView(generics.ListCreateAPIView):
    serializer_class = VideoSerializer

    def get_queryset(self):
        return Video.objects.filter(owner=self.request.user)

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)

class VideoDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = VideoDetailSerializer

    def get_queryset(self):
        return Video.objects.filter(owner=self.request.user)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([ScopedRateThrottle])
@throttle_scope('ai')
def analyze(request):
    title = str(request.data.get('title', '')).strip()
    transcript = str(request.data.get('transcript', '')).strip()
    if not title or not transcript:
        return Response({'detail': 'Both title and transcript are required.'}, status=status.HTTP_400_BAD_REQUEST)
    if len(title) > 300:
        return Response({'detail': 'Title must be 300 characters or fewer.'}, status=status.HTTP_400_BAD_REQUEST)
    try:
        return Response(analyze_transcript(transcript, title))
    except RuntimeError as error:
        return Response({'detail': str(error)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
    except OpenAIError as error:
        return openai_failure(error, 'transcript analysis')
    except (ValueError, TypeError) as error:
        return Response({'detail': str(error)}, status=status.HTTP_502_BAD_GATEWAY)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([ScopedRateThrottle])
@throttle_scope('ai')
def reanalyze(request, pk):
    try:
        video = Video.objects.get(pk=pk, owner=request.user)
    except Video.DoesNotExist:
        return Response({'detail': 'Video not found.'}, status=status.HTTP_404_NOT_FOUND)
    if not video.transcript:
        return Response({'detail': 'This video has no stored transcript to analyze.'}, status=status.HTTP_400_BAD_REQUEST)
    try:
        result = analyze_transcript(video.transcript, video.title)
        for key, field in [('summary', 'summary'), ('categories', 'categories'), ('keyTopics', 'key_topics'), ('kbScore', 'kb_score'), ('trainingReady', 'training_ready'), ('analysisMode', 'analysis_mode')]:
            setattr(video, field, result[key])
        video.status = 'indexed'
        video.save()
        from .serializers import VideoSerializer
        return Response(VideoSerializer(video).data)
    except RuntimeError as error:
        return Response({'detail': str(error)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
    except OpenAIError as error:
        return openai_failure(error, 'transcript analysis')

@api_view(['POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([ScopedRateThrottle])
@throttle_scope('ai')
def upload_video(request):
    uploaded = request.FILES.get('file')
    title = str(request.data.get('title', '')).strip() or (uploaded.name.rsplit('.', 1)[0] if uploaded else '')
    if not uploaded:
        return Response({'detail': 'Choose a video file to upload.'}, status=status.HTTP_400_BAD_REQUEST)
    extension = uploaded.name.lower().rsplit('.', 1)[-1] if '.' in uploaded.name else ''
    if extension not in {'mp4', 'mov', 'webm'}:
        return Response({'detail': 'Supported video formats are MP4, MOV, and WEBM.'}, status=status.HTTP_400_BAD_REQUEST)
    try:
        transcript = transcribe_video(uploaded)
    except OpenAIError as error:
        return openai_failure(error, 'video transcription')
    except RuntimeError as error:
        return Response({'detail': str(error)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        return Response({'detail': str(error) or 'Could not extract audio from this video.'}, status=status.HTTP_400_BAD_REQUEST)
    try:
        result = analyze_transcript(transcript, title)
    except OpenAIError as error:
        return openai_failure(error, 'video summarization')
    except RuntimeError as error:
        return Response({'detail': str(error)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
    except (ValueError, TypeError) as error:
        return Response({'detail': str(error)}, status=status.HTTP_502_BAD_GATEWAY)
    try:
        video = Video.objects.create(owner=request.user, title=title, source_url=str(request.data.get('sourceUrl', '')), source_type='upload', transcript=transcript,
            summary=result['summary'], categories=result['categories'], key_topics=result['keyTopics'],
            kb_score=result['kbScore'], training_ready=result['trainingReady'], status='indexed', analysis_mode=result['analysisMode'])
        from .serializers import VideoSerializer
        return Response(VideoSerializer(video).data, status=status.HTTP_201_CREATED)
    except RuntimeError as error:
        return Response({'detail': str(error)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
    except (ValueError, TypeError, OSError, subprocess.TimeoutExpired) as error:
        return Response({'detail': str(error) or 'Video processing failed.'}, status=status.HTTP_400_BAD_REQUEST)

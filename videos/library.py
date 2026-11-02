"""Helpers for making completed shared SharePoint results visible per account."""
from django.conf import settings
from django.db import transaction
from django.db.models import Case, IntegerField, Value, When

from .models import Video


COPY_FIELDS = (
    'title', 'source_url', 'source_folder', 'source_drive_id', 'source_item_id',
    'source_modified_at', 'source_size', 'source_etag', 'source_ctag',
    'summary', 'transcript', 'categories', 'key_topics', 'duration', 'language',
    'kb_score', 'training_ready', 'status', 'error_message', 'retry_count',
    'analysis_completed_at', 'analysis_mode', 'analysis_version',
)


def _identity(video):
    if video.source_drive_id and video.source_item_id:
        return ('item', video.source_drive_id, video.source_item_id)
    if video.source_item_id:
        return ('item', '', video.source_item_id)
    if video.source_url:
        return ('url', video.source_url)
    return None


def _copy_values(source):
    return {field: getattr(source, field) for field in COPY_FIELDS}


def copy_analyzed_sharepoint_results(user):
    """Copy existing analyzed SharePoint results into this user's library.

    SharePoint is one configured team folder, so its completed analysis is
    reusable by every account. This copies persisted output only; it never
    downloads a source video or calls OpenAI.
    """
    owned = list(Video.objects.filter(owner=user, source_type='sharepoint'))
    owned_by_identity = {
        identity: video for video in owned
        if (identity := _identity(video)) is not None
    }
    shared = Video.objects.filter(
        source_type='sharepoint', status__in=['indexed', 'failed'],
    ).exclude(owner=user).annotate(
        _status_rank=Case(
            When(status='indexed', then=Value(0)),
            default=Value(1),
            output_field=IntegerField(),
        ),
    ).order_by('_status_rank', 'created_at')

    with transaction.atomic():
        for source in shared.iterator():
            identity = _identity(source)
            if not identity:
                continue
            current = owned_by_identity.get(identity)
            values = _copy_values(source)
            if source.status == 'failed':
                # Don't make a new account repeat another account's exhausted
                # retries. It can still use the explicit Retry action.
                values['retry_count'] = settings.SHAREPOINT_MAX_VIDEO_RETRIES + 1
            if current:
                if current.status != 'indexed' or source.status == 'indexed':
                    for field, value in values.items():
                        setattr(current, field, value)
                    current.save(update_fields=[*COPY_FIELDS, 'updated_at'])
                continue

            lookup = {
                'owner': user,
                'source_type': 'sharepoint',
                'source_drive_id': source.source_drive_id,
                'source_item_id': source.source_item_id,
            } if source.source_drive_id and source.source_item_id else {
                'owner': user,
                'source_type': 'sharepoint',
                'source_url': source.source_url,
            }
            current, _created = Video.objects.get_or_create(
                **lookup,
                defaults=values,
            )
            owned_by_identity[identity] = current

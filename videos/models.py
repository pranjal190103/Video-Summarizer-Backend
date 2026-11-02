import uuid
from django.conf import settings
from django.db import models

class Video(models.Model):
    CATEGORIES = [('Design', 'Design'), ('Content', 'Content'), ('Marketing', 'Marketing')]
    STATUSES = [('pending', 'Pending'), ('processing', 'Processing'), ('indexed', 'Indexed'), ('failed', 'Failed')]
    MODES = [('ai', 'AI'), ('manual', 'Manual')]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='videos', on_delete=models.CASCADE, null=True, blank=True)
    title = models.CharField(max_length=300)
    source_url = models.URLField(blank=True)
    source_type = models.CharField(max_length=20, default='transcript')
    source_folder = models.CharField(max_length=500, blank=True)
    source_drive_id = models.CharField(max_length=255, blank=True)
    source_item_id = models.CharField(max_length=255, blank=True)
    source_modified_at = models.DateTimeField(null=True, blank=True)
    source_size = models.PositiveBigIntegerField(null=True, blank=True)
    source_etag = models.CharField(max_length=255, blank=True)
    source_ctag = models.CharField(max_length=255, blank=True)
    analysis_version = models.CharField(max_length=32, default='1')
    summary = models.TextField(blank=True)
    transcript = models.TextField(blank=True)
    categories = models.JSONField(default=list)
    key_topics = models.JSONField(default=list)
    duration = models.CharField(max_length=30, blank=True)
    language = models.CharField(max_length=30, blank=True)
    kb_score = models.PositiveSmallIntegerField(default=0)
    training_ready = models.BooleanField(default=False)
    status = models.CharField(max_length=20, choices=STATUSES, default='pending')
    error_message = models.CharField(max_length=500, blank=True)
    retry_count = models.PositiveSmallIntegerField(default=0)
    analysis_completed_at = models.DateTimeField(null=True, blank=True)
    analysis_mode = models.CharField(max_length=20, choices=MODES, default='manual')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['owner', 'source_drive_id', 'source_item_id'],
                condition=models.Q(source_type='sharepoint') & ~models.Q(source_drive_id='') & ~models.Q(source_item_id=''),
                name='unique_sharepoint_video_per_owner',
            ),
        ]


class SharePointImport(models.Model):
    STATUSES = [('queued', 'Queued'), ('processing', 'Processing'), ('cancelling', 'Stopping'), ('cancelled', 'Stopped'), ('completed', 'Completed'), ('completed_with_errors', 'Completed with errors'), ('failed', 'Failed')]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='sharepoint_imports', on_delete=models.CASCADE)
    folder_name = models.CharField(max_length=255)
    folder_url = models.URLField(blank=True)
    status = models.CharField(max_length=24, choices=STATUSES, default='queued')
    total = models.PositiveIntegerField(default=0)
    folder_total = models.PositiveIntegerField(default=0)
    processed = models.PositiveIntegerField(default=0)
    imported = models.PositiveIntegerField(default=0)
    failed = models.PositiveIntegerField(default=0)
    batch_size = models.PositiveSmallIntegerField(default=2)
    trigger = models.CharField(max_length=20, choices=[('manual', 'Manual'), ('scheduled', 'Scheduled')], default='manual')
    target_video = models.ForeignKey(Video, null=True, blank=True, related_name='retry_imports', on_delete=models.SET_NULL)
    remaining = models.PositiveIntegerField(default=0)
    has_more = models.BooleanField(default=True)
    errors = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']


class SharePointSyncState(models.Model):
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    next_sync_at = models.DateTimeField(null=True, blank=True)
    discovered_count = models.PositiveIntegerField(default=0)
    last_error = models.CharField(max_length=500, blank=True)
    lease_token = models.UUIDField(null=True, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)


class SharePointAnalysisSlot(models.Model):
    slot_number = models.PositiveSmallIntegerField(primary_key=True)
    lease_token = models.UUIDField(null=True, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)

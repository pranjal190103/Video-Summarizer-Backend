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
    analysis_mode = models.CharField(max_length=20, choices=MODES, default='manual')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']


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
    batch_size = models.PositiveSmallIntegerField(default=10)
    remaining = models.PositiveIntegerField(default=0)
    has_more = models.BooleanField(default=True)
    errors = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']


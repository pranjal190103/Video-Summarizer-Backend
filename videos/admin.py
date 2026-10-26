from django.contrib import admin
from .models import Video

@admin.register(Video)
class VideoAdmin(admin.ModelAdmin):
    list_display = ('title', 'status', 'kb_score', 'training_ready', 'created_at')
    search_fields = ('title', 'summary')
    list_filter = ('status', 'training_ready', 'analysis_mode')

from django.urls import path
from .auth_views import CurrentAccountView, LoginView, LogoutView, RegisterView
from .views import VideoDetailView, VideoListCreateView, analyze, cancel_sharepoint_import, clear_video_library, health, reanalyze, retry_sharepoint_sync, retry_sharepoint_video, sharepoint_import_detail, sharepoint_import_list, sharepoint_imports, sharepoint_sync_status, upload_video

urlpatterns = [
    path('health/', health, name='health'),
    path('auth/register/', RegisterView.as_view(), name='auth-register'),
    path('auth/login/', LoginView.as_view(), name='auth-login'),
    path('auth/logout/', LogoutView.as_view(), name='auth-logout'),
    path('auth/me/', CurrentAccountView.as_view(), name='auth-current-account'),
    path('analyze/', analyze, name='analyze'),
    path('videos/upload/', upload_video, name='video-upload'),
    path('videos/clear/', clear_video_library, name='video-clear'),
    path('sharepoint/imports/', sharepoint_imports, name='sharepoint-imports'),
    path('sharepoint/imports/recent/', sharepoint_import_list, name='sharepoint-import-list'),
    path('sharepoint/sync-status/', sharepoint_sync_status, name='sharepoint-sync-status'),
    path('sharepoint/sync/now/', retry_sharepoint_sync, name='sharepoint-sync-now'),
    path('sharepoint/imports/<uuid:pk>/', sharepoint_import_detail, name='sharepoint-import-detail'),
    path('sharepoint/imports/<uuid:pk>/cancel/', cancel_sharepoint_import, name='sharepoint-import-cancel'),
    path('videos/', VideoListCreateView.as_view(), name='video-list'),
    path('videos/<uuid:pk>/', VideoDetailView.as_view(), name='video-detail'),
    path('videos/<uuid:pk>/reanalyze/', reanalyze, name='video-reanalyze'),
    path('videos/<uuid:pk>/retry/', retry_sharepoint_video, name='video-retry'),
]

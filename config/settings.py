import os
from pathlib import Path
from dotenv import load_dotenv
      
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env')
SECRET_KEY = os.getenv('DJANGO_SECRET_KEY', 'local-development-only-change-before-deploy')
DEBUG = os.getenv('DJANGO_DEBUG', 'false').lower() == 'true'
ALLOWED_HOSTS = [host.strip() for host in os.getenv('DJANGO_ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',') if host.strip()]
INSTALLED_APPS = [
    'django.contrib.admin', 'django.contrib.auth', 'django.contrib.contenttypes',
    'django.contrib.sessions', 'django.contrib.messages', 'django.contrib.staticfiles',
    'corsheaders', 'rest_framework', 'rest_framework.authtoken', 'videos',
]
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware', 'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware', 'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware', 'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware', 'django.middleware.clickjacking.XFrameOptionsMiddleware',
]
ROOT_URLCONF = 'config.urls'
TEMPLATES = [{'BACKEND': 'django.template.backends.django.DjangoTemplates', 'DIRS': [], 'APP_DIRS': True,
              'OPTIONS': {'context_processors': ['django.template.context_processors.request', 'django.contrib.auth.context_processors.auth', 'django.contrib.messages.context_processors.messages']}}]
WSGI_APPLICATION = 'config.wsgi.application'
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': os.getenv('DJANGO_DB_PATH') or BASE_DIR / 'db.sqlite3'}}
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 12}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True
STATIC_URL = 'static/'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
_configured_cors_origins = [
    origin.strip()
    for origin in os.getenv('CORS_ALLOWED_ORIGINS', 'http://localhost:5173,http://3.108.234.6').split(',')
    if origin.strip()
]
_local_vite_origins = [
    f'http://{host}:{port}'
    for host in ('localhost', '127.0.0.1')
    for port in (5173, 5174, 5175)
]
CORS_ALLOWED_ORIGINS = list(dict.fromkeys(_configured_cors_origins + _local_vite_origins))
REST_FRAMEWORK = {
    'DEFAULT_RENDERER_CLASSES': ['rest_framework.renderers.JSONRenderer'],
    'DEFAULT_AUTHENTICATION_CLASSES': ['rest_framework.authentication.TokenAuthentication'],
    'DEFAULT_PERMISSION_CLASSES': ['rest_framework.permissions.IsAuthenticated'],
    'DEFAULT_THROTTLE_RATES': {'register': '5/hour', 'login': '10/minute', 'ai': '5/hour', 'sharepoint-start': '10/minute'},
}
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', '')
OPENAI_MODEL = os.getenv('OPENAI_MODEL', 'gpt-4o-mini')
MS_GRAPH_TENANT_ID = os.getenv('MS_GRAPH_TENANT_ID', '')
MS_GRAPH_CLIENT_ID = os.getenv('MS_GRAPH_CLIENT_ID', '')
MS_GRAPH_CLIENT_SECRET = os.getenv('MS_GRAPH_CLIENT_SECRET', '')
MS_GRAPH_DRIVE_ID = os.getenv('MS_GRAPH_DRIVE_ID', '')
MS_GRAPH_FOLDER_ITEM_ID = os.getenv('MS_GRAPH_FOLDER_ITEM_ID', '')
MS_GRAPH_FOLDER_NAME = os.getenv('MS_GRAPH_FOLDER_NAME', 'SharePoint video folder')
DATA_UPLOAD_MAX_MEMORY_SIZE = 550 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024
MAX_VIDEO_SIZE_GB = max(1, int(os.getenv('MAX_VIDEO_SIZE_GB', '5')))
MAX_VIDEO_SIZE_BYTES = MAX_VIDEO_SIZE_GB * 1024 * 1024 * 1024
SHAREPOINT_BATCH_SIZE = min(2, max(1, int(os.getenv('SHAREPOINT_BATCH_SIZE', '2'))))
SHAREPOINT_MAX_VIDEO_RETRIES = max(0, int(os.getenv('SHAREPOINT_MAX_VIDEO_RETRIES', '2')))
SHAREPOINT_RETRY_DELAY_SECONDS = max(0, int(os.getenv('SHAREPOINT_RETRY_DELAY_SECONDS', '30')))
SHAREPOINT_SYNC_MAX_RETRIES = max(0, int(os.getenv('SHAREPOINT_SYNC_MAX_RETRIES', '3')))
SHAREPOINT_SYNC_RETRY_DELAY_SECONDS = max(1, int(os.getenv('SHAREPOINT_SYNC_RETRY_DELAY_SECONDS', '900')))
SHAREPOINT_SYNC_INTERVAL_HOURS = max(1, int(os.getenv('SHAREPOINT_SYNC_INTERVAL_HOURS', '24')))
VIDEO_ANALYSIS_VERSION = '1'

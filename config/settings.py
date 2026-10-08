"""
Django settings for the chat backend.

Every environment-specific value is read from environment variables, which are
loaded from ``backend/.env`` in development (see ``.env.example``).

Without DATABASE_URL / REDIS_URL the project falls back to SQLite and in-memory
cache / channel layers, so it starts without any extra services installed.
"""

import os
import sys
from datetime import timedelta
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / '.env')


def env_str(name, default=''):
    return os.environ.get(name, default).strip()


def env_bool(name, default=False):
    value = os.environ.get(name, '').strip().lower()
    if not value:
        return default
    return value in {'1', 'true', 'yes', 'on'}


def env_int(name, default):
    value = os.environ.get(name, '').strip()
    return int(value) if value else default


def env_list(name, default=''):
    return [item.strip() for item in os.environ.get(name, default).split(',') if item.strip()]


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

DEBUG = env_bool('DJANGO_DEBUG', default=False)
TESTING = len(sys.argv) > 1 and sys.argv[1] == 'test'

SECRET_KEY = env_str('DJANGO_SECRET_KEY')
if not SECRET_KEY:
    raise ImproperlyConfigured(
        'DJANGO_SECRET_KEY is not set. Copy backend/.env.example to backend/.env and fill it in.'
    )

ALLOWED_HOSTS = env_list('DJANGO_ALLOWED_HOSTS', 'localhost,127.0.0.1')
# Render sets this to the service's own *.onrender.com host name.
RENDER_EXTERNAL_HOSTNAME = env_str('RENDER_EXTERNAL_HOSTNAME')
if RENDER_EXTERNAL_HOSTNAME:
    ALLOWED_HOSTS.append(RENDER_EXTERNAL_HOSTNAME)

# Moderation panel (the shield in the chat list). Empty = turned off.
CHAT_ADMIN_PASSWORD = env_str('CHAT_ADMIN_PASSWORD')


# Origins (scheme://host:port) the Next.js frontend is served from. The browser
# only talks to Next.js, which proxies /api, /media and /ws to this backend.
FRONTEND_ORIGINS = env_list('FRONTEND_ORIGINS', 'http://localhost:3000,http://127.0.0.1:3000')

CSRF_TRUSTED_ORIGINS = FRONTEND_ORIGINS

# Origin check for WebSocket handshakes. In development any origin is accepted
# so the app can be opened from a phone on the local network; auth cookies are
# SameSite=Lax, so cross-site pages still cannot open an authenticated socket.
WEBSOCKET_ALLOWED_ORIGINS = ['*'] if DEBUG else FRONTEND_ORIGINS


# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------

INSTALLED_APPS = [
    # ASGI server: must stay first so `manage.py runserver` serves HTTP + WebSocket.
    'daphne',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # Third party
    'rest_framework',
    'rest_framework_simplejwt.token_blacklist',
    # Local
    'users',
    'chats',
    'messaging',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    # Production static files (admin CSS) after `collectstatic`; runserver serves them in DEBUG.
    *([] if DEBUG else ['whitenoise.middleware.WhiteNoiseMiddleware']),
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'config.middleware.ApiCsrfHeaderMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

ASGI_APPLICATION = 'config.asgi.application'
WSGI_APPLICATION = 'config.wsgi.application'


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

DATABASE_URL = env_str('DATABASE_URL')

if DATABASE_URL:
    DATABASES = {'default': dj_database_url.parse(DATABASE_URL)}
    if DATABASES['default']['ENGINE'] == 'django.db.backends.postgresql':
        # Persistent connections leak under ASGI; psycopg's pool is the
        # recommended replacement (requires CONN_MAX_AGE = 0, the default).
        # Short timeouts make requests fail fast while the database is down.
        # Hosted databases (e.g. Neon) suspend when idle and drop open
        # connections, so each pooled connection is checked before use.
        from psycopg_pool import ConnectionPool

        DATABASES['default'].setdefault('OPTIONS', {}).update(
            {
                'connect_timeout': 5,
                'pool': {
                    'min_size': 1,
                    'max_size': 10,
                    'timeout': 10,
                    'check': ConnectionPool.check_connection,
                    'max_idle': 120,
                },
            }
        )
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# ---------------------------------------------------------------------------
# Redis: cache + channel layer (WebSocket fan-out)
# ---------------------------------------------------------------------------

REDIS_URL = env_str('REDIS_URL')

if REDIS_URL:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.redis.RedisCache',
            'LOCATION': REDIS_URL,
            'KEY_PREFIX': 'chat',
        }
    }
    CHANNEL_LAYERS = {
        'default': {
            'BACKEND': 'channels_redis.core.RedisChannelLayer',
            'CONFIG': {'hosts': [REDIS_URL], 'prefix': 'chat'},
        }
    }
else:
    # Single-process only: fine for development, not for multiple workers.
    CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
    CHANNEL_LAYERS = {'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

AUTH_USER_MODEL = 'users.User'

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

if TESTING:
    # Real hashers are deliberately slow; tests only need them to work.
    PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=env_int('JWT_ACCESS_MINUTES', 15)),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=env_int('JWT_REFRESH_DAYS', 30)),
    # Tokens carry a hash of the password: changing it invalidates them at once.
    'CHECK_REVOKE_TOKEN': True,
}

# JWTs are stored in httpOnly cookies (see users/tokens.py).
AUTH_COOKIE_ACCESS = 'chat_access'
AUTH_COOKIE_REFRESH = 'chat_refresh'
AUTH_COOKIE_MEDIA = 'chat_media'
AUTH_COOKIE_SESSION = 'chat_session'
AUTH_COOKIE_SECURE = env_bool('AUTH_COOKIE_SECURE', default=not DEBUG)
AUTH_COOKIE_DOMAIN = env_str('AUTH_COOKIE_DOMAIN') or None

# Per-account sign-in protection: this many failures locks the account briefly.
LOGIN_MAX_FAILURES = 10
LOGIN_LOCKOUT_SECONDS = 15 * 60

AVATAR_MAX_UPLOAD_SIZE = 10 * 1024 * 1024


# ---------------------------------------------------------------------------
# Chat messages
# ---------------------------------------------------------------------------

MB = 1024 * 1024
CHAT_MAX_TEXT_LENGTH = 4096
CHAT_UPLOAD_LIMITS = {
    'IMAGE': env_int('CHAT_MAX_IMAGE_MB', 20) * MB,
    'VIDEO': env_int('CHAT_MAX_VIDEO_MB', 100) * MB,
    'AUDIO': env_int('CHAT_MAX_AUDIO_MB', 20) * MB,
    'FILE': env_int('CHAT_MAX_FILE_MB', 100) * MB,
}
# Whole request: the largest file + a video thumbnail + form fields.
CHAT_MAX_REQUEST_SIZE = max(CHAT_UPLOAD_LIMITS.values()) + 25 * MB

# Online status (see chats/presence.py). The app pings every 25 seconds.
PRESENCE_ONLINE_WINDOW = 90  # a heartbeat this recent means "online"
PRESENCE_HEARTBEAT_WRITE_SECONDS = 60  # how often heartbeats update last_seen
PRESENCE_OFFLINE_GRACE_SECONDS = 8  # quick reconnects don't show "offline"
PRESENCE_CACHE_TTL = 120  # connection counter lifetime without heartbeats

# With nginx in front, set e.g. "/protected-media/" (an `internal` location
# pointing at MEDIA_ROOT) and nginx will send files instead of Django.
MEDIA_ACCEL_REDIRECT_PREFIX = env_str('MEDIA_ACCEL_REDIRECT_PREFIX')


# ---------------------------------------------------------------------------
# Django REST framework
# ---------------------------------------------------------------------------

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': ['users.authentication.CookieJWTAuthentication'],
    'DEFAULT_PERMISSION_CLASSES': ['rest_framework.permissions.IsAuthenticated'],
    'DEFAULT_RENDERER_CLASSES': [
        'rest_framework.renderers.JSONRenderer',
        *(['rest_framework.renderers.BrowsableAPIRenderer'] if DEBUG else []),
    ],
    'DEFAULT_PARSER_CLASSES': [
        'rest_framework.parsers.JSONParser',
        'rest_framework.parsers.MultiPartParser',
        'rest_framework.parsers.FormParser',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'login': '30/min',
        'register': '30/hour',
        'guest': '60/hour',
        'password': '10/hour',
        'send_message': '120/min',
    },
    # 0 = trust only the socket address. Behind nginx (which sets
    # X-Forwarded-For) use 1 so rate limits apply per client IP.
    'NUM_PROXIES': env_int('DJANGO_NUM_PROXIES', 0),
    'EXCEPTION_HANDLER': 'config.exceptions.api_exception_handler',
}


# ---------------------------------------------------------------------------
# Internationalization
# ---------------------------------------------------------------------------

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True


# ---------------------------------------------------------------------------
# Static & media files
# ---------------------------------------------------------------------------

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

# User uploads: avatars/, images/, videos/, audio/, files/
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'
FILE_UPLOAD_PERMISSIONS = 0o644

# Hosts with a temporary disk (e.g. Render's free plan) lose local files on
# every restart: then uploads go to S3-compatible storage (e.g. Cloudflare R2).
# Files stay private; Django still checks access and streams them.
S3_BUCKET = env_str('S3_BUCKET')
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {
        'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'
        if DEBUG
        else 'whitenoise.storage.CompressedManifestStaticFilesStorage'
    },
}
if S3_BUCKET:
    STORAGES['default'] = {
        'BACKEND': 'storages.backends.s3.S3Storage',
        'OPTIONS': {
            'bucket_name': S3_BUCKET,
            'endpoint_url': env_str('S3_ENDPOINT_URL') or None,
            'access_key': env_str('S3_ACCESS_KEY_ID'),
            'secret_key': env_str('S3_SECRET_ACCESS_KEY'),
            'region_name': env_str('S3_REGION', 'auto'),
            # "path" for providers that need it (e.g. Supabase Storage).
            'addressing_style': env_str('S3_ADDRESSING_STYLE') or None,
            'signature_version': 's3v4',
            'default_acl': None,
            'file_overwrite': False,
            'querystring_auth': True,
        },
    }


# ---------------------------------------------------------------------------
# Security (production)
# ---------------------------------------------------------------------------

X_FRAME_OPTIONS = 'DENY'

if not DEBUG:
    SESSION_COOKIE_SECURE = env_bool('DJANGO_SECURE_COOKIES', default=True)
    CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE
    # TLS is terminated by the reverse proxy in front of the app.
    SECURE_SSL_REDIRECT = env_bool('DJANGO_SECURE_SSL_REDIRECT', default=False)
    SECURE_HSTS_SECONDS = env_int('DJANGO_HSTS_SECONDS', 0)
    if env_bool('DJANGO_TRUST_X_FORWARDED_PROTO', default=False):
        SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'console': {
            'format': '[{asctime}] {levelname} {name}: {message}',
            'datefmt': '%H:%M:%S',
            'style': '{',
        },
    },
    'handlers': {
        'console': {'class': 'logging.StreamHandler', 'formatter': 'console'},
    },
    'root': {'handlers': ['console'], 'level': env_str('DJANGO_LOG_LEVEL', 'INFO').upper()},
    'loggers': {
        # Replace Django's default handler so each line is printed only once.
        'django': {'handlers': ['console'], 'level': 'INFO', 'propagate': False},
        # Tests send bad requests on purpose; only show real errors.
        'django.request': {'level': 'ERROR' if TESTING else 'INFO'},
    },
}

import asyncio

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.core.cache import caches
from django.db import connection
from django.http import JsonResponse
from django.views import defaults
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_safe

from config.media import serve_file


def _check_database():
    with connection.cursor() as cursor:
        cursor.execute('SELECT 1')
    return {'backend': connection.vendor}


def _check_cache():
    cache = caches['default']
    cache.set('health:ping', 'pong', timeout=5)
    if cache.get('health:ping') != 'pong':
        raise RuntimeError('cache did not return the stored value')
    return {'backend': type(cache).__name__}


async def _round_trip(layer):
    channel = await layer.new_channel('health.')
    await layer.send(channel, {'type': 'health.ping'})
    message = await asyncio.wait_for(layer.receive(channel), timeout=3)
    if message.get('type') != 'health.ping':
        raise RuntimeError('channel layer returned an unexpected message')


def _check_channel_layer():
    layer = get_channel_layer()
    async_to_sync(_round_trip)(layer)
    return {'backend': type(layer).__name__}


def _check_storage():
    from django.core.files.storage import default_storage

    remote = hasattr(default_storage, 'bucket_name')  # also sets up the lazy storage
    backend = default_storage.__class__.__name__
    try:
        if remote:
            # A listing (unlike HEAD) returns an error body with a precise code.
            client = default_storage.connection.meta.client
            client.list_objects_v2(Bucket=default_storage.bucket_name, MaxKeys=1)
        else:
            default_storage.exists('health-check')
    except Exception as exc:
        # S3 errors carry a safe code such as "SignatureDoesNotMatch" or "NoSuchBucket".
        code = getattr(exc, 'response', {}).get('Error', {}).get('Code') or type(exc).__name__
        raise RuntimeError(f'{backend}: {code}') from exc
    return {'backend': backend}


@csrf_exempt  # read-only; lets require_GET answer other methods with 405
@never_cache
@require_GET
def health(request):
    """Report whether the database, cache and channel layer are reachable."""
    checks = {}
    for name, check in (
        ('database', _check_database),
        ('cache', _check_cache),
        ('channel_layer', _check_channel_layer),
        ('storage', _check_storage),
    ):
        try:
            checks[name] = {'ok': True, **check()}
        except Exception as exc:  # noqa: BLE001 - any failure means unhealthy
            safe = name == 'storage'  # its message holds only a backend name and an error code
            checks[name] = {'ok': False, 'error': str(exc) if settings.DEBUG or safe else 'unavailable'}

    healthy = all(item['ok'] for item in checks.values())
    return JsonResponse(
        {'status': 'ok' if healthy else 'error', 'checks': checks},
        status=200 if healthy else 503,
    )


AVATAR_TYPES = {'webp': 'image/webp', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png'}


@require_safe
def avatar(request, path):
    """Public avatar files. Names are random and never reused, so they cache forever."""
    extension = path.rsplit('.', 1)[-1].lower()
    return serve_file(
        request,
        f'avatars/{path}',
        content_type=AVATAR_TYPES.get(extension, 'application/octet-stream'),
        cache_control='public, max-age=31536000, immutable',
    )


def page_not_found(request, exception):
    if request.path.startswith('/api/'):
        return JsonResponse({'detail': 'Not found.', 'code': 'not_found'}, status=404)
    return defaults.page_not_found(request, exception)


def server_error(request):
    if request.path.startswith('/api/'):
        return JsonResponse({'detail': 'Something went wrong on our side.', 'code': 'server_error'}, status=500)
    return defaults.server_error(request)

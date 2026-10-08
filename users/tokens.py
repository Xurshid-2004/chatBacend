"""
JWT session helpers: tokens live in httpOnly cookies, never in JavaScript.

- access  cookie: path "/", short-lived, sent with every API/WebSocket request
- refresh cookie: path "/api/auth/", long-lived, only sent to auth endpoints
- media   cookie: path "/api/messages/", long-lived, read-only access to chat
                  files, so <img>/<video> keep loading after the access token expires
- session cookie: a harmless "signed in" hint the Next.js proxy uses to guard pages
"""

from django.conf import settings
from django.core import signing
from rest_framework_simplejwt.settings import api_settings as jwt_settings
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.utils import get_md5_hash_password

REFRESH_COOKIE_PATH = '/api/auth/'
MEDIA_COOKIE_PATH = '/api/messages/'
MEDIA_TOKEN_SALT = 'users.media-cookie'


def make_media_token(user):
    # The password hash ties the token to the current password (changing it revokes the token).
    return signing.dumps({'user': user.pk, 'pwd': get_md5_hash_password(user.password)}, salt=MEDIA_TOKEN_SALT)


def read_media_token(value):
    """Return the token payload, or None when it is invalid or expired."""
    max_age = jwt_settings.REFRESH_TOKEN_LIFETIME.total_seconds()
    try:
        return signing.loads(value, salt=MEDIA_TOKEN_SALT, max_age=max_age)
    except signing.BadSignature:
        return None


def _cookie_options():
    return {
        'httponly': True,
        'secure': settings.AUTH_COOKIE_SECURE,
        'samesite': 'Lax',
        'domain': settings.AUTH_COOKIE_DOMAIN,
    }


def set_auth_cookies(response, user):
    """Issue a fresh token pair for `user` and attach it to `response`."""
    refresh = RefreshToken.for_user(user)
    access_age = int(jwt_settings.ACCESS_TOKEN_LIFETIME.total_seconds())
    refresh_age = int(jwt_settings.REFRESH_TOKEN_LIFETIME.total_seconds())
    options = _cookie_options()

    response.set_cookie(
        settings.AUTH_COOKIE_ACCESS, str(refresh.access_token), max_age=access_age, path='/', **options
    )
    response.set_cookie(
        settings.AUTH_COOKIE_REFRESH, str(refresh), max_age=refresh_age, path=REFRESH_COOKIE_PATH, **options
    )
    response.set_cookie(
        settings.AUTH_COOKIE_MEDIA, make_media_token(user), max_age=refresh_age, path=MEDIA_COOKIE_PATH, **options
    )
    response.set_cookie(settings.AUTH_COOKIE_SESSION, '1', max_age=refresh_age, path='/', **options)
    return response


def clear_auth_cookies(response):
    domain = settings.AUTH_COOKIE_DOMAIN
    response.delete_cookie(settings.AUTH_COOKIE_ACCESS, path='/', domain=domain, samesite='Lax')
    response.delete_cookie(settings.AUTH_COOKIE_REFRESH, path=REFRESH_COOKIE_PATH, domain=domain, samesite='Lax')
    response.delete_cookie(settings.AUTH_COOKIE_MEDIA, path=MEDIA_COOKIE_PATH, domain=domain, samesite='Lax')
    response.delete_cookie(settings.AUTH_COOKIE_SESSION, path='/', domain=domain, samesite='Lax')
    return response


def revoke_all_refresh_tokens(user):
    """Sign `user` out everywhere by blacklisting every refresh token issued to them."""
    tokens = OutstandingToken.objects.filter(user=user, blacklistedtoken__isnull=True)
    BlacklistedToken.objects.bulk_create(
        [BlacklistedToken(token=token) for token in tokens], ignore_conflicts=True
    )

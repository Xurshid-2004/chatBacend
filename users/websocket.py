from channels.db import database_sync_to_async
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.http.cookie import parse_cookie
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError


def _raw_token(scope):
    headers = dict(scope.get('headers', []))
    authorization = headers.get(b'authorization', b'').split()
    if len(authorization) == 2 and authorization[0].lower() == b'bearer':
        return authorization[1].decode('latin-1')
    cookies = parse_cookie(headers.get(b'cookie', b'').decode('latin-1'))
    return cookies.get(settings.AUTH_COOKIE_ACCESS) or None


@database_sync_to_async
def _authenticate(raw_token):
    authentication = JWTAuthentication()
    try:
        token = authentication.get_validated_token(raw_token)
        user = authentication.get_user(token)
    except (InvalidToken, TokenError, AuthenticationFailed):
        return None, None
    return user, token['exp']


class JWTAuthMiddleware:
    """
    Authenticates WebSocket handshakes with the access token cookie (or an
    `Authorization: Bearer` header). Adds `user` and `token_expires_at` to the scope.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        user, expires_at = None, None
        raw_token = _raw_token(scope)
        if raw_token:
            user, expires_at = await _authenticate(raw_token)
        scope = {**scope, 'user': user or AnonymousUser(), 'token_expires_at': expires_at}
        return await self.app(scope, receive, send)

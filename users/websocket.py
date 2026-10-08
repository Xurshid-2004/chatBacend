import time
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.core import signing
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


TICKET_SALT = 'chat.ws-ticket'
TICKET_MAX_AGE = 60  # seconds to open the socket after getting a ticket


def make_ticket(user):
    """
    A short-lived pass for opening the WebSocket straight to this server, for
    frontends whose host cannot proxy WebSockets (e.g. Vercel). The page gets
    it from the API (authenticated by the session cookie).
    """
    return signing.dumps({'u': user.pk}, salt=TICKET_SALT, compress=True)


def _ticket(scope):
    values = parse_qs(scope.get('query_string', b'').decode('latin-1')).get('ticket')
    return values[0] if values else None


@database_sync_to_async
def _authenticate_ticket(ticket):
    from .models import User

    try:
        data = signing.loads(ticket, salt=TICKET_SALT, max_age=TICKET_MAX_AGE)
        user = User.objects.get(pk=data['u'], is_active=True)
    except (signing.BadSignature, User.DoesNotExist, KeyError, TypeError):
        return None, None
    # Re-authenticate as often as with the access token: the app gets a new ticket then.
    lifetime = settings.SIMPLE_JWT['ACCESS_TOKEN_LIFETIME'].total_seconds()
    return user, int(time.time() + lifetime)


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
        ticket = _ticket(scope)
        raw_token = _raw_token(scope)
        if ticket:
            user, expires_at = await _authenticate_ticket(ticket)
        elif raw_token:
            user, expires_at = await _authenticate(raw_token)
        scope = {**scope, 'user': user or AnonymousUser(), 'token_expires_at': expires_at}
        return await self.app(scope, receive, send)

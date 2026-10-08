"""
Moderation: whoever knows CHAT_ADMIN_PASSWORD can see everyone who joined and
delete accounts (with their chats). The password lives only in the server's
environment; unlocking gives this browser a signed, httpOnly cookie for 12 hours.
"""

import secrets

from django.conf import settings
from django.core import signing
from django.core.cache import cache
from django.db.models import Count
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound, PermissionDenied, Throttled, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import User
from .serializers import UserSerializer
from .services import delete_account

COOKIE_NAME = 'chat_admin'
COOKIE_PATH = '/api/moderation/'
COOKIE_SALT = 'users.moderation'
COOKIE_MAX_AGE = 12 * 60 * 60
# Wrong passwords from anyone, anywhere: a short password must not be guessable.
FAILURES_KEY = 'moderation:failures'
MAX_FAILURES = 10
LOCKOUT_SECONDS = 15 * 60


def _admin_cookie(user):
    return signing.dumps({'u': user.pk}, salt=COOKIE_SALT)


def is_unlocked(request):
    value = request.COOKIES.get(COOKIE_NAME)
    if not value or not request.user.is_authenticated:
        return False
    try:
        data = signing.loads(value, salt=COOKIE_SALT, max_age=COOKIE_MAX_AGE)
    except signing.BadSignature:
        return False
    return data.get('u') == request.user.pk


class ModerationView(APIView):
    def check_permissions(self, request):
        super().check_permissions(request)
        if not is_unlocked(request):
            raise PermissionDenied('Enter the admin password first.')


class StatusView(APIView):
    def get(self, request):
        return Response({'enabled': bool(settings.CHAT_ADMIN_PASSWORD), 'unlocked': is_unlocked(request)})


class UnlockView(APIView):
    def post(self, request):
        expected = settings.CHAT_ADMIN_PASSWORD
        if not expected:
            raise PermissionDenied('The admin password is not set on the server (CHAT_ADMIN_PASSWORD).')
        if cache.get(FAILURES_KEY, 0) >= MAX_FAILURES:
            raise Throttled(detail='Too many wrong passwords. Try again in 15 minutes.')

        password = str(request.data.get('password', ''))
        if not secrets.compare_digest(password.encode(), expected.encode()):
            cache.add(FAILURES_KEY, 0, timeout=LOCKOUT_SECONDS)
            cache.incr(FAILURES_KEY)
            raise ValidationError({'password': ['Wrong password.']})

        response = Response({'unlocked': True})
        response.set_cookie(
            COOKIE_NAME,
            _admin_cookie(request.user),
            max_age=COOKIE_MAX_AGE,
            path=COOKIE_PATH,
            httponly=True,
            secure=settings.AUTH_COOKIE_SECURE,
            samesite='Lax',
            domain=settings.AUTH_COOKIE_DOMAIN,
        )
        return response


class LockView(APIView):
    def post(self, request):
        response = Response(status=status.HTTP_204_NO_CONTENT)
        response.delete_cookie(COOKIE_NAME, path=COOKIE_PATH, domain=settings.AUTH_COOKIE_DOMAIN, samesite='Lax')
        return response


class MemberSerializer(UserSerializer):
    chats = serializers.IntegerField(source='chat_count', read_only=True)
    messages = serializers.IntegerField(source='message_count', read_only=True)

    class Meta(UserSerializer.Meta):
        fields = [*UserSerializer.Meta.fields, 'chats', 'messages']


class MemberListView(ModerationView):
    def get(self, request):
        users = (
            User.objects.filter(is_superuser=False, is_staff=False)
            .annotate(
                chat_count=Count('chat_memberships', distinct=True),
                message_count=Count('sent_messages', distinct=True),
            )
            .order_by('-date_joined')
        )
        return Response(MemberSerializer(users, many=True).data)


class MemberDetailView(ModerationView):
    def delete(self, request, pk):
        user = User.objects.filter(pk=pk, is_superuser=False, is_staff=False).first()
        if user is None:
            raise NotFound('This person is already gone.')
        if user.pk == request.user.pk:
            raise ValidationError('To delete your own account, use Profile → Delete account.')
        delete_account(user)
        return Response(status=status.HTTP_204_NO_CONTENT)

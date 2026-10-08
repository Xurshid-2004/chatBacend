import secrets
from collections import defaultdict

from django.conf import settings
from django.contrib.auth import authenticate
from django.contrib.auth.models import update_last_login
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Case, F, IntegerField, Q, When
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, Throttled, ValidationError
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.settings import api_settings as jwt_settings
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.utils import get_md5_hash_password

from chats.models import Chat, ChatMember
from chats.realtime import account_deleted

from .models import User
from .names import username_candidates
from .presets import AVATAR_PRESETS
from .serializers import (
    USERNAME_TAKEN,
    LoginSerializer,
    PasswordChangeSerializer,
    ProfileSerializer,
    RegisterSerializer,
    StartSerializer,
    UserSerializer,
)
from .signals import profile_updated
from .tokens import clear_auth_cookies, revoke_all_refresh_tokens, set_auth_cookies
from .websocket import make_ticket

INVALID_CREDENTIALS = 'Invalid username or password.'


def _chat_members_of(user_id):
    """{chat_id: [member ids]} for every chat `user_id` is in."""
    members = defaultdict(list)
    rows = ChatMember.objects.filter(chat__members__user_id=user_id).values_list('chat_id', 'user_id')
    for chat_id, member_id in rows:
        members[chat_id].append(member_id)
    return members


class PublicAuthView(APIView):
    """Auth endpoints ignore access tokens, so a stale cookie can never block them."""

    authentication_classes = []
    permission_classes = [AllowAny]


class RegisterView(PublicAuthView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'register'

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            with transaction.atomic():
                user = serializer.save()
        except IntegrityError as exc:  # the same username registered at the same moment
            raise ValidationError({'username': [USERNAME_TAKEN]}) from exc

        update_last_login(None, user)
        response = Response({'user': UserSerializer(user).data}, status=status.HTTP_201_CREATED)
        return set_auth_cookies(response, user)


class GuestView(PublicAuthView):
    """
    The start screen: a name + a ready-made avatar create an account with no
    password. The session cookies are the key to it (it lives on this device).
    """

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'guest'

    def post(self, request):
        serializer = StartSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        name = serializer.validated_data['name']
        preset = serializer.validated_data.get('avatar_preset') or secrets.choice(AVATAR_PRESETS)

        user = None
        for username in username_candidates(name):
            if User.objects.filter(username__iexact=username).exists():
                continue
            try:
                with transaction.atomic():
                    user = User.objects.create_user(username=username, first_name=name, avatar_preset=preset)
                break
            except IntegrityError:  # taken at the same moment; try another number
                continue
        if user is None:
            raise ValidationError('Could not create an account right now. Please try again.')

        update_last_login(None, user)
        response = Response({'user': UserSerializer(user).data}, status=status.HTTP_201_CREATED)
        return set_auth_cookies(response, user)


class LoginView(PublicAuthView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'login'

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        username = serializer.validated_data['username'].strip()

        # Per-account brute-force protection, independent of the client IP.
        failures_key = f'auth:login-failures:{username.lower()}'
        if cache.get(failures_key, 0) >= settings.LOGIN_MAX_FAILURES:
            raise Throttled(
                wait=settings.LOGIN_LOCKOUT_SECONDS,
                detail='Too many failed sign-in attempts. Try again later.',
            )

        user = authenticate(request, username=username, password=serializer.validated_data['password'])
        if user is None:
            cache.add(failures_key, 0, timeout=settings.LOGIN_LOCKOUT_SECONDS)
            try:
                cache.incr(failures_key)
            except ValueError:  # the key expired between add() and incr()
                pass
            raise ValidationError(INVALID_CREDENTIALS)

        cache.delete(failures_key)
        update_last_login(None, user)
        response = Response({'user': UserSerializer(user).data})
        return set_auth_cookies(response, user)


class RefreshView(PublicAuthView):
    """Trade the refresh cookie for a new token pair (the old refresh token is revoked)."""

    def post(self, request):
        raw_token = request.COOKIES.get(settings.AUTH_COOKIE_REFRESH)
        user = self._consume_refresh_token(raw_token) if raw_token else None
        if user is None:
            response = Response(
                {'detail': 'Your session has expired. Please sign in again.', 'code': 'session_expired'},
                status=status.HTTP_401_UNAUTHORIZED,
            )
            return clear_auth_cookies(response)
        return set_auth_cookies(Response(status=status.HTTP_204_NO_CONTENT), user)

    @staticmethod
    def _consume_refresh_token(raw_token):
        try:
            refresh = RefreshToken(raw_token)  # checks signature, expiry and blacklist
            user = User.objects.get(pk=refresh[jwt_settings.USER_ID_CLAIM], is_active=True)
        except (TokenError, KeyError, ValueError, User.DoesNotExist):
            return None
        if refresh.get(jwt_settings.REVOKE_TOKEN_CLAIM) != get_md5_hash_password(user.password):
            return None  # the password changed after this token was issued
        refresh.blacklist()
        return user


class LogoutView(PublicAuthView):
    def post(self, request):
        raw_token = request.COOKIES.get(settings.AUTH_COOKIE_REFRESH)
        if raw_token:
            try:
                RefreshToken(raw_token).blacklist()
            except TokenError:
                pass
        return clear_auth_cookies(Response(status=status.HTTP_204_NO_CONTENT))


class WebSocketTicketView(APIView):
    """A one-minute pass for opening the WebSocket directly (see users/websocket.py)."""

    def post(self, request):
        return Response({'ticket': make_ticket(request.user)})


class MeView(APIView):
    def get(self, request):
        return Response(UserSerializer(request.user).data)


class ProfileView(APIView):
    def get(self, request):
        return Response(UserSerializer(request.user).data)

    def patch(self, request):
        serializer = ProfileSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        try:
            with transaction.atomic():
                user = serializer.save()
        except IntegrityError as exc:
            raise ValidationError({'username': [USERNAME_TAKEN]}) from exc
        transaction.on_commit(lambda: profile_updated.send(sender=User, user=user))
        return Response(UserSerializer(user).data)

    def delete(self, request):
        """
        Delete the account for good: the profile, avatar, and every chat the user
        was in (with all messages and files on both sides — a one-to-one chat
        with nobody on the other end is useless). Cannot be undone.
        """
        user = request.user
        if user.is_staff or user.is_superuser:
            raise PermissionDenied('Admin accounts can only be deleted in the admin panel.')
        user_id = user.pk
        with transaction.atomic():
            chats = {
                chat_id: [member for member in members if member != user_id]
                for chat_id, members in _chat_members_of(user_id).items()
            }
            revoke_all_refresh_tokens(user)
            Chat.objects.filter(pk__in=chats).delete()  # messages and their files go with them
            user.delete()
        transaction.on_commit(lambda: account_deleted(user_id=user_id, chats=chats))
        return clear_auth_cookies(Response(status=status.HTTP_204_NO_CONTENT))


class PasswordChangeView(APIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'password'

    def post(self, request):
        serializer = PasswordChangeSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        user = request.user
        with transaction.atomic():
            user.set_password(serializer.validated_data['new_password'])
            user.save(update_fields=['password'])
            revoke_all_refresh_tokens(user)
        # Every other device is signed out; this one continues with a fresh session.
        return set_auth_cookies(Response(status=status.HTTP_204_NO_CONTENT), user)


class UserSearchView(ListAPIView):
    serializer_class = UserSerializer
    pagination_class = None

    def get_queryset(self):
        query = self.request.query_params.get('search', '')[:64].strip().lstrip('@')
        terms = query.split()[:3]
        if not terms:
            return User.objects.none()

        queryset = User.objects.filter(is_active=True).exclude(pk=self.request.user.pk)
        for term in terms:
            queryset = queryset.filter(
                Q(username__icontains=term) | Q(first_name__icontains=term) | Q(last_name__icontains=term)
            )
        first = terms[0]
        return queryset.annotate(
            rank=Case(
                When(username__iexact=first, then=0),
                When(username__istartswith=first, then=1),
                default=2,
                output_field=IntegerField(),
            )
        ).order_by('rank', 'username')[:20]


class PeopleView(ListAPIView):
    """Other people on the app, most recently active first (to start a chat in one tap)."""

    serializer_class = UserSerializer
    pagination_class = None

    def get_queryset(self):
        return (
            User.objects.filter(is_active=True, is_superuser=False)
            .exclude(pk=self.request.user.pk)
            .order_by(F('last_seen').desc(nulls_last=True), '-date_joined')[:20]
        )


class UserDetailView(RetrieveAPIView):
    serializer_class = UserSerializer
    queryset = User.objects.filter(is_active=True)

from django.conf import settings
from rest_framework.authentication import BaseAuthentication
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.utils import get_md5_hash_password

from .models import User
from .tokens import read_media_token


class CookieJWTAuthentication(JWTAuthentication):
    """
    Authenticate with the access token from the `Authorization: Bearer` header
    or, for the browser app, from the httpOnly access cookie.
    """

    def authenticate(self, request):
        header = self.get_header(request)
        if header is not None:
            raw_token = self.get_raw_token(header)
        else:
            raw_token = request.COOKIES.get(settings.AUTH_COOKIE_ACCESS) or None
        if raw_token is None:
            return None

        validated_token = self.get_validated_token(raw_token)
        return self.get_user(validated_token), validated_token


class MediaCookieAuthentication(BaseAuthentication):
    """
    Read-only access to chat files with the long-lived media cookie. Only used
    by the attachment views, so expired access tokens never break <img>/<video>.
    """

    def authenticate(self, request):
        value = request.COOKIES.get(settings.AUTH_COOKIE_MEDIA)
        payload = read_media_token(value) if value else None
        if not payload:
            return None
        user = User.objects.filter(pk=payload.get('user'), is_active=True).first()
        if user is None or payload.get('pwd') != get_md5_hash_password(user.password):
            return None
        return user, None

    def authenticate_header(self, request):
        # Makes DRF answer 401 (not 403) when no credentials were sent.
        return 'Bearer realm="api"'

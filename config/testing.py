"""Shared helpers for the backend test suites."""

import shutil
import tempfile
from io import BytesIO

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from PIL import Image
from rest_framework.test import APIClient, APITestCase

from chats.services import get_or_create_private_chat
from users.models import User

PASSWORD = 'Str0ng-pass-phrase!'

MP4_HEADER = b'\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom'
MOV_HEADER = b'\x00\x00\x00\x14ftypqt  \x00\x00\x00\x00qt  '
WEBM_HEADER = b'\x1a\x45\xdf\xa3\x9f\x42\x86\x81\x01'


def make_client():
    """An API client that behaves like the frontend (sends the CSRF header)."""
    client = APIClient()
    client.credentials(HTTP_X_REQUESTED_WITH='XMLHttpRequest')
    return client


def create_user(username='alice', password=PASSWORD, **extra):
    return User.objects.create_user(username=username, password=password, **extra)


def create_chat(user, other):
    return get_or_create_private_chat(user, other)[0]


def image_upload(name='photo.jpg', size=(800, 600), image_format='JPEG', **save_options):
    buffer = BytesIO()
    Image.new('RGB', size, (200, 60, 60)).save(buffer, format=image_format, **save_options)
    return SimpleUploadedFile(name, buffer.getvalue(), content_type=f'image/{image_format.lower()}')


def media_upload(name, header, size=2048, content_type='application/octet-stream'):
    """A file whose first bytes look like a real container (e.g. MP4_HEADER)."""
    return SimpleUploadedFile(name, header + b'\x00' * max(size - len(header), 0), content_type=content_type)


class ApiTestCase(APITestCase):
    """Isolated media folder + clean cache (rate limits) for every test."""

    @classmethod
    def setUpClass(cls):
        cls._media_root = tempfile.mkdtemp(prefix='chat-test-media-')
        cls._media_override = override_settings(MEDIA_ROOT=cls._media_root)
        cls._media_override.enable()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls._media_override.disable()
        shutil.rmtree(cls._media_root, ignore_errors=True)

    def setUp(self):
        cache.clear()
        self.client = make_client()

    def login(self, username='alice', password=PASSWORD, client=None):
        client = client or self.client
        response = client.post('/api/auth/login/', {'username': username, 'password': password}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        return response

    def client_for(self, username):
        client = make_client()
        self.login(username, client=client)
        return client

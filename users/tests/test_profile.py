from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from PIL import Image

from config.testing import ApiTestCase, create_user, image_upload

URL = '/api/users/me/'


class ProfileTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.user = create_user()
        self.login()

    def test_update_names_and_bio(self):
        response = self.client.patch(URL, {'first_name': 'Alice', 'last_name': 'Smith', 'bio': 'Hi!'}, format='json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['display_name'], 'Alice Smith')
        self.assertEqual(response.data['bio'], 'Hi!')

    def test_change_username(self):
        create_user('bob')
        taken = self.client.patch(URL, {'username': 'BOB'}, format='json')
        self.assertEqual(taken.status_code, 400)

        same_name_new_case = self.client.patch(URL, {'username': 'Alice'}, format='json')
        self.assertEqual(same_name_new_case.status_code, 200)
        self.assertEqual(same_name_new_case.data['username'], 'Alice')

    def test_avatar_is_converted_to_square_webp_without_metadata(self):
        exif = Image.Exif()
        exif[0x0112] = 6  # orientation: rotate 90°
        exif[0x010F] = 'PhoneMaker'
        response = self.client.patch(
            URL, {'avatar': image_upload(size=(1200, 800), exif=exif)}, format='multipart'
        )

        self.assertEqual(response.status_code, 200, response.data)
        url = response.data['avatar']
        self.assertRegex(url, r'^/media/avatars/[0-9a-f]{32}\.webp$')
        self.user.refresh_from_db()
        with default_storage.open(self.user.avatar.name) as stored, Image.open(stored) as image:
            self.assertEqual(image.format, 'WEBP')
            self.assertEqual(image.size, (512, 512))
            self.assertEqual(len(image.getexif()), 0)

    def test_new_avatar_replaces_and_deletes_the_old_file(self):
        self.client.patch(URL, {'avatar': image_upload()}, format='multipart')
        self.user.refresh_from_db()
        old_name = self.user.avatar.name

        with self.captureOnCommitCallbacks(execute=True):
            self.client.patch(URL, {'avatar': image_upload(image_format='PNG', name='a.png')}, format='multipart')

        self.user.refresh_from_db()
        self.assertNotEqual(self.user.avatar.name, old_name)
        self.assertFalse(default_storage.exists(old_name))
        self.assertTrue(default_storage.exists(self.user.avatar.name))

    def test_remove_avatar(self):
        self.client.patch(URL, {'avatar': image_upload()}, format='multipart')
        self.user.refresh_from_db()
        old_name = self.user.avatar.name

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.patch(URL, {'remove_avatar': True}, format='json')

        self.assertIsNone(response.data['avatar'])
        self.assertFalse(default_storage.exists(old_name))

    def test_non_image_avatar_is_rejected(self):
        fake = SimpleUploadedFile('evil.jpg', b'<script>alert(1)</script>', content_type='image/jpeg')
        response = self.client.patch(URL, {'avatar': fake}, format='multipart')

        self.assertEqual(response.status_code, 400)
        self.assertIn('avatar', response.data['errors'])

    @override_settings(AVATAR_MAX_UPLOAD_SIZE=1024)
    def test_too_large_avatar_is_rejected(self):
        response = self.client.patch(URL, {'avatar': image_upload(size=(600, 600))}, format='multipart')
        self.assertEqual(response.status_code, 400)

    def test_profile_requires_authentication(self):
        self.client.post('/api/auth/logout/')
        self.assertEqual(self.client.patch(URL, {'bio': 'x'}, format='json').status_code, 401)

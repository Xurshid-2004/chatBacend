from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from config.testing import MP4_HEADER, ApiTestCase, create_chat, create_user, image_upload, media_upload

CONTENT = bytes(range(256)) * 4  # 1024 known bytes


def body(response):
    return b''.join(response.streaming_content)


class AttachmentServingTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.alice = create_user('alice')
        self.bob = create_user('bob')
        create_user('carol')
        self.chat = create_chat(self.alice, self.bob)
        self.login('alice')
        self.file_message = self.upload(type='FILE', file=SimpleUploadedFile('notes.txt', CONTENT, 'text/plain'))
        self.url = self.file_message['attachment']['url']

    def upload(self, **data):
        response = self.client.post(f'/api/chats/{self.chat.id}/messages/', data, format='multipart')
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def test_member_downloads_document_with_safe_headers(self):
        response = self.client_for('bob').get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(body(response), CONTENT)
        self.assertEqual(response['Content-Type'], 'application/octet-stream')
        self.assertEqual(response['Content-Disposition'], 'attachment; filename="notes.txt"')
        self.assertEqual(response['Content-Length'], '1024')
        self.assertEqual(response['Accept-Ranges'], 'bytes')
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        self.assertIn('sandbox', response['Content-Security-Policy'])
        self.assertTrue(response['Cache-Control'].startswith('private'))

    def test_outsiders_cannot_download(self):
        self.assertEqual(self.client_for('carol').get(self.url).status_code, 404)
        self.assertEqual(APIClient().get(self.url).status_code, 401)

    def test_byte_ranges(self):
        cases = {
            'bytes=10-19': (CONTENT[10:20], 'bytes 10-19/1024'),
            'bytes=1000-': (CONTENT[1000:], 'bytes 1000-1023/1024'),
            'bytes=-24': (CONTENT[-24:], 'bytes 1000-1023/1024'),
            'bytes=1020-5000': (CONTENT[1020:], 'bytes 1020-1023/1024'),
        }
        for header, (expected, content_range) in cases.items():
            with self.subTest(range=header):
                response = self.client.get(self.url, HTTP_RANGE=header)
                self.assertEqual(response.status_code, 206)
                self.assertEqual(body(response), expected)
                self.assertEqual(response['Content-Range'], content_range)
                self.assertEqual(response['Content-Length'], str(len(expected)))

    def test_unsatisfiable_and_unsupported_ranges(self):
        response = self.client.get(self.url, HTTP_RANGE='bytes=2000-3000')
        self.assertEqual(response.status_code, 416)
        self.assertEqual(response['Content-Range'], 'bytes */1024')

        multi = self.client.get(self.url, HTTP_RANGE='bytes=0-1,5-6')  # not supported: whole file
        self.assertEqual(multi.status_code, 200)
        self.assertEqual(body(multi), CONTENT)

    def test_head_and_conditional_requests(self):
        head = self.client.head(self.url)
        self.assertEqual(head.status_code, 200)
        self.assertEqual(head['Content-Length'], '1024')

        etag = head['ETag']
        cached = self.client.get(self.url, HTTP_IF_NONE_MATCH=etag)
        self.assertEqual(cached.status_code, 304)

    def test_media_cookie_keeps_working_after_access_cookie_expires(self):
        del self.client.cookies['chat_access']
        self.assertEqual(self.client.get('/api/auth/me/').status_code, 401)
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_media_cookie_is_revoked_by_password_change(self):
        other_device = self.client_for('alice')
        del other_device.cookies['chat_access']
        self.assertEqual(other_device.get(self.url).status_code, 200)

        self.client.post(
            '/api/users/me/password/',
            {'current_password': 'Str0ng-pass-phrase!', 'new_password': 'Brand-new-pass-77'},
            format='json',
        )

        self.assertEqual(other_device.get(self.url).status_code, 401)
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_images_are_inline_unless_download_is_requested(self):
        image = self.upload(type='IMAGE', file=image_upload())
        inline = self.client.get(image['attachment']['url'])
        self.assertEqual(inline['Content-Type'], 'image/webp')
        self.assertTrue(inline['Content-Disposition'].startswith('inline'))

        download = self.client.get(image['attachment']['url'], {'download': '1'})
        self.assertTrue(download['Content-Disposition'].startswith('attachment'))

    def test_video_thumbnail(self):
        video = self.upload(type='VIDEO', file=media_upload('a.mp4', MP4_HEADER), thumbnail=image_upload('p.jpg'))
        response = self.client.get(video['attachment']['thumbnail_url'])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'image/webp')

        no_thumbnail = self.client.get(f"/api/messages/{self.file_message['id']}/thumbnail/")
        self.assertEqual(no_thumbnail.status_code, 404)

    async def test_streams_asynchronously_under_asgi(self):
        self.async_client.cookies = self.client.cookies
        response = await self.async_client.get(self.url, headers={'Range': 'bytes=0-99'})

        self.assertEqual(response.status_code, 206)
        self.assertEqual(b''.join([chunk async for chunk in response]), CONTENT[:100])

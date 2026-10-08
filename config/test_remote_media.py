from io import BytesIO
from types import SimpleNamespace
from unittest import mock

from django.test import RequestFactory, SimpleTestCase

from config import media

DATA = bytes(range(256)) * 40  # 10 KB


class FakeS3Storage:
    bucket_name = 'chat'

    def __init__(self):
        self.ranges = []
        client = SimpleNamespace(get_object=self.get_object)
        self.connection = SimpleNamespace(meta=SimpleNamespace(client=client))

    def size(self, name):
        return len(DATA)

    def _normalize_name(self, name):
        return name

    def get_object(self, Bucket, Key, Range):
        self.ranges.append(Range)
        start, end = (int(part) for part in Range.removeprefix('bytes=').split('-'))
        return {'Body': BytesIO(DATA[start : end + 1])}


class RemoteMediaTests(SimpleTestCase):
    def serve(self, **headers):
        request = RequestFactory().get('/x', headers=headers)
        with mock.patch.object(media, 'default_storage', self.storage):
            response = media.serve_file(request, 'images/a.webp', content_type='image/webp')
            body = b''.join(response.streaming_content) if response.streaming else response.content
        return response, body

    def setUp(self):
        self.storage = FakeS3Storage()

    def test_whole_file(self):
        response, body = self.serve()
        self.assertEqual((response.status_code, body), (200, DATA))
        self.assertEqual(response['Content-Length'], str(len(DATA)))
        self.assertEqual(self.storage.ranges, [f'bytes=0-{len(DATA) - 1}'])

    def test_byte_range_is_fetched_from_storage(self):
        response, body = self.serve(Range='bytes=100-199')
        self.assertEqual(response.status_code, 206)
        self.assertEqual(body, DATA[100:200])
        self.assertEqual(response['Content-Range'], f'bytes 100-199/{len(DATA)}')
        self.assertEqual(self.storage.ranges, ['bytes=100-199'])

    def test_not_modified_skips_the_download(self):
        etag = self.serve()[0]['ETag']
        self.storage.ranges.clear()
        response, _ = self.serve(If_None_Match=etag)
        self.assertEqual(response.status_code, 304)
        self.assertEqual(self.storage.ranges, [])

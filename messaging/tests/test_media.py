from io import BytesIO

from django.conf import settings
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from PIL import Image

from config.testing import (
    MOV_HEADER,
    MP4_HEADER,
    WEBM_HEADER,
    ApiTestCase,
    create_chat,
    create_user,
    image_upload,
    media_upload,
)
from messaging.media import clean_filename, sniff_container
from messaging.models import Message


def animated_gif():
    frames = [Image.new('RGB', (64, 48), color) for color in ((255, 0, 0), (0, 255, 0))]
    buffer = BytesIO()
    frames[0].save(buffer, format='GIF', save_all=True, append_images=frames[1:], duration=100, loop=0)
    return SimpleUploadedFile('fun.gif', buffer.getvalue(), content_type='image/gif')


class MediaMessageTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.alice = create_user('alice')
        self.chat = create_chat(self.alice, create_user('bob'))
        self.url = f'/api/chats/{self.chat.id}/messages/'
        self.login('alice')

    def send(self, **data):
        return self.client.post(self.url, data, format='multipart')

    def test_image_is_resized_to_webp_without_metadata(self):
        exif = Image.Exif()
        exif[0x010F] = 'PhoneMaker'
        response = self.send(type='IMAGE', text='Look!', file=image_upload(size=(3000, 2000), exif=exif))

        self.assertEqual(response.status_code, 201, response.data)
        attachment = response.data['attachment']
        self.assertEqual(response.data['text'], 'Look!')
        self.assertEqual(attachment['mime_type'], 'image/webp')
        self.assertEqual((attachment['width'], attachment['height']), (2048, 1365))
        self.assertEqual(attachment['name'], 'photo.webp')
        self.assertTrue(attachment['preview'].startswith('data:image/webp;base64,'))
        self.assertEqual(attachment['url'], f"/api/messages/{response.data['id']}/attachment/")

        message = Message.objects.get(pk=response.data['id'])
        self.assertEqual(attachment['size'], message.image.size)
        with default_storage.open(message.image.name) as stored, Image.open(stored) as image:
            self.assertEqual(image.format, 'WEBP')
            self.assertEqual(len(image.getexif()), 0)

    def test_animated_gif_keeps_its_animation(self):
        response = self.send(type='IMAGE', file=animated_gif())

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['attachment']['mime_type'], 'image/gif')
        self.assertEqual(response.data['attachment']['name'], 'fun.gif')
        message = Message.objects.get(pk=response.data['id'])
        with default_storage.open(message.image.name) as stored, Image.open(stored) as image:
            self.assertTrue(image.is_animated)

    def test_fake_image_is_rejected(self):
        fake = SimpleUploadedFile('cat.jpg', b'<html>not an image</html>', content_type='image/jpeg')
        response = self.send(type='IMAGE', file=fake)

        self.assertEqual(response.status_code, 400)
        self.assertIn('file', response.data['errors'])
        self.assertEqual(Message.objects.count(), 0)

    def test_video_with_thumbnail_and_metadata(self):
        response = self.send(
            type='VIDEO',
            file=media_upload('clip.MP4', MP4_HEADER, content_type='video/mp4'),
            thumbnail=image_upload('poster.jpg', size=(1280, 720)),
            duration='12.345',
            width=1920,
            height=1080,
        )

        self.assertEqual(response.status_code, 201, response.data)
        attachment = response.data['attachment']
        self.assertEqual(attachment['mime_type'], 'video/mp4')
        self.assertEqual(attachment['name'], 'clip.mp4')
        self.assertEqual(attachment['duration'], 12.35)
        self.assertEqual((attachment['width'], attachment['height']), (1920, 1080))
        self.assertEqual(attachment['thumbnail_url'], f"/api/messages/{response.data['id']}/thumbnail/")
        self.assertTrue(attachment['preview'])

    def test_video_size_falls_back_to_thumbnail_size(self):
        response = self.send(
            type='VIDEO',
            file=media_upload('clip.mov', MOV_HEADER),
            thumbnail=image_upload('poster.jpg', size=(360, 640)),
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['attachment']['mime_type'], 'video/quicktime')
        self.assertEqual((response.data['attachment']['width'], response.data['attachment']['height']), (360, 640))

    def test_non_video_file_is_rejected_as_video(self):
        response = self.send(type='VIDEO', file=media_upload('clip.mp4', b'just some text'))
        self.assertEqual(response.status_code, 400)

    def test_voice_message_with_waveform(self):
        response = self.send(
            type='AUDIO',
            file=media_upload('voice.webm', WEBM_HEADER, content_type='audio/webm'),
            duration=3.5,
            waveform='[0, 0.12345, 1]',
        )

        self.assertEqual(response.status_code, 201, response.data)
        attachment = response.data['attachment']
        self.assertEqual(attachment['mime_type'], 'audio/webm')
        self.assertEqual(attachment['waveform'], [0.0, 0.123, 1.0])
        self.assertEqual(attachment['duration'], 3.5)

    def test_invalid_waveforms_are_rejected(self):
        voice = lambda: media_upload('voice.webm', WEBM_HEADER)  # noqa: E731
        for waveform in ('[2]', '[-1]', '"loud"', '[true]', str([0.5] * 129)):
            with self.subTest(waveform=waveform):
                self.assertEqual(self.send(type='AUDIO', file=voice(), waveform=waveform).status_code, 400)
        image = self.send(type='IMAGE', file=image_upload(), waveform='[0.5]')
        self.assertEqual(image.status_code, 400)

    def test_document_keeps_a_safe_original_name(self):
        document = SimpleUploadedFile('../../Hisobot <2026>.pdf', b'%PDF-1.7 test', content_type='application/pdf')
        response = self.send(type='FILE', file=document)

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['attachment']['name'], 'Hisobot 2026.pdf')
        self.assertEqual(response.data['attachment']['mime_type'], 'application/pdf')
        self.assertEqual(response.data['attachment']['size'], 13)
        stored = Message.objects.get(pk=response.data['id']).file.name
        self.assertRegex(stored, r'^files/\d{4}/\d{2}/[0-9a-f]{32}\.pdf$')

    def test_type_and_file_must_match(self):
        self.assertEqual(self.send(type='TEXT', text='hi', file=image_upload()).status_code, 400)
        self.assertEqual(self.send(type='IMAGE', text='no file').status_code, 400)
        self.assertEqual(self.send(type='IMAGE', file=image_upload(), thumbnail=image_upload()).status_code, 400)

    def test_per_type_size_limit(self):
        limits = {**settings.CHAT_UPLOAD_LIMITS, 'FILE': 10}
        with override_settings(CHAT_UPLOAD_LIMITS=limits):
            response = self.send(type='FILE', file=SimpleUploadedFile('big.bin', b'x' * 11))
        self.assertEqual(response.status_code, 400)

    @override_settings(CHAT_MAX_REQUEST_SIZE=1024)
    def test_oversized_request_is_refused_before_parsing(self):
        response = self.send(type='FILE', file=SimpleUploadedFile('big.bin', b'x' * 4096))
        self.assertEqual(response.status_code, 413)

    def test_deleting_a_message_removes_its_files(self):
        response = self.send(
            type='VIDEO', file=media_upload('clip.mp4', MP4_HEADER), thumbnail=image_upload('poster.jpg')
        )
        message = Message.objects.get(pk=response.data['id'])
        names = message.stored_file_names()
        self.assertEqual(len(names), 2)

        with self.captureOnCommitCallbacks(execute=True):
            self.client.delete(f'/api/messages/{message.id}/')

        for name in names:
            self.assertFalse(default_storage.exists(name))


class MediaHelperTests(ApiTestCase):
    def test_container_sniffing(self):
        cases = {
            MP4_HEADER: 'mp4',
            MOV_HEADER: 'quicktime',
            WEBM_HEADER: 'webm',
            b'OggS\x00\x02': 'ogg',
            b'ID3\x04\x00': 'mpeg',
            b'\xff\xfb\x90\x64': 'mpeg',
            b'\xff\xf1\x50\x80': 'aac',
            b'RIFF\x24\x00\x00\x00WAVEfmt ': 'wav',
            b'fLaC\x00\x00': 'flac',
            b'<!DOCTYPE html>': None,
        }
        for head, expected in cases.items():
            with self.subTest(head=head):
                self.assertEqual(sniff_container(head), expected)

    def test_clean_filename(self):
        self.assertEqual(clean_filename('C:\\Users\\me\\a.txt'), 'a.txt')
        self.assertEqual(clean_filename('  ..  '), 'file')
        self.assertEqual(clean_filename('ok\x00name?.zip'), 'okname.zip')
        self.assertEqual(len(clean_filename('a' * 300 + '.txt')), 200)
        self.assertTrue(clean_filename('a' * 300 + '.txt').endswith('.txt'))

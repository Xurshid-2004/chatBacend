from unittest import mock

from django.core.files.storage import default_storage

from chats.models import Chat, ChatMember
from config.testing import ApiTestCase, create_chat, create_user, image_upload, make_client
from messaging.models import Message
from users.models import User


class DeleteAccountTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.alice = create_user('alice')
        self.bob = create_user('bob')
        self.carol = create_user('carol')
        self.chat = create_chat(self.alice, self.bob)
        self.other_chat = create_chat(self.bob, self.carol)
        self.login('alice')

    def delete(self, client=None):
        with self.captureOnCommitCallbacks(execute=True):
            return (client or self.client).delete('/api/users/me/')

    def test_deletes_the_account_with_its_chats_messages_and_files(self):
        self.client.patch('/api/users/me/', {'avatar': image_upload()}, format='multipart')
        photo = self.client.post(f'/api/chats/{self.chat.id}/messages/', {'type': 'IMAGE', 'file': image_upload()}, format='multipart')
        bob = self.client_for('bob')
        bob.post(f'/api/chats/{self.chat.id}/messages/', {'text': 'hi'}, format='json')
        files = [User.objects.get(pk=self.alice.pk).avatar.name, *Message.objects.get(pk=photo.data['id']).stored_file_names()]
        self.assertTrue(all(default_storage.exists(name) for name in files))

        response = self.delete()

        self.assertEqual(response.status_code, 204)
        self.assertFalse(User.objects.filter(pk=self.alice.pk).exists())
        self.assertFalse(Chat.objects.filter(pk=self.chat.pk).exists())
        self.assertFalse(Message.objects.filter(chat_id=self.chat.pk).exists())
        self.assertFalse(any(default_storage.exists(name) for name in files), files)
        for cookie in ('chat_access', 'chat_refresh', 'chat_media', 'chat_session'):
            self.assertEqual(response.cookies[cookie].value, '')

    def test_other_people_and_their_chats_stay(self):
        bob = self.client_for('bob')
        bob.post(f'/api/chats/{self.other_chat.id}/messages/', {'text': 'hi carol'}, format='json')
        self.delete()
        self.assertTrue(User.objects.filter(pk=self.bob.pk).exists())
        self.assertTrue(ChatMember.objects.filter(chat=self.other_chat, user=self.carol).exists())
        self.assertEqual([chat['id'] for chat in bob.get('/api/chats/').data['results']], [self.other_chat.id])
        self.assertEqual(bob.get(f'/api/chats/{self.chat.id}/').status_code, 404)

    def test_partners_and_other_devices_are_told(self):
        with mock.patch('chats.realtime.send_to_users') as send:
            self.delete()
        send.assert_any_call([self.bob.pk], {'type': 'chat.deleted', 'chat_id': self.chat.id})
        send.assert_any_call([self.alice.pk], {'type': 'account.deleted'})

    def test_every_session_ends(self):
        other_device = self.client_for('alice')
        self.delete()
        self.assertEqual(other_device.get('/api/auth/me/').status_code, 401)
        self.assertEqual(other_device.post('/api/auth/refresh/').status_code, 401)

    def test_needs_a_session(self):
        self.assertEqual(make_client().delete('/api/users/me/').status_code, 401)

    def test_admin_accounts_are_kept(self):
        User.objects.filter(pk=self.alice.pk).update(is_staff=True)
        self.assertEqual(self.delete().status_code, 403)
        self.assertTrue(User.objects.filter(pk=self.alice.pk).exists())

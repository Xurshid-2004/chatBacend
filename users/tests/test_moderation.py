from django.test import override_settings

from chats.models import Chat
from config.testing import ApiTestCase, create_chat, create_user, make_client
from users.models import User

PASSWORD = 'admin-pass-123'


@override_settings(CHAT_ADMIN_PASSWORD=PASSWORD)
class ModerationTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.alice = create_user('alice')
        self.bob = create_user('bob')
        self.carol = create_user('carol')
        self.chat = create_chat(self.bob, self.carol)
        self.login('alice')

    def unlock(self, password=PASSWORD, client=None):
        return (client or self.client).post('/api/moderation/unlock/', {'password': password}, format='json')

    def test_locked_until_the_password_is_given(self):
        self.assertEqual(self.client.get('/api/moderation/status/').data, {'enabled': True, 'unlocked': False})
        self.assertEqual(self.client.get('/api/moderation/users/').status_code, 403)
        self.assertEqual(self.unlock('wrong').status_code, 400)
        self.assertEqual(self.unlock().status_code, 200)
        self.assertTrue(self.client.get('/api/moderation/status/').data['unlocked'])

    def test_lists_everyone_and_deletes_accounts_with_their_chats(self):
        self.unlock()
        people = self.client.get('/api/moderation/users/').data
        self.assertEqual({person['username'] for person in people}, {'alice', 'bob', 'carol'})
        bob_row = next(person for person in people if person['username'] == 'bob')
        self.assertEqual(bob_row['chats'], 1)

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.delete(f'/api/moderation/users/{self.bob.pk}/')
        self.assertEqual(response.status_code, 204)
        self.assertFalse(User.objects.filter(pk=self.bob.pk).exists())
        self.assertFalse(Chat.objects.filter(pk=self.chat.pk).exists())
        self.assertEqual(self.client.delete(f'/api/moderation/users/{self.bob.pk}/').status_code, 404)

    def test_cannot_delete_yourself_or_admins_here(self):
        self.unlock()
        self.assertEqual(self.client.delete(f'/api/moderation/users/{self.alice.pk}/').status_code, 400)
        staff = create_user('staff', is_staff=True)
        self.assertEqual(self.client.delete(f'/api/moderation/users/{staff.pk}/').status_code, 404)

    def test_the_unlock_belongs_to_one_account(self):
        self.unlock()
        cookie = self.client.cookies['chat_admin'].value
        bob = self.client_for('bob')
        bob.cookies['chat_admin'] = cookie
        self.assertEqual(bob.get('/api/moderation/users/').status_code, 403)

    def test_lock_and_brute_force_protection(self):
        self.unlock()
        self.client.post('/api/moderation/lock/')
        self.assertFalse(self.client.get('/api/moderation/status/').data['unlocked'])
        for _ in range(10):
            self.unlock('nope')
        self.assertEqual(self.unlock().status_code, 429)

    def test_needs_a_session(self):
        self.assertEqual(self.unlock(client=make_client()).status_code, 401)


class ModerationOffTests(ApiTestCase):
    def test_off_without_a_password(self):
        create_user('alice')
        self.login('alice')
        self.assertEqual(self.client.get('/api/moderation/status/').data, {'enabled': False, 'unlocked': False})
        self.assertEqual(self.client.post('/api/moderation/unlock/', {'password': ''}, format='json').status_code, 403)

from config.testing import ApiTestCase, create_user, make_client
from users.models import User
from users.names import username_base
from users.presets import AVATAR_PRESETS


class StartTests(ApiTestCase):
    def start(self, client=None, **data):
        payload = {'name': 'Alisher', 'avatar_preset': 'fox', **data}
        return (client or self.client).post('/api/auth/guest/', payload, format='json')

    def test_name_and_avatar_create_an_account_and_sign_in(self):
        response = self.start()

        self.assertEqual(response.status_code, 201)
        user = User.objects.get(pk=response.data['user']['id'])
        self.assertFalse(user.has_usable_password())
        self.assertRegex(user.username, r'^alisher_\d{4}$')
        self.assertEqual(response.data['user']['display_name'], 'Alisher')
        self.assertEqual(response.data['user']['avatar_preset'], 'fox')
        for cookie in ('chat_access', 'chat_refresh', 'chat_media', 'chat_session'):
            self.assertTrue(response.cookies[cookie].value)
        self.assertEqual(self.client.get('/api/auth/me/').data['id'], user.id)

    def test_same_name_gets_different_usernames(self):
        usernames = {self.start(make_client()).data['user']['username'] for _ in range(5)}
        self.assertEqual(len(usernames), 5)

    def test_avatar_is_picked_when_not_chosen(self):
        response = self.client.post('/api/auth/guest/', {'name': 'Bob'}, format='json')
        self.assertIn(response.data['user']['avatar_preset'], AVATAR_PRESETS)

    def test_name_is_required_and_avatar_must_be_known(self):
        self.assertEqual(self.start(name='   ').status_code, 400)
        self.assertEqual(self.start(name='x' * 41).status_code, 400)
        self.assertEqual(self.start(avatar_preset='dragon').status_code, 400)

    def test_session_refreshes_like_any_other(self):
        self.start()
        self.assertEqual(self.client.post('/api/auth/refresh/').status_code, 204)

    def test_usernames_from_any_alphabet(self):
        self.assertEqual(username_base('Алишер Ғулом'), 'alisher_gulom')
        self.assertEqual(username_base("O'tkir"), 'o_tkir')
        self.assertEqual(username_base('Zoë'), 'zoe')
        self.assertIsNone(username_base('李雷'))
        self.assertIsNone(username_base('42'))
        response = self.start(name='李雷')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['user']['display_name'], '李雷')
        self.assertRegex(response.data['user']['username'], r'^[a-z]+_[a-z]+_\d{4}$')

    def test_avatar_can_be_changed_in_the_profile(self):
        self.start()
        response = self.client.patch('/api/users/me/', {'avatar_preset': 'owl'}, format='json')
        self.assertEqual(response.data['avatar_preset'], 'owl')


class PeopleTests(ApiTestCase):
    def test_lists_other_active_people(self):
        create_user('alice')
        create_user('bob')
        create_user('ghost', is_active=False)
        User.objects.create_superuser('admin_user', password='Adm1n-pass-phrase')
        self.login('alice')

        response = self.client.get('/api/users/people/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual([person['username'] for person in response.data], ['bob'])

    def test_people_requires_authentication(self):
        self.assertEqual(self.client.get('/api/users/people/').status_code, 401)

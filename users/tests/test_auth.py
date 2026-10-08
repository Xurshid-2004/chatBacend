from django.test import override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from config.testing import PASSWORD, ApiTestCase, create_user, make_client

ACCESS, REFRESH, SESSION = 'chat_access', 'chat_refresh', 'chat_session'


class RegisterTests(ApiTestCase):
    def register(self, **data):
        payload = {'username': 'alice', 'password': PASSWORD, **data}
        return self.client.post('/api/auth/register/', payload, format='json')

    def test_register_creates_user_and_sets_http_only_cookies(self):
        response = self.register(first_name='Alice')

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['user']['username'], 'alice')
        self.assertEqual(response.data['user']['display_name'], 'Alice')
        for name in (ACCESS, REFRESH, SESSION):
            cookie = response.cookies[name]
            self.assertTrue(cookie.value)
            self.assertTrue(cookie['httponly'])
            self.assertEqual(cookie['samesite'], 'Lax')
        self.assertEqual(response.cookies[REFRESH]['path'], '/api/auth/')
        self.assertEqual(response.cookies[ACCESS]['path'], '/')

    def test_username_is_unique_ignoring_case(self):
        create_user('Alice')
        response = self.register(username='alice')

        self.assertEqual(response.status_code, 400)
        self.assertIn('username', response.data['errors'])
        self.assertEqual(response.data['detail'], 'This username is already taken.')

    def test_invalid_usernames_are_rejected(self):
        for username in ('ab', '1alice', 'bad name', 'a' * 33, 'ali-ce'):
            with self.subTest(username=username):
                self.assertEqual(self.register(username=username).status_code, 400)

    def test_weak_password_is_rejected(self):
        for password in ('123', 'password', '1234567890'):
            with self.subTest(password=password):
                response = self.register(password=password)
                self.assertEqual(response.status_code, 400)
                self.assertIn('password', response.data['errors'])


class CsrfHeaderTests(ApiTestCase):
    def test_unsafe_request_without_header_is_rejected(self):
        client = APIClient()  # no X-Requested-With header
        response = client.post('/api/auth/login/', {'username': 'x', 'password': 'y'}, format='json')

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['code'], 'csrf_failed')

    def test_bearer_requests_do_not_need_the_header(self):
        user = create_user()
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {RefreshToken.for_user(user).access_token}')

        response = client.patch('/api/users/me/', {'bio': 'hello'}, format='json')

        self.assertEqual(response.status_code, 200)


class LoginTests(ApiTestCase):
    def test_login_ignores_username_case(self):
        create_user('Alice')
        response = self.login(username='ALICE')

        self.assertEqual(response.data['user']['username'], 'Alice')
        self.assertTrue(response.cookies[ACCESS].value)

    def test_wrong_password_and_unknown_user_give_the_same_error(self):
        create_user()
        wrong = self.client.post('/api/auth/login/', {'username': 'alice', 'password': 'nope'}, format='json')
        unknown = self.client.post('/api/auth/login/', {'username': 'bob', 'password': 'nope'}, format='json')

        self.assertEqual(wrong.status_code, 400)
        self.assertEqual(unknown.status_code, 400)
        self.assertEqual(wrong.data['detail'], 'Invalid username or password.')
        self.assertEqual(wrong.data['detail'], unknown.data['detail'])

    @override_settings(LOGIN_MAX_FAILURES=3)
    def test_account_is_locked_after_repeated_failures(self):
        create_user()
        for _ in range(3):
            self.client.post('/api/auth/login/', {'username': 'alice', 'password': 'nope'}, format='json')

        response = self.client.post('/api/auth/login/', {'username': 'alice', 'password': PASSWORD}, format='json')

        self.assertEqual(response.status_code, 429)
        self.assertIn('Retry-After', response)

    def test_inactive_user_cannot_login(self):
        create_user(is_active=False)
        response = self.client.post('/api/auth/login/', {'username': 'alice', 'password': PASSWORD}, format='json')
        self.assertEqual(response.status_code, 400)

    def test_stale_access_cookie_does_not_block_login(self):
        create_user()
        self.client.cookies[ACCESS] = 'not-a-valid-token'
        self.login()


class SessionTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.user = create_user()

    def test_me_requires_authentication(self):
        response = self.client.get('/api/auth/me/')
        self.assertEqual(response.status_code, 401)

    def test_me_returns_current_user(self):
        self.login()
        response = self.client.get('/api/auth/me/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['id'], self.user.id)

    def test_refresh_rotates_and_old_refresh_token_stops_working(self):
        self.login()
        old_refresh = self.client.cookies[REFRESH].value

        response = self.client.post('/api/auth/refresh/')
        self.assertEqual(response.status_code, 204)
        self.assertNotEqual(response.cookies[REFRESH].value, old_refresh)
        self.assertEqual(self.client.get('/api/auth/me/').status_code, 200)

        self.client.cookies[REFRESH] = old_refresh
        reused = self.client.post('/api/auth/refresh/')
        self.assertEqual(reused.status_code, 401)
        self.assertEqual(reused.data['code'], 'session_expired')
        self.assertEqual(reused.cookies[ACCESS].value, '')

    def test_refresh_without_cookie_is_unauthorized(self):
        response = self.client.post('/api/auth/refresh/')
        self.assertEqual(response.status_code, 401)

    def test_refresh_fails_for_deactivated_user(self):
        self.login()
        self.user.is_active = False
        self.user.save()
        self.assertEqual(self.client.post('/api/auth/refresh/').status_code, 401)

    def test_logout_revokes_refresh_token_and_clears_cookies(self):
        self.login()
        refresh = self.client.cookies[REFRESH].value

        response = self.client.post('/api/auth/logout/')
        self.assertEqual(response.status_code, 204)
        for name in (ACCESS, REFRESH, SESSION):
            self.assertEqual(response.cookies[name].value, '')

        self.client.cookies[REFRESH] = refresh
        self.assertEqual(self.client.post('/api/auth/refresh/').status_code, 401)


class PasswordChangeTests(ApiTestCase):
    url = '/api/users/me/password/'

    def setUp(self):
        super().setUp()
        self.user = create_user()

    def test_password_change_signs_out_other_devices(self):
        other_device = make_client()
        self.login(client=other_device)
        self.login()

        response = self.client.post(
            self.url, {'current_password': PASSWORD, 'new_password': 'An0ther-strong-one'}, format='json'
        )
        self.assertEqual(response.status_code, 204)

        # This device continues with its fresh session...
        self.assertEqual(self.client.get('/api/auth/me/').status_code, 200)
        # ...the other device is signed out at once (access + refresh).
        self.assertEqual(other_device.get('/api/auth/me/').status_code, 401)
        self.assertEqual(other_device.post('/api/auth/refresh/').status_code, 401)
        # And the new password works.
        self.login(password='An0ther-strong-one', client=make_client())

    def test_wrong_current_password_is_rejected(self):
        self.login()
        response = self.client.post(
            self.url, {'current_password': 'wrong', 'new_password': 'An0ther-strong-one'}, format='json'
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('current_password', response.data['errors'])

    def test_weak_new_password_is_rejected(self):
        self.login()
        response = self.client.post(self.url, {'current_password': PASSWORD, 'new_password': '123'}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('new_password', response.data['errors'])

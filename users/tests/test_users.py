from config.testing import ApiTestCase, create_user, image_upload
from users.models import User


class UserSearchTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        create_user('alice')
        create_user('alex', first_name='Alexander', last_name='Valiyev')
        create_user('ghost_al', is_active=False)
        create_user('bob', first_name='Ali')
        self.login('alice')

    def search(self, query):
        response = self.client.get('/api/users/', {'search': query})
        self.assertEqual(response.status_code, 200)
        return [user['username'] for user in response.data]

    def test_search_excludes_self_and_inactive_users(self):
        self.assertEqual(self.search('al'), ['alex', 'bob'])

    def test_exact_and_prefix_username_matches_come_first(self):
        create_user('xalex')
        self.assertEqual(self.search('alex')[:2], ['alex', 'xalex'])

    def test_multi_word_search_matches_full_name(self):
        self.assertEqual(self.search('alexander valiyev'), ['alex'])

    def test_at_sign_is_ignored_and_empty_query_returns_nothing(self):
        self.assertEqual(self.search('@bob'), ['bob'])
        self.assertEqual(self.search('   '), [])

    def test_search_requires_authentication(self):
        self.client.post('/api/auth/logout/')
        self.assertEqual(self.client.get('/api/users/', {'search': 'a'}).status_code, 401)


class UserDetailTests(ApiTestCase):
    def test_public_profile(self):
        bob = create_user('bob', bio='Hello')
        ghost = create_user('ghost', is_active=False)
        create_user('alice')
        self.login('alice')

        response = self.client.get(f'/api/users/{bob.id}/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['bio'], 'Hello')
        self.assertNotIn('password', response.data)
        self.assertEqual(self.client.get(f'/api/users/{ghost.id}/').status_code, 404)


class AvatarServingTests(ApiTestCase):
    def test_avatar_is_served_with_safe_cache_headers(self):
        create_user('alice')
        self.login('alice')
        url = self.client.patch('/api/users/me/', {'avatar': image_upload()}, format='multipart').data['avatar']

        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'image/webp')
        self.assertIn('immutable', response['Cache-Control'])
        self.assertIn('sandbox', response['Content-Security-Policy'])
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')

    def test_other_media_folders_are_not_public(self):
        self.assertEqual(self.client.get('/media/images/secret.jpg').status_code, 404)
        self.assertEqual(self.client.get('/media/avatars/../settings.py').status_code, 404)


class AdminTests(ApiTestCase):
    def test_admin_can_list_and_create_users(self):
        admin = User.objects.create_superuser('admin_user', password='Adm1n-pass-phrase')
        self.client.force_login(admin)

        self.assertEqual(self.client.get('/admin/users/user/').status_code, 200)
        self.assertEqual(self.client.get('/admin/users/user/add/').status_code, 200)
        response = self.client.post(
            '/admin/users/user/add/',
            {
                'username': 'created_in_admin',
                'usable_password': 'true',
                'password1': 'Adm1n-made-pass',
                'password2': 'Adm1n-made-pass',
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.filter(username='created_in_admin').exists())

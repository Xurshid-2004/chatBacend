from chats.models import Chat
from config.testing import ApiTestCase, create_chat, create_user


class ChatApiTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.alice = create_user('alice')
        self.bob = create_user('bob', first_name='Bob')
        self.carol = create_user('carol')
        self.login('alice')

    def start_chat(self, user, client=None):
        return (client or self.client).post('/api/chats/', {'user_id': user.id}, format='json')

    def send(self, chat_id, text, client=None):
        response = (client or self.client).post(f'/api/chats/{chat_id}/messages/', {'text': text}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def chat_list(self, client=None):
        response = (client or self.client).get('/api/chats/')
        self.assertEqual(response.status_code, 200)
        return response.data['results']

    def test_starting_a_chat_is_idempotent(self):
        first = self.start_chat(self.bob)
        second = self.start_chat(self.bob)

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.data['id'], second.data['id'])
        self.assertEqual(first.data['peer']['username'], 'bob')
        self.assertIsNone(first.data['last_message'])
        self.assertEqual(first.data['unread_count'], 0)

    def test_both_users_share_one_chat(self):
        chat_id = self.start_chat(self.bob).data['id']
        from_bob = self.start_chat(self.alice, client=self.client_for('bob'))

        self.assertEqual(from_bob.data['id'], chat_id)
        self.assertEqual(from_bob.data['peer']['username'], 'alice')
        self.assertEqual(Chat.objects.count(), 1)

    def test_invalid_chat_partners_are_rejected(self):
        ghost = create_user('ghost', is_active=False)
        for user_id in (self.alice.id, ghost.id, 999999, 'abc'):
            with self.subTest(user_id=user_id):
                response = self.client.post('/api/chats/', {'user_id': user_id}, format='json')
                self.assertEqual(response.status_code, 400)

    def test_list_shows_chats_with_messages_newest_first(self):
        bob_chat = self.start_chat(self.bob).data['id']
        carol_chat = self.start_chat(self.carol).data['id']
        self.assertEqual(self.chat_list(), [])

        self.send(bob_chat, 'hi bob')
        self.send(carol_chat, 'hi carol')
        chats = self.chat_list()
        self.assertEqual([chat['id'] for chat in chats], [carol_chat, bob_chat])
        self.assertEqual(chats[0]['last_message']['text'], 'hi carol')
        self.assertEqual(chats[1]['peer']['display_name'], 'Bob')

        self.send(bob_chat, 'again')
        self.assertEqual([chat['id'] for chat in self.chat_list()], [bob_chat, carol_chat])

    def test_unread_count_only_counts_incoming_unread_messages(self):
        chat = create_chat(self.alice, self.bob)
        bob = self.client_for('bob')
        self.send(chat.id, 'one', client=bob)
        self.send(chat.id, 'two', client=bob)
        self.send(chat.id, 'mine')

        self.assertEqual(self.chat_list()[0]['unread_count'], 2)
        self.assertEqual(self.chat_list(client=bob)[0]['unread_count'], 1)

    def test_list_does_not_repeat_queries_per_chat(self):
        for user in (self.bob, self.carol, create_user('dave'), create_user('erin')):
            self.send(create_chat(self.alice, user).id, 'hello')

        # session-less JWT auth (1) + chats (1) + participants (1) + last messages (1)
        with self.assertNumQueries(4):
            self.assertEqual(len(self.chat_list()), 4)

    def test_non_member_cannot_open_chat(self):
        chat = create_chat(self.bob, self.carol)
        self.assertEqual(self.client.get(f'/api/chats/{chat.id}/').status_code, 404)

    def test_chat_detail(self):
        chat = create_chat(self.alice, self.bob)
        response = self.client.get(f'/api/chats/{chat.id}/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['peer']['id'], self.bob.id)

    def test_chats_require_authentication(self):
        self.client.post('/api/auth/logout/')
        self.assertEqual(self.client.get('/api/chats/').status_code, 401)

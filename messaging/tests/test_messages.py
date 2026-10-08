import uuid

from chats.models import Chat
from config.testing import ApiTestCase, create_chat, create_user
from messaging.models import Message


class MessageApiTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.alice = create_user('alice')
        self.bob = create_user('bob')
        self.carol = create_user('carol')
        self.chat = create_chat(self.alice, self.bob)
        self.url = f'/api/chats/{self.chat.id}/messages/'
        self.login('alice')

    def send(self, text='hello', client=None, **extra):
        return (client or self.client).post(self.url, {'text': text, **extra}, format='json')

    def test_send_text_message(self):
        before = Chat.objects.get(pk=self.chat.pk).updated_at
        response = self.send('  Hello, Bob!  ')

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['text'], 'Hello, Bob!')
        self.assertEqual(response.data['type'], 'TEXT')
        self.assertEqual(response.data['sender'], self.alice.id)
        self.assertIsNone(response.data['attachment'])
        self.assertFalse(response.data['is_read'])
        self.assertGreater(Chat.objects.get(pk=self.chat.pk).updated_at, before)

    def test_empty_and_too_long_text_is_rejected(self):
        self.assertEqual(self.send('   ').status_code, 400)
        self.assertEqual(self.send('x' * 4097).status_code, 400)
        self.assertEqual(self.send('x' * 4096).status_code, 201)

    def test_client_id_makes_sending_idempotent(self):
        client_id = str(uuid.uuid4())
        first = self.send('once', client_id=client_id)
        retry = self.send('once', client_id=client_id)

        self.assertEqual(first.status_code, 201)
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(first.data['id'], retry.data['id'])
        self.assertEqual(first.data['client_id'], client_id)
        self.assertEqual(Message.objects.count(), 1)

    def test_reply_must_point_to_a_message_in_the_same_chat(self):
        original = self.send('question').data
        other_chat = create_chat(self.alice, self.carol)
        foreign = self.client.post(f'/api/chats/{other_chat.id}/messages/', {'text': 'x'}, format='json').data

        reply = self.send('answer', reply_to=original['id'])
        self.assertEqual(reply.status_code, 201)
        self.assertEqual(reply.data['reply_to']['id'], original['id'])
        self.assertEqual(reply.data['reply_to']['text'], 'question')

        self.assertEqual(self.send('nope', reply_to=foreign['id']).status_code, 400)

    def test_history_pages_backwards_and_catches_up_forwards(self):
        ids = [self.send(f'message {number}').data['id'] for number in range(5)]

        latest = self.client.get(self.url, {'limit': 2}).data
        self.assertEqual([m['id'] for m in latest['results']], ids[3:])
        self.assertTrue(latest['has_more'])

        older = self.client.get(self.url, {'limit': 2, 'before': ids[3]}).data
        self.assertEqual([m['id'] for m in older['results']], ids[1:3])
        oldest = self.client.get(self.url, {'limit': 2, 'before': ids[1]}).data
        self.assertEqual([m['id'] for m in oldest['results']], ids[:1])
        self.assertFalse(oldest['has_more'])

        newer = self.client.get(self.url, {'after': ids[1]}).data
        self.assertEqual([m['id'] for m in newer['results']], ids[2:])
        self.assertFalse(newer['has_more'])

        self.assertEqual(self.client.get(self.url, {'before': 'abc'}).status_code, 400)

    def test_non_member_can_neither_read_nor_send(self):
        carol = self.client_for('carol')
        self.assertEqual(carol.get(self.url).status_code, 404)
        self.assertEqual(self.send('intruder', client=carol).status_code, 404)

    def test_mark_read_only_touches_incoming_messages(self):
        bob = self.client_for('bob')
        incoming = [self.send(f'from bob {n}', client=bob).data['id'] for n in range(3)]
        mine = self.send('from alice').data['id']
        read_url = f'/api/chats/{self.chat.id}/read/'

        partial = self.client.post(read_url, {'up_to': incoming[1]}, format='json')
        self.assertEqual(partial.data, {'updated': 2, 'last_read_id': incoming[1]})

        rest = self.client.post(read_url, {}, format='json')
        self.assertEqual(rest.data, {'updated': 1, 'last_read_id': incoming[2]})
        self.assertFalse(Message.objects.get(pk=mine).is_read)

        nothing = self.client.post(read_url, {}, format='json')
        self.assertEqual(nothing.data, {'updated': 0, 'last_read_id': None})

    def test_only_the_sender_can_delete_a_message(self):
        bob = self.client_for('bob')
        bobs = self.send('bob says', client=bob).data['id']
        mine = self.send('alice says').data['id']
        reply = self.send('reply', reply_to=mine).data['id']

        self.assertEqual(self.client.delete(f'/api/messages/{bobs}/').status_code, 403)
        self.assertEqual(self.client.delete(f'/api/messages/{mine}/').status_code, 204)
        self.assertFalse(Message.objects.filter(pk=mine).exists())
        self.assertIsNone(Message.objects.get(pk=reply).reply_to_id)
        self.assertEqual(self.client_for('carol').delete(f'/api/messages/{bobs}/').status_code, 404)

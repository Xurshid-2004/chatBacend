import asyncio
from unittest import mock
from datetime import timedelta

from asgiref.sync import sync_to_async
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.test import override_settings
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from chats import presence
from config.asgi import application
from config.testing import ApiTestCase, create_chat, create_user
from users.models import User

ORIGIN = (b'origin', b'http://localhost:3000')


@override_settings(PRESENCE_OFFLINE_GRACE_SECONDS=0)
class RealtimeTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.alice = create_user('alice')
        self.bob = create_user('bob', first_name='Bob')
        self.carol = create_user('carol')
        self.chat = create_chat(self.alice, self.bob)
        self.sockets = []

    # --- helpers ---------------------------------------------------------

    async def open_socket(self, token):
        headers = [ORIGIN]
        if token:
            headers.append((b'cookie', f'chat_access={token}'.encode()))
        socket = WebsocketCommunicator(application, '/ws/', headers=headers)
        connected, _ = await socket.connect()
        self.assertTrue(connected)
        self.sockets.append(socket)
        return socket

    async def connect(self, user):
        socket = await self.open_socket(str(AccessToken.for_user(user)))
        self.assertEqual((await socket.receive_json_from())['type'], 'ready')
        return socket

    async def rest(self, username, method, url, data=None):
        """Call the REST API as `username`, running on-commit hooks (the broadcasts)."""

        def call():
            client = self.client_for(username)
            with self.captureOnCommitCallbacks(execute=True):
                return getattr(client, method)(url, data or {}, format='json')

        return await sync_to_async(call)()

    async def close_all(self):
        for socket in self.sockets:
            await socket.disconnect()
        self.sockets = []
        await asyncio.gather(*presence._background_tasks)

    async def expect(self, socket, event_type, timeout=2):
        event = await socket.receive_json_from(timeout=timeout)
        self.assertEqual(event['type'], event_type, event)
        return event

    # --- tests -----------------------------------------------------------

    async def test_missing_or_invalid_token_is_closed_with_4401(self):
        for token in (None, 'not-a-jwt'):
            socket = await self.open_socket(token)
            self.assertEqual(await socket.receive_output(timeout=2), {'type': 'websocket.close', 'code': 4401})

    async def test_ping_pong_and_bad_input(self):
        socket = await self.connect(self.alice)

        await socket.send_json_to({'type': 'ping'})
        self.assertEqual(await socket.receive_json_from(), {'type': 'pong'})
        await socket.send_to(text_data='not json')
        await self.expect(socket, 'error')
        await socket.send_json_to({'type': 'dance'})
        await self.expect(socket, 'error')
        await self.close_all()

    @override_settings(PRESENCE_HEARTBEAT_WRITE_SECONDS=0)
    async def test_heartbeat_moves_last_seen_forward(self):
        socket = await self.connect(self.alice)
        stale = timezone.now() - timedelta(minutes=10)
        await database_sync_to_async(User.objects.filter(pk=self.alice.pk).update)(last_seen=stale)

        await socket.send_json_to({'type': 'ping'})
        await self.expect(socket, 'pong')
        alice = await database_sync_to_async(User.objects.get)(pk=self.alice.pk)
        self.assertGreater(alice.last_seen, stale)
        self.assertTrue(alice.online_now)
        await self.close_all()

    async def test_new_message_reaches_every_member_and_nobody_else(self):
        alice = await self.connect(self.alice)
        bob = await self.connect(self.bob)
        await self.expect(alice, 'presence')  # bob came online
        carol = await self.connect(self.carol)

        response = await self.rest('alice', 'post', f'/api/chats/{self.chat.id}/messages/', {'text': 'Hi Bob'})
        self.assertEqual(response.status_code, 201)

        for socket in (alice, bob):
            event = await self.expect(socket, 'message.created')
            self.assertEqual(event['chat_id'], self.chat.id)
            self.assertEqual(event['message']['text'], 'Hi Bob')
            self.assertEqual(event['message']['id'], response.data['id'])
        self.assertTrue(await carol.receive_nothing(timeout=0.2))
        await self.close_all()

    async def test_typing_goes_to_the_peer_and_is_throttled(self):
        alice = await self.connect(self.alice)
        bob = await self.connect(self.bob)
        await self.expect(alice, 'presence')

        await alice.send_json_to({'type': 'typing', 'chat_id': self.chat.id, 'action': 'typing'})
        event = await self.expect(bob, 'typing')
        self.assertEqual(event, {'type': 'typing', 'chat_id': self.chat.id, 'user_id': self.alice.pk, 'action': 'typing'})
        self.assertTrue(await alice.receive_nothing(timeout=0.1))

        await alice.send_json_to({'type': 'typing', 'chat_id': self.chat.id, 'action': 'typing'})
        self.assertTrue(await bob.receive_nothing(timeout=0.2))  # within 1 s: dropped
        await alice.send_json_to({'type': 'typing', 'chat_id': self.chat.id, 'action': 'stop'})
        self.assertEqual((await self.expect(bob, 'typing'))['action'], 'stop')

        foreign = await database_sync_to_async(create_chat)(self.bob, self.carol)
        await alice.send_json_to({'type': 'typing', 'chat_id': foreign.id, 'action': 'typing'})
        await self.expect(alice, 'error')
        await self.close_all()

    async def test_presence_with_several_tabs(self):
        alice = await self.connect(self.alice)
        bob_tab_1 = await self.connect(self.bob)
        online = await self.expect(alice, 'presence')
        self.assertEqual((online['user_id'], online['is_online']), (self.bob.pk, True))

        bob_tab_2 = await self.connect(self.bob)
        self.assertTrue(await alice.receive_nothing(timeout=0.2))  # still the same "online"

        await bob_tab_1.disconnect()
        self.sockets.remove(bob_tab_1)
        await asyncio.gather(*presence._background_tasks)
        self.assertTrue(await alice.receive_nothing(timeout=0.2))  # one tab is still open

        await bob_tab_2.disconnect()
        self.sockets.remove(bob_tab_2)
        offline = await self.expect(alice, 'presence')
        self.assertEqual((offline['user_id'], offline['is_online']), (self.bob.pk, False))
        self.assertIsNotNone(offline['last_seen'])

        bob = await database_sync_to_async(User.objects.get)(pk=self.bob.pk)
        self.assertFalse(bob.is_online)
        await self.close_all()

    async def test_read_and_delete_events(self):
        alice = await self.connect(self.alice)
        bob = await self.connect(self.bob)
        await self.expect(alice, 'presence')

        sent = await self.rest('bob', 'post', f'/api/chats/{self.chat.id}/messages/', {'text': 'ping'})
        for socket in (alice, bob):
            await self.expect(socket, 'message.created')

        await self.rest('alice', 'post', f'/api/chats/{self.chat.id}/read/')
        for socket in (alice, bob):
            event = await self.expect(socket, 'messages.read')
            self.assertEqual((event['reader_id'], event['last_read_id']), (self.alice.pk, sent.data['id']))

        await self.rest('bob', 'delete', f"/api/messages/{sent.data['id']}/")
        for socket in (alice, bob):
            event = await self.expect(socket, 'message.deleted')
            self.assertEqual(event, {'type': 'message.deleted', 'chat_id': self.chat.id, 'message_id': sent.data['id']})
        await self.close_all()

    async def test_profile_changes_are_broadcast(self):
        alice = await self.connect(self.alice)
        bob = await self.connect(self.bob)
        await self.expect(alice, 'presence')

        await self.rest('bob', 'patch', '/api/users/me/', {'first_name': 'Robert'})
        for socket in (alice, bob):
            event = await self.expect(socket, 'user.updated')
            self.assertEqual(event['user']['display_name'], 'Robert')
        await self.close_all()

    async def open_with_ticket(self, ticket):
        socket = WebsocketCommunicator(application, f'/ws/?ticket={ticket}', headers=[ORIGIN])
        connected, _ = await socket.connect()
        self.assertTrue(connected)
        return socket

    async def test_ticket_opens_the_socket_without_cookies(self):
        response = await self.rest('alice', 'post', '/api/auth/ws-ticket/')
        socket = await self.open_with_ticket(response.data['ticket'])
        self.assertEqual(await self.expect(socket, 'ready'), {'type': 'ready', 'user_id': self.alice.pk})
        self.sockets.append(socket)
        await self.close_all()

    async def test_bad_or_old_tickets_are_refused(self):
        from django.core import signing

        from users.websocket import TICKET_SALT, make_ticket

        with mock.patch('django.core.signing.time.time', return_value=0):
            expired = await sync_to_async(make_ticket)(self.alice)
        for ticket in ('forged', expired):
            socket = await self.open_with_ticket(ticket)
            self.assertEqual(await socket.receive_output(timeout=2), {'type': 'websocket.close', 'code': 4401})

    def test_ticket_needs_a_session(self):
        from config.testing import make_client

        self.assertEqual(make_client().post('/api/auth/ws-ticket/').status_code, 401)

    async def test_deleted_account_removes_the_chat_and_closes_its_sockets(self):
        alice = await self.connect(self.alice)
        bob = await self.connect(self.bob)
        await self.expect(alice, 'presence')

        await self.rest('bob', 'delete', '/api/users/me/')
        self.assertEqual(await self.expect(alice, 'chat.deleted'), {'type': 'chat.deleted', 'chat_id': self.chat.id})
        await self.expect(bob, 'account.deleted')
        self.assertEqual(await bob.receive_output(timeout=2), {'type': 'websocket.close', 'code': 4401})
        self.sockets.remove(bob)
        await asyncio.gather(*presence._background_tasks)
        self.assertTrue(await alice.receive_nothing(timeout=0.2))
        await self.close_all()

    async def test_socket_closes_when_the_access_token_expires(self):
        token = AccessToken.for_user(self.alice)
        token.set_exp(lifetime=timedelta(seconds=1))
        socket = await self.open_socket(str(token))
        await self.expect(socket, 'ready')

        self.assertEqual(await socket.receive_output(timeout=3), {'type': 'websocket.close', 'code': 4401})
        await asyncio.gather(*presence._background_tasks)

    def test_online_requires_a_recent_heartbeat(self):
        self.alice.is_online = True
        self.alice.last_seen = timezone.now()
        self.assertTrue(self.alice.online_now)

        self.alice.last_seen = timezone.now() - timedelta(minutes=5)
        self.assertFalse(self.alice.online_now)

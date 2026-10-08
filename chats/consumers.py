import asyncio
import json
import time

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings

from . import presence
from .models import ChatMember
from .realtime import user_group

# Custom close code: the session is missing or expired. The app refreshes the
# access token and reconnects.
CLOSE_UNAUTHORIZED = 4401
TYPING_ACTIONS = {'typing', 'recording', 'stop'}
TYPING_MIN_INTERVAL = 1.0  # seconds between forwarded typing events per chat


@database_sync_to_async
def _peer_in_chat(chat_id, user_id):
    """The other member of `chat_id`, or None when `user_id` is not a member."""
    members = list(ChatMember.objects.filter(chat_id=chat_id).values_list('user_id', flat=True))
    if user_id not in members:
        return None
    return next((member for member in members if member != user_id), None)


class ChatConsumer(AsyncWebsocketConsumer):
    """
    One connection per browser tab. Receives every event for the user
    (all chats); sends typing indicators and heartbeats.
    """

    async def connect(self):
        self.user = self.scope.get('user')
        self.group_name = None
        self.expiry_task = None
        # Accept first, so the browser can see *why* we close (code 4401).
        await self.accept()
        if self.user is None or not self.user.is_authenticated:
            await self.close(code=CLOSE_UNAUTHORIZED)
            return

        self.peers = {}  # chat_id -> other member id
        self.last_typing = {}
        self.last_seen_written = time.monotonic()
        self.group_name = user_group(self.user.pk)
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await presence.connection_opened(self.user.pk)
        self.expiry_task = asyncio.create_task(self._close_when_token_expires())
        await self.send_json({'type': 'ready', 'user_id': self.user.pk})

    async def disconnect(self, code):
        if self.expiry_task:
            self.expiry_task.cancel()
        if self.group_name:
            await self.channel_layer.group_discard(self.group_name, self.channel_name)
            await presence.connection_closed(self.user.pk)

    async def receive(self, text_data=None, bytes_data=None):
        if self.group_name is None:  # not authenticated; the socket is closing
            return
        try:
            event = json.loads(text_data or '')
        except ValueError:
            await self.send_error('Invalid JSON.')
            return
        if not isinstance(event, dict):
            await self.send_error('Events must be JSON objects.')
            return

        kind = event.get('type')
        if kind == 'ping':
            await self._heartbeat()
            await self.send_json({'type': 'pong'})
        elif kind == 'typing':
            await self._typing(event)
        else:
            await self.send_error(f'Unknown event type: {kind!r}.')

    # --- events from the channel layer -------------------------------------

    async def deliver(self, event):
        await self.send_json(event['payload'])
        if event['payload'].get('type') == 'account.deleted':
            await self.close(code=CLOSE_UNAUTHORIZED)

    # --- helpers -------------------------------------------------------------

    async def send_json(self, payload):
        await self.send(text_data=json.dumps(payload))

    async def send_error(self, detail):
        await self.send_json({'type': 'error', 'detail': detail})

    async def _heartbeat(self):
        now = time.monotonic()
        write = now - self.last_seen_written >= settings.PRESENCE_HEARTBEAT_WRITE_SECONDS
        if write:
            self.last_seen_written = now
        await presence.heartbeat(self.user.pk, write_last_seen=write)

    async def _typing(self, event):
        chat_id, action = event.get('chat_id'), event.get('action', 'typing')
        if not isinstance(chat_id, int) or isinstance(chat_id, bool) or action not in TYPING_ACTIONS:
            await self.send_error('Invalid typing event.')
            return

        peer_id = self.peers.get(chat_id)
        if peer_id is None:
            peer_id = await _peer_in_chat(chat_id, self.user.pk)
            if peer_id is None:
                await self.send_error('Chat not found.')
                return
            self.peers[chat_id] = peer_id

        now = time.monotonic()
        if action != 'stop':
            if now - self.last_typing.get((chat_id, action), 0) < TYPING_MIN_INTERVAL:
                return
            self.last_typing[(chat_id, action)] = now
        await self.channel_layer.group_send(
            user_group(peer_id),
            {
                'type': 'deliver',
                'payload': {'type': 'typing', 'chat_id': chat_id, 'user_id': self.user.pk, 'action': action},
            },
        )

    async def _close_when_token_expires(self):
        expires_at = self.scope.get('token_expires_at')
        if not expires_at:
            return
        await asyncio.sleep(max(expires_at - time.time(), 0))
        await self.close(code=CLOSE_UNAUTHORIZED)

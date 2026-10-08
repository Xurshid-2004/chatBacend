"""
Fan-out of real-time events to users' WebSocket connections.

Every connection of a user (all tabs and devices) joins the group `user.<id>`;
events are delivered with the consumer's `deliver` handler, already in the
JSON shape the browser receives.
"""

import json

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from rest_framework.fields import DateTimeField
from rest_framework.renderers import JSONRenderer

from .models import ChatMember


def user_group(user_id):
    return f'user.{user_id}'


def to_json(data):
    """Plain JSON types (as the REST API renders them), safe for any channel layer."""
    return json.loads(JSONRenderer().render(data))


def format_datetime(value):
    return DateTimeField().to_representation(value) if value else None


def chat_member_ids(chat_id):
    return list(ChatMember.objects.filter(chat_id=chat_id).values_list('user_id', flat=True))


def peer_ids_of(user_id):
    """Everyone who shares a chat with `user_id`."""
    return list(
        ChatMember.objects.filter(chat__members__user_id=user_id)
        .exclude(user_id=user_id)
        .values_list('user_id', flat=True)
        .distinct()
    )


async def send_to_users_async(user_ids, payload):
    layer = get_channel_layer()
    message = {'type': 'deliver', 'payload': payload}
    for user_id in set(user_ids):
        await layer.group_send(user_group(user_id), message)


def send_to_users(user_ids, payload):
    if user_ids:
        async_to_sync(send_to_users_async)(user_ids, payload)


def account_deleted(*, user_id, chats):
    """`chats` maps each removed chat id to its other members (call after commit)."""
    for chat_id, member_ids in chats.items():
        send_to_users(member_ids, {'type': 'chat.deleted', 'chat_id': chat_id})
    # The account's other tabs and devices leave; their sockets are then closed.
    send_to_users([user_id], {'type': 'account.deleted'})

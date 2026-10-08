"""Real-time notifications for message changes (call after the transaction commits)."""

from chats.realtime import chat_member_ids, send_to_users, to_json

from .serializers import MessageSerializer


def message_created(message):
    payload = {
        'type': 'message.created',
        'chat_id': message.chat_id,
        'message': to_json(MessageSerializer(message).data),
    }
    send_to_users(chat_member_ids(message.chat_id), payload)


def message_deleted(*, chat_id, message_id, member_ids):
    send_to_users(member_ids, {'type': 'message.deleted', 'chat_id': chat_id, 'message_id': message_id})


def messages_read(*, chat_id, reader_id, last_read_id):
    payload = {
        'type': 'messages.read',
        'chat_id': chat_id,
        'reader_id': reader_id,
        'last_read_id': last_read_id,
    }
    send_to_users(chat_member_ids(chat_id), payload)

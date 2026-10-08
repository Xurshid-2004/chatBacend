from django.core.files.storage import default_storage
from django.db import IntegrityError, transaction
from django.db.models import Max

from chats.models import Chat

from .models import Message


def send_message(*, chat, sender, data):
    """
    Create a message from validated MessageCreateSerializer data.
    Returns (message, created); a repeated `client_id` returns the existing message.
    """
    client_id = data.get('client_id')
    if client_id:
        existing = Message.objects.filter(sender=sender, client_id=client_id).first()
        if existing is not None:
            return existing, False

    message = Message(
        chat=chat,
        sender=sender,
        type=data['type'],
        text=data['text'],
        reply_to=data.get('reply_to'),
        client_id=client_id,
    )
    saved_files = []
    try:
        with transaction.atomic():
            processed = data.get('processed')
            if processed is not None:
                stored = getattr(message, Message.MEDIA_FIELDS[message.type])
                stored.save(processed.name, processed.content, save=False)
                saved_files.append(stored.name)
                message.file_name = processed.name
                message.file_size = stored.size
                message.mime_type = processed.mime_type
                message.width = processed.width
                message.height = processed.height
                message.preview = processed.preview
                message.duration = data.get('duration')
                message.waveform = data.get('waveform')

            thumbnail = data.get('processed_thumbnail')
            if thumbnail is not None:
                message.thumbnail.save(thumbnail.name, thumbnail.content, save=False)
                saved_files.append(message.thumbnail.name)
                message.preview = thumbnail.preview
                if not (message.width and message.height):
                    message.width, message.height = thumbnail.width, thumbnail.height

            message.save()
            Chat.objects.filter(pk=chat.pk).update(updated_at=message.created_at)
    except BaseException as exc:
        for name in saved_files:
            default_storage.delete(name)
        if isinstance(exc, IntegrityError) and client_id:
            # The same message arrived twice at the same moment.
            existing = Message.objects.filter(sender=sender, client_id=client_id).first()
            if existing is not None:
                return existing, False
        raise
    return message, True


def mark_read(*, chat, reader, up_to=None):
    """Mark incoming messages (up to message id `up_to`) as read. Returns (count, last_read_id)."""
    unread = Message.objects.filter(chat=chat, is_read=False).exclude(sender=reader)
    if up_to is not None:
        unread = unread.filter(id__lte=up_to)
    last_id = unread.aggregate(last=Max('id'))['last']
    if last_id is None:
        return 0, None
    count = unread.filter(id__lte=last_id).update(is_read=True)
    return count, last_id


def delete_message(message):
    """Delete a message for everyone; its files are removed after the commit."""
    message.delete()

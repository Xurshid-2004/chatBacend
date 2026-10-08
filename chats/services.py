from django.db import IntegrityError, transaction
from django.db.models import Count, IntegerField, OuterRef, Subquery, Value
from django.db.models.functions import Coalesce

from messaging.models import Message

from .models import Chat, ChatMember


def get_or_create_private_chat(user, other):
    """Return (chat, created). Safe when both users start the chat at the same moment."""
    key = Chat.private_key_for(user.pk, other.pk)
    chat = Chat.objects.filter(private_key=key).first()
    if chat is not None:
        return chat, False
    try:
        with transaction.atomic():
            chat = Chat.objects.create(private_key=key)
            ChatMember.objects.bulk_create([ChatMember(chat=chat, user=user), ChatMember(chat=chat, user=other)])
    except IntegrityError:
        return Chat.objects.get(private_key=key), False
    return chat, True


def chats_for(user):
    """Chats of `user`, annotated with `last_message_id` and `unread_count` in one query."""
    last_message = Message.objects.filter(chat=OuterRef('pk')).order_by('-id').values('id')[:1]
    unread = (
        Message.objects.filter(chat=OuterRef('pk'), is_read=False)
        .exclude(sender=user)
        .order_by()
        .values('chat')
        .annotate(total=Count('id'))
        .values('total')
    )
    return (
        Chat.objects.filter(members__user=user)
        .annotate(
            last_message_id=Subquery(last_message),
            unread_count=Coalesce(Subquery(unread, output_field=IntegerField()), Value(0)),
        )
        .prefetch_related('participants')
    )


def last_messages_for(chats):
    """Map last_message_id -> Message for a page of annotated chats (one query)."""
    ids = [chat.last_message_id for chat in chats if chat.last_message_id]
    messages = Message.objects.filter(id__in=ids).select_related('reply_to')
    return {message.id: message for message in messages}

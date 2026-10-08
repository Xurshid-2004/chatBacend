from collections import defaultdict

from django.db import transaction

from chats.models import Chat, ChatMember
from chats.realtime import account_deleted

from .tokens import revoke_all_refresh_tokens


def _chat_members_of(user_id):
    """{chat_id: [member ids]} for every chat `user_id` is in."""
    members = defaultdict(list)
    rows = ChatMember.objects.filter(chat__members__user_id=user_id).values_list('chat_id', 'user_id')
    for chat_id, member_id in rows:
        members[chat_id].append(member_id)
    return members


def delete_account(user):
    """
    Delete `user` for good: the profile, avatar, and every chat they were in
    (with all messages and files on both sides — a one-to-one chat with nobody
    on the other end is useless). Partners and the user's own devices are told
    in real time once the deletion is committed.
    """
    user_id = user.pk
    with transaction.atomic():
        chats = {
            chat_id: [member for member in members if member != user_id]
            for chat_id, members in _chat_members_of(user_id).items()
        }
        revoke_all_refresh_tokens(user)
        Chat.objects.filter(pk__in=chats).delete()  # messages and their files go with them
        user.delete()
    transaction.on_commit(lambda: account_deleted(user_id=user_id, chats=chats))

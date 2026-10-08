from django.conf import settings
from django.db import models
from django.utils import timezone


class Chat(models.Model):
    """A private conversation between two users."""

    participants = models.ManyToManyField(settings.AUTH_USER_MODEL, through='ChatMember', related_name='chats')
    # "<smaller user id>:<larger user id>": the database guarantees one chat per pair.
    private_key = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(default=timezone.now)
    # Time of the latest message; the chat list is ordered by it.
    updated_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ['-updated_at', '-id']

    def __str__(self):
        return f'Chat {self.pk} ({self.private_key})'

    @staticmethod
    def private_key_for(user_id, other_user_id):
        low, high = sorted((int(user_id), int(other_user_id)))
        return f'{low}:{high}'

    def peer_of(self, user):
        """The other participant (uses prefetched participants when available)."""
        return next((member for member in self.participants.all() if member.pk != user.pk), None)


class ChatMember(models.Model):
    chat = models.ForeignKey(Chat, on_delete=models.CASCADE, related_name='members')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='chat_memberships')
    joined_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['chat', 'user'], name='chats_member_unique')]

    def __str__(self):
        return f'{self.user} in chat {self.chat_id}'

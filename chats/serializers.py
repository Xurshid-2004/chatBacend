from rest_framework import serializers

from messaging.serializers import MessageSerializer
from users.models import User
from users.serializers import UserSerializer

from .models import Chat


class ChatSerializer(serializers.ModelSerializer):
    """
    A chat as seen by the requesting user. Expects chats from
    `services.chats_for()` and `last_messages` (id -> Message) in the context.
    """

    peer = serializers.SerializerMethodField()
    last_message = serializers.SerializerMethodField()
    unread_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = Chat
        fields = ['id', 'peer', 'last_message', 'unread_count', 'created_at', 'updated_at']
        read_only_fields = fields

    def get_peer(self, chat):
        peer = chat.peer_of(self.context['request'].user)
        return UserSerializer(peer).data if peer else None

    def get_last_message(self, chat):
        message = self.context.get('last_messages', {}).get(getattr(chat, 'last_message_id', None))
        return MessageSerializer(message).data if message else None


class ChatCreateSerializer(serializers.Serializer):
    user_id = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.filter(is_active=True),
        error_messages={'does_not_exist': 'User not found.', 'incorrect_type': 'User not found.'},
    )

    def validate_user_id(self, user):
        if user.pk == self.context['request'].user.pk:
            raise serializers.ValidationError("You can't start a chat with yourself.")
        return user

from django.dispatch import receiver

from users.serializers import UserSerializer
from users.signals import profile_updated

from .realtime import peer_ids_of, send_to_users, to_json


@receiver(profile_updated)
def broadcast_profile_update(sender, user, **kwargs):
    """Chat partners (and the user's other tabs) see new names/avatars immediately."""
    payload = {'type': 'user.updated', 'user': to_json(UserSerializer(user).data)}
    send_to_users([user.pk, *peer_ids_of(user.pk)], payload)

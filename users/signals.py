from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import Signal, receiver

from .models import User

# Sent (after commit) when a user changes their public profile: name, avatar...
profile_updated = Signal()


@receiver(post_delete, sender=User)
def delete_avatar_file(sender, instance, **kwargs):
    if instance.avatar:
        name = instance.avatar.name
        transaction.on_commit(lambda: default_storage.delete(name))

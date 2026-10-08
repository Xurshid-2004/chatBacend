from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import Message


@receiver(post_delete, sender=Message)
def delete_message_files(sender, instance, **kwargs):
    """Remove stored files once the deletion is committed (also on chat/user cascades)."""
    names = instance.stored_file_names()
    if names:
        transaction.on_commit(lambda: [default_storage.delete(name) for name in names])

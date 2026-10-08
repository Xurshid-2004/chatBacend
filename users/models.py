import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.contrib.auth.models import UserManager as DjangoUserManager
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone

from .presets import AVATAR_PRESETS
from .validators import UsernameValidator


def avatar_upload_to(instance, filename):
    extension = filename.rsplit('.', 1)[-1].lower() if '.' in filename else 'webp'
    return f'avatars/{uuid.uuid4().hex}.{extension}'


class UserManager(DjangoUserManager):
    def get_by_natural_key(self, username):
        # Usernames are unique regardless of case, so sign-in ignores case too.
        return self.alias(username_lower=Lower('username')).get(username_lower=username.lower())


class User(AbstractUser):
    username_validator = UsernameValidator()

    username = models.CharField(
        'username',
        max_length=32,
        unique=True,
        help_text='3-32 characters: letters, digits and underscores, starting with a letter.',
        validators=[username_validator],
        error_messages={'unique': 'This username is already taken.'},
    )
    avatar = models.ImageField(upload_to=avatar_upload_to, blank=True)
    # A ready-made avatar picked on the start screen (shown when there is no photo).
    avatar_preset = models.CharField(
        max_length=20, blank=True, choices=[(key, key.title()) for key in AVATAR_PRESETS]
    )
    bio = models.CharField(max_length=160, blank=True)
    is_online = models.BooleanField(default=False)
    last_seen = models.DateTimeField(null=True, blank=True)

    objects = UserManager()

    class Meta(AbstractUser.Meta):
        constraints = [
            models.UniqueConstraint(
                Lower('username'),
                name='users_user_username_ci_unique',
                violation_error_message='This username is already taken.',
            ),
        ]

    @property
    def display_name(self):
        return self.get_full_name() or self.username

    @property
    def online_now(self):
        """`is_online`, trusted only while WebSocket heartbeats keep `last_seen` fresh."""
        if not self.is_online or self.last_seen is None:
            return False
        return self.last_seen >= timezone.now() - timedelta(seconds=settings.PRESENCE_ONLINE_WINDOW)

    @property
    def created_at(self):
        return self.date_joined

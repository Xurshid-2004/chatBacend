from django.contrib.auth import password_validation
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.files.storage import default_storage
from django.db import transaction
from rest_framework import serializers

from .images import process_avatar
from .models import User
from .presets import AVATAR_PRESETS
from .validators import UsernameValidator

USERNAME_TAKEN = 'This username is already taken.'


def _username_taken(username, exclude_pk=None):
    queryset = User.objects.filter(username__iexact=username)
    if exclude_pk is not None:
        queryset = queryset.exclude(pk=exclude_pk)
    return queryset.exists()


class UserSerializer(serializers.ModelSerializer):
    """Public profile: safe to show to any signed-in user."""

    display_name = serializers.CharField(read_only=True)
    avatar = serializers.SerializerMethodField()
    is_online = serializers.BooleanField(source='online_now', read_only=True)
    created_at = serializers.DateTimeField(source='date_joined', read_only=True)

    class Meta:
        model = User
        fields = [
            'id',
            'username',
            'first_name',
            'last_name',
            'display_name',
            'avatar',
            'avatar_preset',
            'bio',
            'is_online',
            'last_seen',
            'created_at',
        ]
        read_only_fields = fields

    def get_avatar(self, user):
        return user.avatar.url if user.avatar else None


class RegisterSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=32, validators=[UsernameValidator()])
    password = serializers.CharField(max_length=128, trim_whitespace=False, write_only=True)
    first_name = serializers.CharField(max_length=64, required=False, allow_blank=True)
    last_name = serializers.CharField(max_length=64, required=False, allow_blank=True)

    def validate_username(self, value):
        if _username_taken(value):
            raise serializers.ValidationError(USERNAME_TAKEN)
        return value

    def validate(self, attrs):
        candidate = User(
            username=attrs['username'],
            first_name=attrs.get('first_name', ''),
            last_name=attrs.get('last_name', ''),
        )
        try:
            password_validation.validate_password(attrs['password'], user=candidate)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({'password': list(exc.messages)}) from exc
        return attrs

    def create(self, validated_data):
        return User.objects.create_user(**validated_data)


class StartSerializer(serializers.Serializer):
    """The start screen: a name and one of the ready-made avatars."""

    name = serializers.CharField(max_length=40)
    avatar_preset = serializers.ChoiceField(choices=AVATAR_PRESETS, required=False)

    def validate_name(self, value):
        value = ' '.join(value.split())
        if not value:
            raise serializers.ValidationError('Enter your name.')
        return value


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    password = serializers.CharField(max_length=128, trim_whitespace=False, write_only=True)


class ProfileSerializer(serializers.ModelSerializer):
    """Fields a user can change on their own profile."""

    username = serializers.CharField(max_length=32, required=False, validators=[UsernameValidator()])
    first_name = serializers.CharField(max_length=64, required=False, allow_blank=True)
    last_name = serializers.CharField(max_length=64, required=False, allow_blank=True)
    avatar = serializers.FileField(required=False, write_only=True)
    remove_avatar = serializers.BooleanField(required=False, write_only=True)
    avatar_preset = serializers.ChoiceField(choices=AVATAR_PRESETS, required=False, allow_blank=True)

    class Meta:
        model = User
        fields = ['username', 'first_name', 'last_name', 'bio', 'avatar', 'remove_avatar', 'avatar_preset']

    def validate_username(self, value):
        if _username_taken(value, exclude_pk=self.instance.pk):
            raise serializers.ValidationError(USERNAME_TAKEN)
        return value

    def validate_avatar(self, uploaded):
        try:
            return process_avatar(uploaded)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages) from exc

    def update(self, user, validated_data):
        new_avatar = validated_data.pop('avatar', None)
        remove_avatar = validated_data.pop('remove_avatar', False)
        old_avatar = user.avatar.name if user.avatar else None

        for field, value in validated_data.items():
            setattr(user, field, value)

        saved_avatar = None
        if new_avatar is not None:
            user.avatar.save(new_avatar.name, new_avatar, save=False)
            saved_avatar = user.avatar.name
        elif remove_avatar:
            user.avatar = ''

        try:
            user.save()
        except Exception:
            if saved_avatar:
                default_storage.delete(saved_avatar)
            raise

        if old_avatar and (new_avatar is not None or remove_avatar):
            transaction.on_commit(lambda: default_storage.delete(old_avatar))
        return user


class PasswordChangeSerializer(serializers.Serializer):
    current_password = serializers.CharField(max_length=128, trim_whitespace=False, write_only=True)
    new_password = serializers.CharField(max_length=128, trim_whitespace=False, write_only=True)

    def validate_current_password(self, value):
        if not self.context['request'].user.check_password(value):
            raise serializers.ValidationError('Current password is incorrect.')
        return value

    def validate(self, attrs):
        user = self.context['request'].user
        try:
            password_validation.validate_password(attrs['new_password'], user=user)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({'new_password': list(exc.messages)}) from exc
        return attrs

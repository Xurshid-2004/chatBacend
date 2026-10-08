import math

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.urls import reverse
from rest_framework import serializers

from .media import (
    THUMBNAIL_MAX_SIDE,
    ProcessedMedia,
    clean_filename,
    detect_media,
    process_image,
    with_extension,
)
from .models import Message

Type = Message.Type
MAX_WAVEFORM_POINTS = 128


def attachment_data(message):
    """The file part of a message in one shape for every media type (None for text)."""
    if message.type == Type.TEXT or not message.attachment:
        return None
    return {
        'url': reverse('message-attachment', args=[message.pk]),
        'name': message.file_name,
        'size': message.file_size,
        'mime_type': message.mime_type,
        'width': message.width,
        'height': message.height,
        'duration': message.duration,
        'waveform': message.waveform,
        'thumbnail_url': reverse('message-thumbnail', args=[message.pk]) if message.thumbnail else None,
        'preview': message.preview or None,
    }


class MessageSerializer(serializers.ModelSerializer):
    chat = serializers.IntegerField(source='chat_id', read_only=True)
    sender = serializers.IntegerField(source='sender_id', read_only=True)
    attachment = serializers.SerializerMethodField()
    reply_to = serializers.SerializerMethodField()

    class Meta:
        model = Message
        fields = ['id', 'chat', 'sender', 'type', 'text', 'attachment', 'reply_to', 'is_read', 'client_id', 'created_at']
        read_only_fields = fields

    def get_attachment(self, message):
        return attachment_data(message)

    def get_reply_to(self, message):
        original = message.reply_to
        if original is None:
            return None
        return {
            'id': original.pk,
            'sender': original.sender_id,
            'type': original.type,
            'text': original.text[:200],
            'attachment': attachment_data(original),
        }


class MessageCreateSerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=Type.choices, default=Type.TEXT)
    text = serializers.CharField(
        required=False, allow_blank=True, trim_whitespace=False, max_length=settings.CHAT_MAX_TEXT_LENGTH
    )
    file = serializers.FileField(required=False, allow_empty_file=False)
    thumbnail = serializers.FileField(required=False, allow_empty_file=False)
    duration = serializers.FloatField(required=False, min_value=0, max_value=6 * 3600)
    width = serializers.IntegerField(required=False, min_value=1, max_value=20000)
    height = serializers.IntegerField(required=False, min_value=1, max_value=20000)
    waveform = serializers.JSONField(required=False)
    reply_to = serializers.IntegerField(required=False, allow_null=True)
    client_id = serializers.UUIDField(required=False, allow_null=True)

    def validate_duration(self, value):
        if not math.isfinite(value):
            raise serializers.ValidationError('Invalid duration.')
        return round(value, 2)

    def validate_waveform(self, value):
        if not isinstance(value, list) or len(value) > MAX_WAVEFORM_POINTS:
            raise serializers.ValidationError(f'Waveform must be a list of at most {MAX_WAVEFORM_POINTS} numbers.')
        points = []
        for point in value:
            if isinstance(point, bool) or not isinstance(point, (int, float)) or not 0 <= point <= 1:
                raise serializers.ValidationError('Waveform values must be numbers between 0 and 1.')
            points.append(round(float(point), 3))
        return points

    def validate_reply_to(self, value):
        if value is None:
            return None
        original = Message.objects.filter(pk=value, chat=self.context['chat']).first()
        if original is None:
            raise serializers.ValidationError('The message you are replying to was not found in this chat.')
        return original

    def validate(self, attrs):
        kind = attrs['type']
        attrs['text'] = attrs.get('text', '').strip()
        upload = attrs.get('file')

        if kind == Type.TEXT:
            if upload is not None:
                raise serializers.ValidationError({'file': 'Text messages cannot contain a file.'})
            if not attrs['text']:
                raise serializers.ValidationError({'text': 'Message cannot be empty.'})
        else:
            if upload is None:
                raise serializers.ValidationError({'file': 'Attach a file to send.'})
            limit = settings.CHAT_UPLOAD_LIMITS[kind]
            if upload.size > limit:
                raise serializers.ValidationError(
                    {'file': f'File is too large. The limit is {limit // (1024 * 1024)} MB.'}
                )
            attrs['processed'] = self._process_upload(kind, upload, attrs)

        if attrs.get('thumbnail') is not None:
            if kind != Type.VIDEO:
                raise serializers.ValidationError({'thumbnail': 'Only videos can have a thumbnail.'})
            attrs['processed_thumbnail'] = self._process_thumbnail(attrs['thumbnail'])
        if attrs.get('waveform') is not None and kind != Type.AUDIO:
            raise serializers.ValidationError({'waveform': 'Only voice messages can have a waveform.'})
        return attrs

    def _process_upload(self, kind, upload, attrs):
        original_name = clean_filename(upload.name)
        if kind == Type.FILE:
            return ProcessedMedia(content=upload, name=original_name, mime_type=self._client_mime_type(upload))
        try:
            if kind == Type.IMAGE:
                processed = process_image(upload)
                processed.name = with_extension(original_name, '.' + processed.name.rsplit('.', 1)[-1])
                return processed
            mime_type, extension = detect_media(upload, 'video' if kind == Type.VIDEO else 'audio')
        except DjangoValidationError as exc:
            raise serializers.ValidationError({'file': exc.messages}) from exc
        is_video = kind == Type.VIDEO
        return ProcessedMedia(
            content=upload,
            name=with_extension(original_name, extension),
            mime_type=mime_type,
            width=attrs.get('width') if is_video else None,
            height=attrs.get('height') if is_video else None,
        )

    @staticmethod
    def _process_thumbnail(upload):
        if upload.size > settings.CHAT_UPLOAD_LIMITS[Type.IMAGE]:
            raise serializers.ValidationError({'thumbnail': 'Thumbnail is too large.'})
        try:
            return process_image(upload, max_side=THUMBNAIL_MAX_SIDE)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({'thumbnail': exc.messages}) from exc

    @staticmethod
    def _client_mime_type(upload):
        # Only used to pick an icon; files are always served as downloads.
        value = (upload.content_type or '').split(';')[0].strip().lower()
        valid = len(value) <= 100 and value.count('/') == 1 and all(
            part and all(ch.isalnum() or ch in '.+-_' for ch in part) for part in value.split('/')
        )
        return value if valid else 'application/octet-stream'

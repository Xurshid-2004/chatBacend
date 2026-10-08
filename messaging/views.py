from django.conf import settings
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from chats.models import Chat
from chats.realtime import chat_member_ids
from config.media import serve_file
from users.authentication import CookieJWTAuthentication, MediaCookieAuthentication

from . import events
from .models import Message
from .serializers import MessageCreateSerializer, MessageSerializer
from .services import delete_message, mark_read, send_message

DEFAULT_PAGE_SIZE = 30
MAX_PAGE_SIZE = 100


def member_chat_or_404(user, chat_id):
    return get_object_or_404(Chat.objects.filter(members__user=user), pk=chat_id)


def visible_message_or_404(user, pk):
    return get_object_or_404(Message.objects.filter(chat__members__user=user), pk=pk)


def int_param(request, name, default=None, minimum=None, maximum=None):
    raw = request.query_params.get(name)
    if raw in (None, ''):
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValidationError({name: ['Must be an integer.']}) from exc
    if minimum is not None:
        value = max(value, minimum)
    if maximum is not None:
        value = min(value, maximum)
    return value


class MessageListCreateView(APIView):
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    throttle_scope = 'send_message'

    def get_throttles(self):
        return [ScopedRateThrottle()] if self.request.method == 'POST' else []

    def get(self, request, chat_id):
        """
        History, oldest first. `?before=<id>` pages back in time; `?after=<id>`
        fetches newer messages (catching up after a reconnect).
        """
        chat = member_chat_or_404(request.user, chat_id)
        limit = int_param(request, 'limit', DEFAULT_PAGE_SIZE, 1, MAX_PAGE_SIZE)
        before = int_param(request, 'before')
        after = int_param(request, 'after')

        messages = chat.messages.select_related('reply_to')
        if after is not None:
            items = list(messages.filter(id__gt=after).order_by('id')[: limit + 1])
            has_more = len(items) > limit
            items = items[:limit]
        else:
            if before is not None:
                messages = messages.filter(id__lt=before)
            items = list(messages.order_by('-id')[: limit + 1])
            has_more = len(items) > limit
            items = items[:limit][::-1]
        return Response({'results': MessageSerializer(items, many=True).data, 'has_more': has_more})

    def post(self, request, chat_id):
        # Refuse oversized uploads before the body is parsed.
        try:
            content_length = int(request.META.get('CONTENT_LENGTH') or 0)
        except ValueError:
            content_length = 0
        if content_length > settings.CHAT_MAX_REQUEST_SIZE:
            return Response(
                {'detail': 'File is too large.', 'code': 'too_large'},
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )

        chat = member_chat_or_404(request.user, chat_id)
        serializer = MessageCreateSerializer(data=request.data, context={'request': request, 'chat': chat})
        serializer.is_valid(raise_exception=True)
        message, created = send_message(chat=chat, sender=request.user, data=serializer.validated_data)
        if created:
            transaction.on_commit(lambda: events.message_created(message))
        return Response(
            MessageSerializer(message).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class ChatReadView(APIView):
    def post(self, request, chat_id):
        chat = member_chat_or_404(request.user, chat_id)
        up_to = request.data.get('up_to')
        if up_to is not None:
            try:
                up_to = int(up_to)
            except (TypeError, ValueError) as exc:
                raise ValidationError({'up_to': ['Must be a message id.']}) from exc
        count, last_read_id = mark_read(chat=chat, reader=request.user, up_to=up_to)
        if count:
            transaction.on_commit(
                lambda: events.messages_read(
                    chat_id=chat.pk, reader_id=request.user.pk, last_read_id=last_read_id
                )
            )
        return Response({'updated': count, 'last_read_id': last_read_id})


class MessageDetailView(APIView):
    def delete(self, request, pk):
        message = visible_message_or_404(request.user, pk)
        if message.sender_id != request.user.pk:
            raise PermissionDenied('You can only delete your own messages.')
        chat_id, message_id = message.chat_id, message.pk
        member_ids = chat_member_ids(chat_id)
        delete_message(message)
        transaction.on_commit(
            lambda: events.message_deleted(chat_id=chat_id, message_id=message_id, member_ids=member_ids)
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class MessageFileView(APIView):
    # The media cookie comes first, so an expired access cookie never breaks <img>/<video>.
    authentication_classes = [MediaCookieAuthentication, CookieJWTAuthentication]


class AttachmentView(MessageFileView):
    def get(self, request, pk):
        message = visible_message_or_404(request.user, pk)
        stored = message.attachment
        if not stored:
            raise Http404
        is_document = message.type == Message.Type.FILE
        return serve_file(
            request,
            stored.name,
            # Documents are always downloads, so their content is never rendered by the browser.
            content_type='application/octet-stream' if is_document else message.mime_type,
            filename=message.file_name or None,
            as_attachment=is_document or request.query_params.get('download') == '1',
        )


class ThumbnailView(MessageFileView):
    def get(self, request, pk):
        message = visible_message_or_404(request.user, pk)
        if not message.thumbnail:
            raise Http404
        return serve_file(request, message.thumbnail.name, content_type='image/webp')

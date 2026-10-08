from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.pagination import CursorPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import ChatCreateSerializer, ChatSerializer
from .services import chats_for, get_or_create_private_chat, last_messages_for


class ChatPagination(CursorPagination):
    page_size = 30
    ordering = ('-updated_at', '-id')


def _serialize(chats, request, many=False):
    chat_list = chats if many else [chats]
    context = {'request': request, 'last_messages': last_messages_for(chat_list)}
    return ChatSerializer(chats, many=many, context=context).data


class ChatListCreateView(APIView):
    def get(self, request):
        # Only chats that already have messages (an opened but empty chat stays hidden).
        queryset = chats_for(request.user).filter(last_message_id__isnull=False)
        paginator = ChatPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(_serialize(page, request, many=True))

    def post(self, request):
        serializer = ChatCreateSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        chat, created = get_or_create_private_chat(request.user, serializer.validated_data['user_id'])
        chat = chats_for(request.user).get(pk=chat.pk)
        return Response(
            _serialize(chat, request),
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class ChatDetailView(APIView):
    def get(self, request, pk):
        chat = get_object_or_404(chats_for(request.user), pk=pk)
        return Response(_serialize(chat, request))

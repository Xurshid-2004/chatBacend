from django.urls import path

from . import views

urlpatterns = [
    path('chats/<int:chat_id>/messages/', views.MessageListCreateView.as_view(), name='message-list'),
    path('chats/<int:chat_id>/read/', views.ChatReadView.as_view(), name='chat-read'),
    path('messages/<int:pk>/', views.MessageDetailView.as_view(), name='message-detail'),
    path('messages/<int:pk>/attachment/', views.AttachmentView.as_view(), name='message-attachment'),
    path('messages/<int:pk>/thumbnail/', views.ThumbnailView.as_view(), name='message-thumbnail'),
]

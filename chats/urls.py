from django.urls import path

from . import views

urlpatterns = [
    path('chats/', views.ChatListCreateView.as_view(), name='chat-list'),
    path('chats/<int:pk>/', views.ChatDetailView.as_view(), name='chat-detail'),
]

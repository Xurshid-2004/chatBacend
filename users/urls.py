from django.urls import path

from . import moderation, views

urlpatterns = [
    path('auth/guest/', views.GuestView.as_view(), name='auth-guest'),
    path('auth/register/', views.RegisterView.as_view(), name='auth-register'),
    path('auth/login/', views.LoginView.as_view(), name='auth-login'),
    path('auth/refresh/', views.RefreshView.as_view(), name='auth-refresh'),
    path('auth/logout/', views.LogoutView.as_view(), name='auth-logout'),
    path('auth/me/', views.MeView.as_view(), name='auth-me'),
    path('auth/ws-ticket/', views.WebSocketTicketView.as_view(), name='auth-ws-ticket'),
    path('users/', views.UserSearchView.as_view(), name='user-search'),
    path('users/me/', views.ProfileView.as_view(), name='user-me'),
    path('users/me/password/', views.PasswordChangeView.as_view(), name='user-password'),
    path('users/people/', views.PeopleView.as_view(), name='user-people'),
    path('users/<int:pk>/', views.UserDetailView.as_view(), name='user-detail'),
    path('moderation/status/', moderation.StatusView.as_view(), name='moderation-status'),
    path('moderation/unlock/', moderation.UnlockView.as_view(), name='moderation-unlock'),
    path('moderation/lock/', moderation.LockView.as_view(), name='moderation-lock'),
    path('moderation/users/', moderation.MemberListView.as_view(), name='moderation-users'),
    path('moderation/users/<int:pk>/', moderation.MemberDetailView.as_view(), name='moderation-user'),
]

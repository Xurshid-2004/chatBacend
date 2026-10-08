from django.urls import path

from . import views

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
]

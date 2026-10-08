from django.contrib import admin
from django.urls import include, path, re_path

from config import views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/health/', views.health, name='health'),
    path('api/', include('users.urls')),
    path('api/', include('chats.urls')),
    path('api/', include('messaging.urls')),
    re_path(r'^media/avatars/(?P<path>[A-Za-z0-9_-]+\.(?:webp|jpe?g|png))$', views.avatar, name='avatar'),
]

handler404 = 'config.views.page_not_found'
handler500 = 'config.views.server_error'

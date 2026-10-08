"""
ASGI entrypoint for the chat backend.

HTTP requests go to Django; WebSocket connections go to Channels consumers
listed in config/routing.py.
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

# Set up Django (apps, models) before importing code that touches models.
django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402
from channels.security.websocket import OriginValidator, WebsocketDenier  # noqa: E402
from django.conf import settings  # noqa: E402
from django.urls import re_path  # noqa: E402

from config.routing import websocket_urlpatterns  # noqa: E402
from users.websocket import JWTAuthMiddleware  # noqa: E402

application = ProtocolTypeRouter(
    {
        'http': django_asgi_app,
        'websocket': OriginValidator(
            JWTAuthMiddleware(
                URLRouter(
                    [
                        *websocket_urlpatterns,
                        # Unknown paths: refuse the handshake instead of raising.
                        re_path(r'', WebsocketDenier.as_asgi()),
                    ]
                )
            ),
            settings.WEBSOCKET_ALLOWED_ORIGINS,
        ),
    }
)

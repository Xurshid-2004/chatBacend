"""
WebSocket routes. Apps define their own `websocket_urlpatterns`
(e.g. chats/routing.py) and they are combined here.
"""

from chats.routing import websocket_urlpatterns as chat_routes

websocket_urlpatterns = [
    *chat_routes,
]

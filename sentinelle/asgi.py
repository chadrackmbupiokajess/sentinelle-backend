"""
Point d'entrée ASGI — VPS (uvicorn ou daphne), avec WebSockets (Django Channels).

    uvicorn sentinelle.asgi:application --host 0.0.0.0 --port 8000 --proxy-headers

Mettez SENTINELLE_WEBSOCKET=1 dans le .env pour activer la diffusion WebSocket.
Plusieurs processus ? Ajoutez Redis : SENTINELLE_REDIS_URL=redis://localhost:6379/0
"""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "sentinelle.settings")

from django.core.asgi import get_asgi_application  # noqa: E402

application_http = get_asgi_application()  # charge Django avant les consumers

try:
    from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402

    from appel.routing import websocket_urlpatterns  # noqa: E402

    application = ProtocolTypeRouter(
        {"http": application_http, "websocket": URLRouter(websocket_urlpatterns)}
    )
except ImportError:  # Channels non installé : HTTP seulement (le frontend passera en AJAX)
    application = application_http

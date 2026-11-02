from django.urls import path

from . import consumers

websocket_urlpatterns = [
    path("ws/soignant", consumers.EcranSoignant.as_asgi()),
    path("ws/borne/<str:jeton>", consumers.BornePatient.as_asgi()),
]

"""
Point d'entrée WSGI — PythonAnywhere (ou tout hébergeur WSGI).

Pas de WebSocket en WSGI : le frontend utilise l'AJAX (NEXT_PUBLIC_WEBSOCKET=off).
Dans le fichier WSGI de PythonAnywhere, mettez :

    import sys
    sys.path.insert(0, "/home/VOTRE_NOM/Sentinelle-la-colombe-django")
    from sentinelle.wsgi import application
"""

import os
import sys
from pathlib import Path

DOSSIER = str(Path(__file__).resolve().parent.parent)
if DOSSIER not in sys.path:
    sys.path.insert(0, DOSSIER)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "sentinelle.settings")

from django.core.wsgi import get_wsgi_application  # noqa: E402

application = get_wsgi_application()

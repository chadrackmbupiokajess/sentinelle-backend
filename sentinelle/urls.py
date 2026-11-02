from django.contrib import admin
from django.contrib.staticfiles.views import serve as servir_statique
from django.urls import include, path

admin.site.site_header = "Jessna — gestion de la plateforme"
admin.site.site_title = "Jessna"
admin.site.index_title = "Établissements et comptes"
admin.site.site_url = None  # pas de lien « Voir le site » : le site est sur une autre adresse (Vercel)


def fichier_statique(requete, path):
    """CSS / JS de l'admin Django, même sans DEBUG (sur PythonAnywhere, le « Static files mapping »
    les sert avant Django ; cette route sert de secours, par exemple en local)."""
    return servir_statique(requete, path, insecure=True)


urlpatterns = [
    path("api/", include("appel.api.urls")),
    # Admin Django : http://localhost:8000/admin/ (compte créé avec : python manage.py createsuperuser).
    path("admin/", admin.site.urls),
    path("static/<path:path>", fichier_statique),
]

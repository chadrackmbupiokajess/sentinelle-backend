"""/admin/ (sur l'adresse du backend) : admin Django (cas avancés). Au quotidien, utilisez la page /plateforme du site."""

from django.contrib import admin
from django.db.models import Count

from .models import Chambre, Compte, Etablissement, TypeEtablissement


@admin.register(TypeEtablissement)
class TypeEtablissementAdmin(admin.ModelAdmin):
    list_display = ("nom", "cree_le")


class ChambreEnLigne(admin.TabularInline):
    model = Chambre
    fields = ("numero", "libelle", "service", "lits", "code")
    readonly_fields = ("code",)
    extra = 0


@admin.register(Etablissement)
class EtablissementAdmin(admin.ModelAdmin):
    list_display = ("nom", "type", "slug", "actif", "nb_comptes", "nb_chambres", "cree_le")
    list_filter = ("actif", "type")
    search_fields = ("nom", "slug")
    readonly_fields = ("slug", "code_activation", "code_activation_expire", "active_le", "cree_le", "a_logo", "logo_version")
    inlines = [ChambreEnLigne]

    def get_queryset(self, requete):
        return super().get_queryset(requete).annotate(
            _comptes=Count("comptes", distinct=True), _chambres=Count("chambres", distinct=True)
        )

    @admin.display(description="comptes", ordering="_comptes")
    def nb_comptes(self, obj):
        return obj._comptes

    @admin.display(description="chambres", ordering="_chambres")
    def nb_chambres(self, obj):
        return obj._chambres

    def has_add_permission(self, requete):
        return False  # création depuis /plateforme (adresse, code d'activation, lieux de départ)


@admin.register(Compte)
class CompteAdmin(admin.ModelAdmin):
    list_display = ("nom", "identifiant", "etablissement", "role", "statut", "cree_le")
    list_filter = ("role", "statut", "etablissement")
    search_fields = ("nom", "identifiant")
    fields = ("etablissement", "identifiant", "nom", "role", "statut", "doit_changer", "is_staff", "is_superuser", "cree_le")
    readonly_fields = ("cree_le",)

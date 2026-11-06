from django.urls import path

from . import vues as v
from . import vues_plateforme as p

urlpatterns = [
    path("sante", v.sante),
    path("config", v.config),
    # Établissements : page publique et activation (code à usage unique)
    path("etablissements/<str:adresse>", v.etablissement_public),
    path("etablissements/<str:adresse>/logo", v.image_logo("logo")),
    path("etablissements/<str:adresse>/icone", v.image_logo("icone")),
    path("etablissements/<str:adresse>/icone-masquable", v.image_logo("icone-masquable")),
    path("activation/verifier", v.verifier_activation),
    path("activation", v.activer),
    # Authentification
    path("auth/inscription", v.inscription),
    path("auth/connexion", v.connexion),
    path("auth/moi", v.moi),
    path("auth/deconnexion", v.deconnexion),
    path("auth/mot-de-passe", v.changer_mot_de_passe),
    path("push/abonnement", v.abonnement_push),
    # Appels
    path("alertes", v.lister_alertes),
    path("alertes/<str:alerte_id>/accuser", v.accuser_reception),
    path("alertes/<str:alerte_id>/terminer", v.terminer),
    # Messagerie
    path("messages", v.messages),
    path("messages-prives/<int:autre_id>", v.messages_prives),
    path("messages-prives/<int:autre_id>/lu", v.marquer_messages_prives_lus),
    path("messages-prives/<int:destinataire_id>/urgence", v.urgence_privee),
    path("messages-prives/<int:destinataire_id>/vocaux", v.vocal_prive),
    path("messages-prives/<int:message_id>/jarrive", v.jarrive),
    path("messages-prives/<int:autre_id>/vocaux/<int:message_id>", v.lire_vocal),
    # Patients et lits
    path("chambres/<str:chambre_id>/lits/<int:lit>/admission", v.admission),
    path("chambres/<str:chambre_id>/lits/<int:lit>/code", v.changer_code_sejour),
    path("chambres/<str:chambre_id>/lits/<int:lit>/sortie-prevue", v.modifier_sortie_prevue),
    path("chambres/<str:chambre_id>/lits/<int:lit>/sortie", v.sortie),
    # Administration
    path("admin/etablissement", v.admin_etablissement),
    path("admin/etablissement/logo", v.admin_logo),
    path("admin/comptes", v.admin_comptes),
    path("admin/comptes/<int:compte_id>/statut", v.admin_statut),
    path("admin/comptes/<int:compte_id>/role", v.admin_role),
    path("admin/comptes/<int:compte_id>/reinitialiser", v.admin_reinitialiser),
    path("admin/comptes/<int:compte_id>/deconnecter", v.admin_deconnecter),
    path("admin/chambres", v.admin_chambres),
    path("admin/chambres/<str:chambre_id>", v.admin_supprimer_chambre),
    path("admin/chambres/<str:chambre_id>/lits", v.admin_changer_lits),
    path("admin/chambres/<str:chambre_id>/code", v.admin_regenerer_code),
    # Bornes patient
    path("bornes/<str:jeton>", v.lire_borne),
    path("bornes/<str:jeton>/activer", v.activer_telephone),
    path("bornes/<str:jeton>/appel", v.appeler),
    path("bornes/<str:jeton>/annuler", v.annuler),
    # Espace propriétaire de la plateforme (page /plateforme)
    path("plateforme/connexion", p.connexion),
    path("plateforme/moi", p.moi),
    path("plateforme/etablissements", p.etablissements),
    path("plateforme/etablissements/<int:etab_id>", p.etablissement),
    path("plateforme/etablissements/<int:etab_id>/activation", p.nouveau_code_activation),
    path("plateforme/etablissements/<int:etab_id>/comptes/<int:compte_id>/reinitialiser", p.reinitialiser_compte),
    path("plateforme/types", p.types),
    path("plateforme/types/<int:type_id>", p.type_etablissement),
    # Temps réel AJAX
    path("temps-reel/soignant", v.sondage_soignant),
    path("temps-reel/borne/<str:jeton>", v.sondage_borne),
]

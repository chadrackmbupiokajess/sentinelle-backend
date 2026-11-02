"""
Routes de l'API Sentinelle (mêmes adresses et mêmes réponses que l'ancien backend FastAPI).

  • Bornes patient   : /api/bornes/{chambre}-{code}/…            (publiques, protégées par le code de porte)
  • Soignants        : /api/…                                      (jeton de session)
  • Administrateurs  : /api/admin/…                                (rôle admin, dans leur établissement)
  • Temps réel AJAX  : /api/temps-reel/soignant, /api/temps-reel/borne/{jeton}
"""

from __future__ import annotations

from django.http import HttpResponse
from rest_framework.decorators import api_view, parser_classes, permission_classes
from rest_framework.response import Response

from .. import push, services, temps_reel
from ..outils import (
    ErreurMetier,
    ip,
    lim_appel_ip,
    lim_appel_lit,
    lim_code_faux_ip,
    lim_connexion_ip,
    lim_etablissement_faux_ip,
    lim_inscription_ip,
    lim_sejour_faux_ip,
)
from . import serializers as s
from .authentification import EstAdministrateur, EstSoignant, jeton_de

TAILLE_MAX_VOCAL = 5 * 1024 * 1024
TYPES_AUDIO_AUTORISES = {"audio/webm", "audio/ogg", "audio/mp4", "audio/mpeg", "audio/wav"}

def message_statut(statut: str, v: dict) -> str:
    return {
        "libre": f"L'accès n'est pas encore ouvert ici. Demandez {v['a_intervenant']} de l'activer.",
        "code_requis": f"Entrez le code donné par {v['l_intervenant']}.",
        "termine": f"Votre {v['sejour']} est terminé : les appels ne sont plus possibles depuis ce téléphone.",
    }[statut]


def _etab(requete):
    return requete.user.etablissement


def _diffuser(requete) -> None:
    temps_reel.publier(requete.user.etablissement_id)


# ==========================================================================
# Généralités et authentification
# ==========================================================================
@api_view(["GET"])
def sante(requete):
    return Response({"ok": True})


@api_view(["GET"])
def config(requete):
    return Response({"premier_compte": services.premier_compte(), "vapid_public_key": push.cle_publique()})


@api_view(["GET"])
def etablissement_public(requete, adresse: str):
    """Page d'un établissement (localhost:3000/<adresse>) : nom, type et vocabulaire."""
    etab = services.etablissement_par_adresse(adresse)
    return Response({**services.etablissement_public(etab), "active": bool(etab.active_le)})


@api_view(["POST"])
def verifier_activation(requete):
    """Page /admin : le responsable colle son code. Valable → adresse de son établissement (code non consommé)."""
    adresse = ip(requete)
    if lim_etablissement_faux_ip.depasse(adresse):
        raise ErreurMetier("Trop de codes incorrects. Réessayez plus tard.", 429)
    d = s.valider(s.CodeActivation, requete.data)
    try:
        etab = services.etablissement_par_activation(d["code"])
    except ErreurMetier:
        lim_etablissement_faux_ip.compter(adresse)
        raise
    return Response(services.etablissement_public(etab))


@api_view(["POST"])
def activer(requete):
    """Création du compte administrateur avec le code d'activation (usage unique)."""
    adresse = ip(requete)
    if lim_etablissement_faux_ip.depasse(adresse):
        raise ErreurMetier("Trop de codes incorrects. Réessayez plus tard.", 429)
    d = s.valider(s.Activation, requete.data)
    try:
        compte = services.activer_etablissement(d["code"], d["identifiant"], d["nom"], d["mot_de_passe"])
    except ErreurMetier as e:
        if e.code == 404:
            lim_etablissement_faux_ip.compter(adresse)
        raise
    jeton = services.creer_session(compte, requete.META.get("HTTP_USER_AGENT", ""))
    return Response({"compte": services.compte_public(compte), "jeton": jeton, "en_attente": False}, status=201)


@api_view(["POST"])
def inscription(requete):
    if not lim_inscription_ip.autoriser(ip(requete)):
        raise ErreurMetier("Trop d'inscriptions depuis cet appareil. Réessayez plus tard.", 429)
    d = s.valider(s.Inscription, requete.data)
    compte = services.inscrire(d["identifiant"], d["nom"], d["mot_de_passe"], d["etablissement"])
    temps_reel.publier(compte.etablissement_id)  # les administrateurs voient la nouvelle demande
    return Response({"compte": services.compte_public(compte), "jeton": None, "en_attente": True}, status=201)


@api_view(["POST"])
def connexion(requete):
    if not lim_connexion_ip.autoriser(ip(requete)):
        raise ErreurMetier("Trop de tentatives depuis cet appareil. Réessayez dans quelques minutes.", 429)
    d = s.valider(s.Connexion, requete.data)
    compte = services.authentifier(d["identifiant"], d["mot_de_passe"], d["etablissement"] or None)
    jeton = services.creer_session(compte, requete.META.get("HTTP_USER_AGENT", ""))
    return Response({"jeton": jeton, "compte": services.compte_public(compte)})


@api_view(["GET"])
@permission_classes([EstSoignant])
def moi(requete):
    return Response(services.compte_public(requete.user))


@api_view(["POST"])
def deconnexion(requete):
    jeton = jeton_de(requete)
    if jeton:
        compte = services.compte_par_jeton(jeton)
        services.supprimer_session(jeton)
        if compte:
            temps_reel.retirer(compte.id)
    return Response(status=204)


@api_view(["POST"])
@permission_classes([EstSoignant])
def changer_mot_de_passe(requete):
    d = s.valider(s.ChangementMotDePasse, requete.data)
    services.changer_mot_de_passe(requete.user, d["actuel"], d["nouveau"])
    return Response(status=204)


@api_view(["POST", "DELETE"])
@permission_classes([EstSoignant])
def abonnement_push(requete):
    compte = requete.user
    if requete.method == "DELETE":
        d = s.valider(s.SuppressionAbonnementPush, requete.data)
        services.supprimer_abonnement_push(compte.id, d["endpoint"])
        return Response(status=204)
    if not push.disponible():
        raise ErreurMetier("Les notifications push ne sont pas configurées sur le serveur.", 503)
    d = s.valider(s.AbonnementPush, requete.data)
    if not d["endpoint"].startswith("https://"):
        raise ErreurMetier("Adresse d'abonnement push invalide.", 400)
    services.enregistrer_abonnement_push(compte, d["endpoint"], d["keys"]["p256dh"], d["keys"]["auth"])
    abonnement = {**d["keys"], "endpoint": d["endpoint"], "compte_id": compte.id}
    for urgence in services.urgences_actives(compte.id):
        push.envoyer_abonnement(
            abonnement,
            push.donnees_urgence(
                urgence["message_id"], urgence["expediteur_nom"], urgence["expediteur_role"],
                compte.etablissement.vocabulaire,
            ),
        )
    return Response({"ok": True}, status=201)


# ==========================================================================
# Soignants : appels
# ==========================================================================
@api_view(["GET"])
@permission_classes([EstSoignant])
def lister_alertes(requete):
    return Response(services.alertes_visibles(_etab(requete)))


@api_view(["POST"])
@permission_classes([EstSoignant])
def accuser_reception(requete, alerte_id: str):
    """Une infirmière accuse réception : l'alarme s'arrête partout."""
    alerte = services.accuser_reception(_etab(requete), alerte_id, requete.user.nom)
    _diffuser(requete)
    return Response(alerte)


@api_view(["POST"])
@permission_classes([EstSoignant])
def terminer(requete, alerte_id: str):
    alerte = services.terminer(_etab(requete), alerte_id, requete.user.nom)
    _diffuser(requete)
    return Response(alerte)


# ==========================================================================
# Soignants : messagerie
# ==========================================================================
@api_view(["GET", "POST"])
@permission_classes([EstSoignant])
def messages(requete):
    if requete.method == "GET":
        return Response(services.messages(_etab(requete)))
    d = s.valider(s.NouveauMessage, requete.data)
    message = services.ajouter_message(_etab(requete), requete.user.nom, d["texte"])
    temps_reel.aux_soignants(requete.user.etablissement_id, {"type": "message", "message": message})
    return Response(message, status=201)


@api_view(["GET", "POST"])
@permission_classes([EstSoignant])
def messages_prives(requete, autre_id: int):
    compte = requete.user
    services.partenaire_medical(compte, autre_id)
    if requete.method == "GET":
        return Response(services.messages_prives(compte.id, autre_id))
    d = s.valider(s.NouveauMessage, requete.data)
    message = services.ajouter_message_prive(compte.id, autre_id, d["texte"])
    temps_reel.a_un_compte(compte.etablissement_id, autre_id, {"type": "message_prive", "message": message})
    return Response(message, status=201)


@api_view(["POST"])
@permission_classes([EstSoignant])
def marquer_messages_prives_lus(requete, autre_id: int):
    compte = requete.user
    services.partenaire_medical(compte, autre_id)
    lecture = services.marquer_messages_prives_lus(compte.id, autre_id)
    if lecture["ids"]:
        temps_reel.a_un_compte(compte.etablissement_id, autre_id, {"type": "messages_prives_lus", **lecture})
    return Response(lecture)


@api_view(["POST"])
@permission_classes([EstSoignant])
def urgence_privee(requete, destinataire_id: int):
    compte = requete.user
    destinataire = services.partenaire_medical(compte, destinataire_id)
    if not temps_reel.compte_en_ligne(destinataire):
        return Response(services.ajouter_appel_manque(compte.id, destinataire_id), status=201)
    message = services.ajouter_message_prive(compte.id, destinataire_id, "🚨 URGENCE — Vous êtes demandé(e).")
    services.ajouter_urgence(message["id"], destinataire_id)  # sonne jusqu'au « J'arrive »
    etab_id = compte.etablissement_id
    temps_reel.a_un_compte(etab_id, destinataire_id, {"type": "message_prive", "message": message, "urgence": True})
    urgence = next(u for u in services.urgences_actives(destinataire_id) if u["message_id"] == message["id"])
    temps_reel.a_un_compte(etab_id, destinataire_id, {"type": "urgence_debut", "urgence": urgence})
    push.envoyer_compte(destinataire_id, push.donnees_urgence(message["id"], compte.nom, compte.role, compte.etablissement.vocabulaire))
    return Response(message, status=201)


@api_view(["POST"])
@parser_classes([])
@permission_classes([EstSoignant])
def vocal_prive(requete, destinataire_id: int):
    compte = requete.user
    services.partenaire_medical(compte, destinataire_id)
    audio_type = requete.META.get("CONTENT_TYPE", "").split(";", 1)[0].strip().lower()
    if audio_type not in TYPES_AUDIO_AUTORISES:
        raise ErreurMetier("Format audio non pris en charge par ce navigateur.", 415)
    longueur = requete.META.get("CONTENT_LENGTH")
    if longueur:
        try:
            if int(longueur) > TAILLE_MAX_VOCAL:
                raise ErreurMetier("Le vocal dépasse la taille maximale de 5 Mo.", 413)
        except ValueError:
            raise ErreurMetier("Taille de vocal invalide.", 400)
    audio = bytearray()
    flux = requete._request
    while bloc := flux.read(64 * 1024):
        if len(audio) + len(bloc) > TAILLE_MAX_VOCAL:
            raise ErreurMetier("Le vocal dépasse la taille maximale de 5 Mo.", 413)
        audio.extend(bloc)
    message = services.ajouter_vocal_prive(compte.id, destinataire_id, bytes(audio), audio_type)
    temps_reel.a_un_compte(compte.etablissement_id, destinataire_id, {"type": "message_prive", "message": message})
    return Response(message, status=201)


@api_view(["POST"])
@permission_classes([EstSoignant])
def jarrive(requete, message_id: int):
    """« J'arrive » : clôt une urgence privée. Seul son destinataire peut le faire."""
    compte = requete.user
    urgence = services.urgence_par_message(message_id)
    if not urgence or not urgence.actif:
        raise ErreurMetier("Aucune urgence active pour ce message.", 400)
    if urgence.destinataire_id != compte.id:
        raise ErreurMetier("Vous n'êtes pas le destinataire de cette urgence.", 403)
    fermeture = services.clore_urgence(message_id, compte.nom)
    temps_reel.a_un_compte(
        compte.etablissement_id, compte.id, {"type": "urgence_terminee", "message_id": message_id, "par": compte.nom}
    )
    push.envoyer_compte(compte.id, {"type": "urgence_terminee", "tag": f"urgence-privee-{message_id}"})
    _diffuser(requete)
    return Response({"ok": True, "fermeture": fermeture})


@api_view(["GET"])
@permission_classes([EstSoignant])
def lire_vocal(requete, autre_id: int, message_id: int):
    compte = requete.user
    services.partenaire_medical(compte, autre_id)
    vocal = services.vocal_prive(message_id, compte.id, autre_id)
    if not vocal or not vocal.audio:
        raise ErreurMetier("Message vocal introuvable.", 404)
    reponse = HttpResponse(bytes(vocal.audio), content_type=vocal.audio_type)
    reponse["Content-Disposition"] = "inline"
    return reponse


# ==========================================================================
# Soignants : patients et lits
# ==========================================================================
@api_view(["POST"])
@permission_classes([EstSoignant])
def admission(requete, chambre_id: str, lit: int):
    """Admettre un patient sur un lit : renvoie le code de séjour à lui donner."""
    d = s.valider(s.Admission, requete.data)
    sejour = services.admettre(_etab(requete), chambre_id, lit, d["initiales"], requete.user.nom, d["sortie_prevue"])
    _diffuser(requete)
    return Response(sejour, status=201)


@api_view(["POST"])
@permission_classes([EstSoignant])
def changer_code_sejour(requete, chambre_id: str, lit: int):
    sejour = services.changer_code_sejour(_etab(requete), chambre_id, lit)
    _diffuser(requete)
    return Response(sejour)


@api_view(["POST"])
@permission_classes([EstSoignant])
def modifier_sortie_prevue(requete, chambre_id: str, lit: int):
    """Prolonger ou avancer la sortie automatique (ou la retirer)."""
    d = s.valider(s.SortiePrevue, requete.data)
    sejour = services.modifier_sortie_prevue(_etab(requete), chambre_id, lit, d["sortie_prevue"])
    _diffuser(requete)
    return Response(sejour)


@api_view(["POST"])
@permission_classes([EstSoignant])
def sortie(requete, chambre_id: str, lit: int):
    """Sortie du patient de ce lit : son téléphone ne peut plus appeler."""
    sejour = services.sortie(_etab(requete), chambre_id, lit, requete.user.nom)
    _diffuser(requete)
    return Response(sejour)


# ==========================================================================
# Administration (dans l'établissement de l'administrateur)
# ==========================================================================
@api_view(["GET", "POST"])
@permission_classes([EstAdministrateur])
def admin_etablissement(requete):
    """Nom, adresse et type de l'établissement (l'adresse sert au personnel pour demander un compte)."""
    etab = _etab(requete)
    if requete.method == "GET":
        return Response(services.etablissement_public(etab))
    d = s.valider(s.NomEtablissement, requete.data)
    return Response(services.renommer_etablissement(etab, d["nom"]))


@api_view(["GET"])
@permission_classes([EstAdministrateur])
def admin_comptes(requete):
    return Response(services.lister_comptes(_etab(requete)))


@api_view(["POST"])
@permission_classes([EstAdministrateur])
def admin_statut(requete, compte_id: int):
    d = s.valider(s.NouveauStatut, requete.data)
    compte = services.changer_statut(_etab(requete), compte_id, d["statut"], requete.user)
    if compte["statut"] != "actif":
        temps_reel.fermer_compte(requete.user.etablissement_id, compte_id)
    _diffuser(requete)
    return Response(compte)


@api_view(["POST"])
@permission_classes([EstAdministrateur])
def admin_role(requete, compte_id: int):
    d = s.valider(s.NouveauRole, requete.data)
    compte = services.changer_role(_etab(requete), compte_id, d["role"], requete.user)
    temps_reel.fermer_compte(requete.user.etablissement_id, compte_id)  # se reconnecte avec ses nouveaux droits
    _diffuser(requete)
    return Response(compte)


@api_view(["POST"])
@permission_classes([EstAdministrateur])
def admin_reinitialiser(requete, compte_id: int):
    provisoire = services.reinitialiser_mot_de_passe(_etab(requete), compte_id)
    temps_reel.fermer_compte(requete.user.etablissement_id, compte_id)
    _diffuser(requete)
    return Response({"mot_de_passe": provisoire})


@api_view(["POST"])
@permission_classes([EstAdministrateur])
def admin_deconnecter(requete, compte_id: int):
    services._exiger_compte(_etab(requete), compte_id)
    services.supprimer_sessions_compte(compte_id)
    temps_reel.fermer_compte(requete.user.etablissement_id, compte_id)
    _diffuser(requete)
    return Response(status=204)


@api_view(["GET", "POST"])
@permission_classes([EstAdministrateur])
def admin_chambres(requete):
    if requete.method == "GET":
        return Response(services.lister_chambres(_etab(requete), avec_codes=True))
    d = s.valider(s.NouvelleChambre, requete.data)
    chambre = services.ajouter_chambre(_etab(requete), d["id"], d["libelle"], d["service"], d["lits"])
    _diffuser(requete)
    return Response(chambre, status=201)


@api_view(["POST"])
@permission_classes([EstAdministrateur])
def admin_changer_lits(requete, chambre_id: str):
    d = s.valider(s.NombreLits, requete.data)
    chambre = services.changer_lits(_etab(requete), chambre_id, d["lits"])
    _diffuser(requete)
    return Response(chambre)


@api_view(["DELETE"])
@permission_classes([EstAdministrateur])
def admin_supprimer_chambre(requete, chambre_id: str):
    services.supprimer_chambre(_etab(requete), chambre_id)
    temps_reel.fermer_bornes(requete.user.etablissement_id, chambre_id)
    _diffuser(requete)
    return Response(status=204)


@api_view(["POST"])
@permission_classes([EstAdministrateur])
def admin_regenerer_code(requete, chambre_id: str):
    chambre = services.regenerer_code(_etab(requete), chambre_id)
    temps_reel.fermer_bornes(requete.user.etablissement_id, chambre_id)
    return Response(chambre)


# ==========================================================================
# Bornes patient (publiques, protégées par le code de la chambre)
# ==========================================================================
def _borne(jeton: str, requete):
    """Chambre correspondant au lien imprimé sur la porte (ex. « 104-K7QM »)."""
    adresse = ip(requete)
    if lim_code_faux_ip.depasse(adresse):
        raise ErreurMetier("Trop de tentatives. Réessayez plus tard.", 429)
    chambre = services.chambre_par_jeton(jeton)
    if not chambre:
        lim_code_faux_ip.compter(adresse)
        raise ErreurMetier("Code de chambre invalide. Scannez de nouveau le code sur la porte.", 404)
    return chambre


def _jeton_sejour(requete) -> str | None:
    return requete.META.get("HTTP_X_SEJOUR") or None


def _exiger_sejour_actif(chambre, requete):
    statut, sejour = services.appareil(chambre, _jeton_sejour(requete))
    if statut != "actif":
        raise ErreurMetier(message_statut(statut, chambre.etablissement.vocabulaire), 403)
    return sejour


@api_view(["GET"])
def lire_borne(requete, jeton: str):
    chambre = _borne(jeton, requete)
    statut, sejour = services.appareil(chambre, _jeton_sejour(requete))
    return Response(
        {
            "chambre": services.chambre_dict(chambre),
            "etablissement": services.etablissement_public(chambre.etablissement),
            "statut": statut,
            "lit": sejour.lit if sejour else None,
            "alerte": services.alerte_active_sejour(sejour.id) if sejour else None,
        }
    )


@api_view(["POST"])
def activer_telephone(requete, jeton: str):
    """Le patient saisit le code de séjour une fois : son téléphone peut ensuite appeler."""
    chambre = _borne(jeton, requete)
    adresse = ip(requete)
    if lim_sejour_faux_ip.depasse(adresse):
        raise ErreurMetier("Trop d'essais. Réessayez dans quelques minutes ou demandez l'aide d'une infirmière.", 429)
    d = s.valider(s.CodeSejour, requete.data)
    try:
        jeton_tel = services.activer_appareil(chambre, d["code"])
    except ErreurMetier as e:
        if e.code == 401:
            lim_sejour_faux_ip.compter(adresse)
        raise
    return Response({"jeton_sejour": jeton_tel})


@api_view(["POST"])
def appeler(requete, jeton: str):
    """Le patient appuie sur le bouton."""
    chambre = _borne(jeton, requete)
    sejour = _exiger_sejour_actif(chambre, requete)
    existante = services.alerte_active_sejour(sejour.id)
    if existante:
        return Response(existante)
    if not lim_appel_lit.autoriser(sejour.id) or not lim_appel_ip.autoriser(ip(requete)):
        conseil = chambre.etablissement.vocabulaire["conseil_urgence"]
        raise ErreurMetier(f"Trop d'appels en peu de temps. {conseil}", 429)
    alerte, _ = services.creer_alerte(sejour)
    temps_reel.publier(chambre.etablissement_id)
    return Response(alerte)


@api_view(["POST"])
def annuler(requete, jeton: str):
    """Le patient annule son appel."""
    chambre = _borne(jeton, requete)
    sejour = _exiger_sejour_actif(chambre, requete)
    alerte = services.annuler_sejour(sejour.id)
    v = chambre.etablissement.vocabulaire
    if alerte:
        temps_reel.publier(chambre.etablissement_id)
        temps_reel.aux_soignants(
            chambre.etablissement_id,
            {
                "type": "info",
                "texte": f"{alerte['chambre_libelle']}, {v['place'].lower()} {alerte['lit']} : "
                f"appel annulé par le {v['beneficiaire'].lower()}.",
            },
        )
    return Response({"ok": True})


# ==========================================================================
# Temps réel par AJAX (mêmes messages que les WebSockets)
# ==========================================================================
@api_view(["GET"])
def sondage_soignant(requete):
    compte = requete.user or services.compte_par_jeton(requete.query_params.get("jeton"))
    if not compte:
        raise ErreurMetier("Session expirée. Reconnectez-vous.", 401)
    temps_reel.verifier_sorties_si_necessaire()
    if temps_reel.marquer_present(compte):
        temps_reel.publier(compte.etablissement_id)  # collègues en WebSocket : liste « équipe » à jour
    curseur = requete.query_params.get("curseur")
    if curseur is None or not curseur.isdigit():
        # Première requête : mêmes messages qu'à l'ouverture du WebSocket.
        nouveau = temps_reel.dernier_id()
        evenements = temps_reel.messages_ouverture(compte)
    else:
        evenements, nouveau = temps_reel.evenements_depuis(int(curseur), compte)
    evenements.append(temps_reel.etat_soignants(compte))
    return Response({"curseur": nouveau, "evenements": evenements})


@api_view(["GET"])
def sondage_borne(requete, jeton: str):
    chambre = _borne(jeton, requete)  # 404 si le code de chambre n'est plus valide
    temps_reel.verifier_sorties_si_necessaire()
    sejour = _jeton_sejour(requete) or requete.query_params.get("sejour") or None
    return Response({"curseur": None, "evenements": [temps_reel.etat_borne(chambre, sejour)]})

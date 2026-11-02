"""
WebSockets (Django Channels) — pour le VPS. Mêmes messages que l'AJAX.

  /ws/soignant?jeton=…           écran soignant
  /ws/borne/{chambre}-{code}     téléphone du patient (?sejour=… une fois le code saisi)

Codes de fermeture : 4401 session expirée / fermée, 4404 code de chambre invalide.
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

from . import services, temps_reel
from .outils import ip, lim_code_faux_ip


def _parametres(scope) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(scope.get("query_string", b"").decode()).items()}


class EcranSoignant(AsyncWebsocketConsumer):
    compte = None
    groupes: list[str] = []

    async def connect(self):
        await self.accept()
        self.jeton = _parametres(self.scope).get("jeton", "")
        self.compte = await database_sync_to_async(services.compte_par_jeton)(self.jeton)
        if not self.compte:
            await self.close(code=4401)
            return
        etab_id = self.compte.etablissement_id
        self.groupes = [temps_reel.groupe_soignants(etab_id), temps_reel.groupe_compte(self.compte.id)]
        for groupe in self.groupes:
            await self.channel_layer.group_add(groupe, self.channel_name)
        nouveau = await database_sync_to_async(temps_reel.marquer_present)(self.compte)
        for message in await database_sync_to_async(temps_reel.messages_ouverture)(self.compte):
            await self._envoyer(message)
        await self._envoyer(await database_sync_to_async(temps_reel.etat_soignants)(self.compte))
        if nouveau:
            await database_sync_to_async(temps_reel.publier)(etab_id)  # liste « équipe » des collègues

    async def disconnect(self, code):
        for groupe in self.groupes:
            await self.channel_layer.group_discard(groupe, self.channel_name)
        if self.compte:
            await database_sync_to_async(temps_reel.expirer_bientot)(self.compte.id)

    async def receive(self, text_data=None, bytes_data=None):
        if text_data != "ping" or not self.compte:
            return
        await self.send("pong")
        valide = await database_sync_to_async(services.compte_par_jeton)(self.jeton)
        if not valide:
            await self.close(code=4401)
            return
        await database_sync_to_async(temps_reel.marquer_present)(self.compte)
        await database_sync_to_async(temps_reel.verifier_sorties_si_necessaire)()

    # --- messages des groupes (voir temps_reel.py)
    async def publier(self, _evenement):
        await self._envoyer(await database_sync_to_async(temps_reel.etat_soignants)(self.compte))

    async def transmettre(self, evenement):
        await self._envoyer(evenement["donnees"])

    async def fermer(self, evenement):
        await self.close(code=evenement.get("code", 4401))

    async def fermer_chambre(self, _evenement):
        pass

    async def _envoyer(self, donnees: dict):
        await self.send(json.dumps(donnees, ensure_ascii=False))


class BornePatient(AsyncWebsocketConsumer):
    chambre = None

    async def connect(self):
        await self.accept()
        adresse = ip(self.scope)
        jeton = self.scope["url_route"]["kwargs"]["jeton"]
        self.chambre = None if lim_code_faux_ip.depasse(adresse) else await database_sync_to_async(
            services.chambre_par_jeton
        )(jeton)
        if not self.chambre:
            lim_code_faux_ip.compter(adresse)
            await self.close(code=4404)
            return
        self.sejour = _parametres(self.scope).get("sejour") or None
        self.groupe = temps_reel.groupe_bornes(self.chambre.etablissement_id)
        await self.channel_layer.group_add(self.groupe, self.channel_name)
        await self.publier(None)

    async def disconnect(self, code):
        if self.chambre:
            await self.channel_layer.group_discard(self.groupe, self.channel_name)

    async def receive(self, text_data=None, bytes_data=None):
        if text_data == "ping":
            await self.send("pong")

    async def publier(self, _evenement):
        etat = await database_sync_to_async(temps_reel.etat_borne)(self.chambre, self.sejour)
        await self.send(json.dumps(etat, ensure_ascii=False))

    async def fermer_chambre(self, evenement):
        if evenement.get("numero") == self.chambre.numero:
            await self.close(code=4404)

    async def transmettre(self, _evenement):
        pass

    async def fermer(self, _evenement):
        pass


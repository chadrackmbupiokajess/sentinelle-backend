"""
Tests de Sentinelle :  python manage.py test

Parcours complet : le propriétaire crée l'établissement (type, adresse, code d'activation),
le responsable active avec le code (usage unique, 7 jours), le personnel s'inscrit sur
l'adresse, isolation entre établissements, vocabulaire, temps réel AJAX.
"""

from datetime import timedelta

from django.test import TestCase
from rest_framework.test import APIClient

from .models import Compte, Etablissement, Sejour, TypeEtablissement
from .outils import TOUS_LES_LIMITEURS, maintenant


class Base(TestCase):
    def setUp(self):
        for limiteur in TOUS_LES_LIMITEURS:
            limiteur.vider()
        self.c = APIClient()
        Compte.objects.create_superuser("proprio", "Proprio123", nom="Propriétaire")
        s, r = self.api("post", "/api/plateforme/connexion", {"identifiant": "proprio", "mot_de_passe": "Proprio123"})
        self.proprio = r["jeton"]

    def api(self, methode, chemin, corps=None, jeton=None, **entetes):
        if jeton:
            entetes["HTTP_AUTHORIZATION"] = f"Bearer {jeton}"
        r = getattr(self.c, methode)(chemin, corps, format="json", **entetes)
        return r.status_code, (r.json() if r.content and r["Content-Type"].startswith("application/json") else None)

    def type_id(self, nom):
        return TypeEtablissement.objects.get(nom=nom).id

    def nouvel_etablissement(self, nom, type_nom="Hôpital"):
        s, e = self.api("post", "/api/plateforme/etablissements", {"nom": nom, "type": self.type_id(type_nom)}, self.proprio)
        self.assertEqual(s, 201, e)
        return e

    def activer(self, code, identifiant):
        return self.api("post", "/api/activation", {
            "code": code, "identifiant": identifiant, "nom": identifiant.title(), "mot_de_passe": "secret123",
        })

    def etablissement_actif(self, nom, admin, type_nom="Hôpital"):
        e = self.nouvel_etablissement(nom, type_nom)
        s, r = self.activer(e["code_activation"], admin)
        self.assertEqual(s, 201, r)
        return r["jeton"], e["slug"]

    def ajouter_lieu(self, admin, numero, **champs):
        s, r = self.api("post", "/api/admin/chambres", {"id": numero, **champs}, admin)
        self.assertEqual(s, 201, r)
        return r

    def soignant(self, identifiant, adresse, admin):
        s, r = self.api("post", "/api/auth/inscription", {
            "identifiant": identifiant, "nom": identifiant.title(), "mot_de_passe": "secret123", "etablissement": adresse,
        })
        self.assertEqual(s, 201, r)
        self.assertTrue(r["en_attente"])
        self.api("post", f"/api/admin/comptes/{r['compte']['id']}/statut", {"statut": "actif"}, admin)
        s, c = self.api("post", "/api/auth/connexion", {"identifiant": identifiant, "mot_de_passe": "secret123", "etablissement": adresse})
        self.assertEqual(s, 200, c)
        return c["jeton"], r["compte"]["id"]


class Activation(Base):
    def test_adresse_et_code(self):
        e = self.nouvel_etablissement("Jessna Hôpital")
        self.assertEqual(e["slug"], "jessna-hopital")
        self.assertEqual(e["activation"], "en_attente")
        self.assertRegex(e["code_activation"], r"^[A-Z2-9]{4}-[A-Z2-9]{4}$")
        self.assertEqual(self.nouvel_etablissement("Admin")["slug"], "admin-2")  # adresse réservée évitée

    def test_code_valable_une_seule_fois(self):
        e = self.nouvel_etablissement("Jessna Hôpital")
        s, r = self.api("post", "/api/activation/verifier", {"code": e["code_activation"].lower().replace("-", "")})
        self.assertEqual((s, r["slug"]), (200, "jessna-hopital"))
        s, r = self.activer(e["code_activation"], "chadrack")
        self.assertEqual((s, r["compte"]["role"]), (201, "admin"))
        self.assertTrue(r["jeton"])
        s, r = self.api("post", "/api/activation/verifier", {"code": e["code_activation"]})
        self.assertEqual((s, r["detail"]), (404, "Code invalide."))
        self.assertEqual(self.activer(e["code_activation"], "intrus")[0], 404)

    def test_code_expire(self):
        e = self.nouvel_etablissement("Jessna Hôpital")
        Etablissement.objects.filter(id=e["id"]).update(code_activation_expire=maintenant() - timedelta(seconds=1))
        self.assertEqual(self.api("post", "/api/activation/verifier", {"code": e["code_activation"]})[1]["detail"], "Code invalide.")
        s, n = self.api("post", f"/api/plateforme/etablissements/{e['id']}/activation", jeton=self.proprio)
        self.assertEqual((s, n["activation"]), (200, "en_attente"))
        self.assertEqual(self.api("post", "/api/activation/verifier", {"code": n["code_activation"]})[0], 200)

    def test_inscription_avant_activation_refusee(self):
        e = self.nouvel_etablissement("Jessna Hôpital")
        s, r = self.api("post", "/api/auth/inscription", {"identifiant": "bob", "nom": "Bob", "mot_de_passe": "secret123", "etablissement": e["slug"]})
        self.assertEqual(s, 409)

    def test_etablissement_vide_au_depart(self):
        admin, _ = self.etablissement_actif("Clinique Vide", "vide")
        self.assertEqual(self.api("get", "/api/admin/chambres", jeton=admin)[1], [])

    def test_page_publique(self):
        _, adresse = self.etablissement_actif("Chez Mama", "mama", "Restaurant")
        s, r = self.api("get", f"/api/etablissements/{adresse}")
        self.assertEqual((s, r["type"], r["vocabulaire"]["lieu"], r["active"]), (200, "Restaurant", "Table", True))
        self.assertEqual(self.api("get", "/api/etablissements/inconnu")[0], 404)

    def test_connexion_limitee_a_son_etablissement(self):
        admin_a, a = self.etablissement_actif("Clinique A", "alice")
        _, b = self.etablissement_actif("Clinique B", "zoe")
        self.assertEqual(self.api("post", "/api/auth/connexion", {"identifiant": "alice", "mot_de_passe": "secret123", "etablissement": b})[0], 401)
        self.assertEqual(self.api("post", "/api/auth/connexion", {"identifiant": "alice", "mot_de_passe": "secret123", "etablissement": a})[0], 200)

    def test_meme_identifiant_dans_deux_etablissements(self):
        admin_mbu, mbu = self.etablissement_actif("Mbu", "chef")
        admin_fleur, fleur = self.etablissement_actif("Lafleur", "chef")  # même identifiant d'administrateur
        self.soignant("mechack", mbu, admin_mbu)
        self.soignant("mechack", fleur, admin_fleur)
        self.assertEqual(Compte.objects.filter(identifiant="mechack").count(), 2)
        # Deux fois dans le même établissement : refusé.
        s, r = self.api("post", "/api/auth/inscription", {
            "identifiant": "mechack", "nom": "Autre", "mot_de_passe": "secret123", "etablissement": mbu,
        })
        self.assertEqual(s, 409, r)
        # Chaque page d'établissement connecte SON mechack.
        for adresse, nom in ((mbu, "Mbu"), (fleur, "Lafleur")):
            s, r = self.api("post", "/api/auth/connexion", {"identifiant": "mechack", "mot_de_passe": "secret123", "etablissement": adresse})
            self.assertEqual((s, r["compte"]["etablissement"]["nom"]), (200, nom))
        # Sans adresse : impossible de savoir lequel.
        s, r = self.api("post", "/api/auth/connexion", {"identifiant": "mechack", "mot_de_passe": "secret123"})
        self.assertEqual(s, 409, r)
        # Les équipes restent séparées.
        s, equipe = self.api("get", "/api/admin/comptes", jeton=admin_mbu)
        self.assertEqual(sorted(c["identifiant"] for c in equipe), ["chef", "mechack"])

    def test_proprietaire_et_admin_django(self):
        admin_a, a = self.etablissement_actif("Mbu", "proprio")  # même identifiant que le propriétaire
        self.assertEqual(self.api("post", "/api/auth/connexion", {"identifiant": "proprio", "mot_de_passe": "secret123", "etablissement": a})[0], 200)
        self.assertEqual(self.api("post", "/api/plateforme/connexion", {"identifiant": "proprio", "mot_de_passe": "Proprio123"})[0], 200)
        self.assertEqual(self.api("post", "/api/plateforme/connexion", {"identifiant": "proprio", "mot_de_passe": "secret123"})[0], 401)
        self.assertTrue(self.client.login(username="proprio", password="Proprio123"))   # admin Django
        self.assertFalse(self.client.login(username="proprio", password="secret123"))  # pas le compte d'établissement
        with self.assertRaises(Exception):
            Compte.objects.create_superuser("proprio", "Autre1234")  # unique entre propriétaires

    def test_logo(self):
        import base64
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
        b64 = lambda b: "data:image/png;base64," + base64.b64encode(b).decode()
        admin, mbu = self.etablissement_actif("Mbu", "chef")
        jeton_soignant, _ = self.soignant("mechack", mbu, admin)
        s, e = self.api("get", f"/api/etablissements/{mbu}")
        self.assertEqual((e["logo"], e["icone"]), (None, None))
        self.assertEqual(self.c.get(f"/api/etablissements/{mbu}/logo").status_code, 404)
        # Réservé à l'administrateur
        corps = {"image": b64(png), "icone": b64(png), "icone_masquable": b64(png)}
        self.assertEqual(self.api("put", "/api/admin/etablissement/logo", corps, jeton_soignant)[0], 403)
        # Formats refusés : SVG (peut contenir du code), texte déguisé
        for faux in (b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>", b"bonjour"):
            s, r = self.api("put", "/api/admin/etablissement/logo", {**corps, "image": b64(faux)}, admin)
            self.assertEqual(s, 415, r)
        s, r = self.api("put", "/api/admin/etablissement/logo", {**corps, "image": "pas du base64 !"}, admin)
        self.assertEqual(s, 400, r)
        s, r = self.api("put", "/api/admin/etablissement/logo", {**corps, "image": b64(b"\x89PNG\r\n\x1a\n" + b"0" * (1024 * 1024 + 1))}, admin)
        self.assertEqual(s, 413, r)
        # JPEG accepté pour le logo
        jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 32
        s, e = self.api("put", "/api/admin/etablissement/logo", {**corps, "image": b64(jpeg)}, admin)
        self.assertEqual(s, 200, e)
        self.assertEqual(e["logo"], f"/api/etablissements/{mbu}/logo?v=1")
        r = self.c.get(e["logo"])
        self.assertEqual((r.status_code, r["Content-Type"], r.content), (200, "image/jpeg", jpeg))
        self.assertIn("immutable", r["Cache-Control"])
        self.assertEqual(r["Cross-Origin-Resource-Policy"], "cross-origin")
        r = self.c.get(f"/api/etablissements/{mbu}/icone-masquable?v=1")
        self.assertEqual((r.status_code, r["Content-Type"]), (200, "image/png"))
        # Visible partout : page publique, session du personnel, liste du propriétaire
        self.assertEqual(self.api("get", f"/api/etablissements/{mbu}")[1]["icone"], f"/api/etablissements/{mbu}/icone?v=1")
        self.assertEqual(self.api("get", "/api/auth/moi", jeton=jeton_soignant)[1]["etablissement"]["logo"], e["logo"])
        liste = self.api("get", "/api/plateforme/etablissements", jeton=self.proprio)[1]
        liste = liste["etablissements"] if isinstance(liste, dict) else liste
        self.assertEqual(next(x for x in liste if x["slug"] == mbu)["logo"], e["logo"])
        # Nouveau logo → nouvelle adresse ; retrait → plus de logo, adresse jamais réutilisée
        self.assertEqual(self.api("put", "/api/admin/etablissement/logo", corps, admin)[1]["logo"], f"/api/etablissements/{mbu}/logo?v=2")
        s, e = self.api("delete", "/api/admin/etablissement/logo", None, admin)
        self.assertEqual((s, e["logo"]), (200, None))
        self.assertEqual(self.c.get(f"/api/etablissements/{mbu}/logo?v=2").status_code, 404)
        self.assertEqual(self.api("put", "/api/admin/etablissement/logo", corps, admin)[1]["logo"], f"/api/etablissements/{mbu}/logo?v=4")
        # Les autres réponses de l'API restent sans cache
        self.assertEqual(self.c.get(f"/api/etablissements/{mbu}")["Cache-Control"], "no-store")

    def test_mot_de_passe_faible(self):
        e = self.nouvel_etablissement("Jessna Hôpital")
        s, r = self.api("post", "/api/activation", {"code": e["code_activation"], "identifiant": "chadrack", "nom": "C", "mot_de_passe": "abcdefgh"})
        self.assertEqual(s, 422)
        s, r = self.api("post", "/api/activation", {"code": e["code_activation"], "identifiant": "chadrack", "nom": "Chadrack", "mot_de_passe": "abcdefgh"})
        self.assertIn("lettres et des chiffres", r["detail"])
        self.assertEqual(self.api("post", "/api/activation/verifier", {"code": e["code_activation"]})[0], 200)  # pas consommé


class Vocabulaire(Base):
    def test_restaurant(self):
        admin, adresse = self.etablissement_actif("Chez Mama", "mama", "Restaurant")
        moi = self.api("get", "/api/auth/moi", jeton=admin)[1]
        self.assertEqual(moi["etablissement"]["vocabulaire"]["beneficiaire"], "Client")
        self.ajouter_lieu(admin, "101", service="Terrasse", lits=4)
        chambres = self.api("get", "/api/admin/chambres", jeton=admin)[1]
        self.assertEqual((chambres[0]["libelle"], chambres[0]["lits"], chambres[0]["service"]), ("Table 101", 4, "Terrasse"))
        s, r = self.api("post", "/api/chambres/101/lits/9/admission", {}, admin)
        self.assertEqual(r["detail"], "La table 101 compte 4 places.")
        self.api("post", "/api/chambres/101/lits/1/admission", {}, admin)
        s, r = self.api("post", "/api/chambres/101/lits/1/admission", {}, admin)
        self.assertEqual(r["detail"], "La place 1 n'est pas libre.")
        borne = f"101-{chambres[0]['code']}"
        s, r = self.api("get", f"/api/bornes/{borne}")
        self.assertEqual(r["etablissement"]["vocabulaire"]["lieu"], "Table")

    def test_types_modifiables(self):
        s, t = self.api("post", "/api/plateforme/types", {"nom": "Hôtel", "vocabulaire": {"lieu": "Suite", "lieux": "Suites", "beneficiaire": "Client"}}, self.proprio)
        self.assertEqual((s, t["vocabulaire"]["lieu"], t["vocabulaire"]["place"]), (201, "Suite", "Lit"))
        s, t = self.api("post", f"/api/plateforme/types/{t['id']}", {"vocabulaire": {"lieu": "Chambre d'hôtel"}}, self.proprio)
        self.assertEqual(t["vocabulaire"]["lieu"], "Chambre d'hôtel")
        e = self.api("post", "/api/plateforme/etablissements", {"nom": "Grand Hôtel", "type": t["id"]}, self.proprio)[1]
        self.assertEqual(self.api("delete", f"/api/plateforme/types/{t['id']}", jeton=self.proprio)[0], 409)
        self.assertEqual(e["type"]["nom"], "Hôtel")


class Isolation(Base):
    def setUp(self):
        super().setUp()
        self.alice, self.a = self.etablissement_actif("Clinique A", "alice")
        self.zoe, self.b = self.etablissement_actif("Hôpital B", "zoe")
        self.bob, self.bob_id = self.soignant("bob", self.a, self.alice)

    def test_comptes(self):
        self.assertNotIn("bob", [c["identifiant"] for c in self.api("get", "/api/admin/comptes", jeton=self.zoe)[1]])
        self.assertEqual(self.api("post", f"/api/admin/comptes/{self.bob_id}/statut", {"statut": "desactive"}, self.zoe)[0], 404)

    def test_appel_patient(self):
        cha = [self.ajouter_lieu(self.alice, "101")]
        chb = [self.ajouter_lieu(self.zoe, "101")]
        a101, b101 = f"101-{cha[0]['code']}", f"101-{chb[0]['code']}"
        s, sej = self.api("post", "/api/chambres/101/lits/1/admission", {"initiales": "jd"}, self.bob)
        self.assertEqual(self.api("get", f"/api/bornes/{b101}")[1]["statut"], "libre")
        r = self.api("post", f"/api/bornes/{a101}/activer", {"code": sej["code"]})[1]
        sejour = {"HTTP_X_SEJOUR": r["jeton_sejour"]}
        alerte = self.api("post", f"/api/bornes/{a101}/appel", **sejour)[1]
        self.assertEqual(self.api("get", "/api/alertes", jeton=self.zoe)[1], [])
        self.assertEqual(self.api("post", f"/api/alertes/{alerte['id']}/accuser", jeton=self.zoe)[0], 404)
        self.assertEqual(self.api("post", f"/api/alertes/{alerte['id']}/accuser", jeton=self.bob)[1]["accuse_par"], "Bob")

    def test_messages(self):
        self.api("post", "/api/messages", {"texte": "privé à A"}, self.alice)
        self.assertEqual(self.api("get", "/api/messages", jeton=self.zoe)[1], [])
        self.assertEqual(self.api("post", f"/api/messages-prives/{self.bob_id}", {"texte": "x"}, self.zoe)[0], 404)


class TempsReelAjax(Base):
    def setUp(self):
        super().setUp()
        self.alice, adresse = self.etablissement_actif("Clinique A", "alice")
        self.bob, self.bob_id = self.soignant("bob", adresse, self.alice)

    def sonder(self, jeton, curseur=None):
        chemin = "/api/temps-reel/soignant" + (f"?curseur={curseur}" if curseur is not None else "")
        r = self.api("get", chemin, jeton=jeton)[1]
        return [e["type"] for e in r["evenements"]], r

    def test_evenements_et_fermeture(self):
        types, r = self.sonder(self.bob)
        self.assertEqual(types, ["moi", "messages", "etat"])
        self.api("post", "/api/messages", {"texte": "Bonjour"}, self.alice)
        types, r = self.sonder(self.bob, r["curseur"])
        self.assertEqual(types, ["message", "etat"])
        urgence = self.api("post", f"/api/messages-prives/{self.bob_id}/urgence", jeton=self.alice)[1]
        self.assertIn("URGENCE", urgence["texte"])
        types, r = self.sonder(self.bob, r["curseur"])
        self.assertEqual(types, ["message_prive", "urgence_debut", "etat"])
        self.api("post", f"/api/admin/comptes/{self.bob_id}/role", {"role": "medecin"}, self.alice)
        _, r = self.sonder(self.bob, r["curseur"])
        self.assertEqual(r["evenements"][0], {"type": "_fermeture", "code": 4401})

    def test_depart_automatique(self):
        from . import temps_reel

        self.ajouter_lieu(self.alice, "103")
        sej = self.api("post", "/api/chambres/103/lits/2/admission", {"initiales": "XY"}, self.bob)[1]
        _, r = self.sonder(self.bob)
        Sejour.objects.filter(id=sej["id"]).update(expire_le=maintenant() - timedelta(minutes=1))
        temps_reel._derniere_verification = 0.0
        types, r = self.sonder(self.bob, r["curseur"])
        self.assertIn("info", types)


class Plateforme(Base):
    def test_acces_reserve(self):
        admin, _ = self.etablissement_actif("Clinique A", "alice")
        self.assertEqual(self.api("post", "/api/plateforme/connexion", {"identifiant": "alice", "mot_de_passe": "secret123"})[0], 401)
        self.assertEqual(self.api("get", "/api/plateforme/etablissements", jeton=admin)[0], 401)
        self.assertEqual(self.api("get", "/api/alertes", jeton=self.proprio)[0], 401)

    def test_liste_suspension_reinitialisation(self):
        admin, adresse = self.etablissement_actif("Clinique A", "alice")
        r = self.api("get", "/api/plateforme/etablissements", jeton=self.proprio)[1]
        e = r["etablissements"][0]
        self.assertEqual((e["slug"], e["activation"], e["type"]["nom"], e["chambres"]), ("clinique-a", "active", "Hôpital", 0))
        self.api("post", f"/api/plateforme/etablissements/{e['id']}", {"actif": False}, self.proprio)
        self.assertEqual(self.api("get", "/api/auth/moi", jeton=admin)[0], 401)
        self.assertEqual(self.api("get", f"/api/etablissements/{adresse}")[0], 404)
        self.api("post", f"/api/plateforme/etablissements/{e['id']}", {"actif": True, "type": self.type_id("Restaurant")}, self.proprio)
        self.assertEqual(self.api("get", "/api/auth/moi", jeton=admin)[1]["etablissement"]["vocabulaire"]["lieu"], "Table")
        moi = self.api("get", "/api/auth/moi", jeton=admin)[1]
        r = self.api("post", f"/api/plateforme/etablissements/{e['id']}/comptes/{moi['id']}/reinitialiser", jeton=self.proprio)[1]
        c = self.api("post", "/api/auth/connexion", {"identifiant": "alice", "mot_de_passe": r["mot_de_passe"]})[1]
        self.assertTrue(c["compte"]["doit_changer"])

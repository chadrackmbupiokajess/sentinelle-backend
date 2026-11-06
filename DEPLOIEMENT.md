# Déploiement de Sentinelle (Django)

| Hébergement | Point d'entrée | Temps réel | Frontend (Vercel) |
|---|---|---|---|
| **PythonAnywhere** (maintenant) | `sentinelle/wsgi.py` | AJAX (toutes les 3 s) | `NEXT_PUBLIC_WEBSOCKET=off` |
| **VPS Hostinger** (plus tard) | `sentinelle/asgi.py` (uvicorn) | WebSocket, AJAX en secours | variable retirée |

---

## 1. Backend sur PythonAnywhere

Remplacez partout `VOTRE_NOM` par votre nom d'utilisateur PythonAnywhere.

### Première installation

1. Envoyez ce dossier sur GitHub (nouveau dépôt, par ex. `Sentinelle-la-colombe-django`).
2. Ouvrez une console **Bash** sur PythonAnywhere :

   ```bash
   git clone https://github.com/VOTRE_COMPTE/Sentinelle-la-colombe-django.git
   mkvirtualenv sentinelle --python=python3.12
   cd ~/Sentinelle-la-colombe-django
   pip install -r requirements.txt
   python manage.py migrate
   python manage.py collectstatic --noinput
   python manage.py check
   ```

   La dernière commande doit afficher `System check identified no issues`.

3. Votre compte de **propriétaire de la plateforme**, indispensable pour créer les établissements.
   Il sert sur la page `/plateforme` du site **et** sur l'admin Django du backend
   (`https://VOTRE_NOM.pythonanywhere.com/admin/`) :

   ```bash
   python manage.py createsuperuser
   ```

4. Onglet **Web** → *Add a new web app* → **Manual configuration** → **Python 3.12**.
5. Sur la page de la web app :
   - **Source code** : `/home/VOTRE_NOM/Sentinelle-la-colombe-django`
   - **Virtualenv** : `/home/VOTRE_NOM/.virtualenvs/sentinelle`
   - **Static files** : URL `/static/` → dossier `/home/VOTRE_NOM/Sentinelle-la-colombe-django/static`
   - **Force HTTPS** : activé
   - **WSGI configuration file** : effacez tout et mettez :

     ```python
     import sys
     sys.path.insert(0, "/home/VOTRE_NOM/Sentinelle-la-colombe-django")
     from sentinelle.wsgi import application
     ```

6. **Reload**, puis ouvrez `https://VOTRE_NOM.pythonanywhere.com/api/sante` → `{"ok": true}`.
   Le message « took a long time to reload » est normal sur le compte gratuit.
   En cas d'erreur : *Error log* de l'onglet Web.

### À chaque mise à jour

```bash
cd ~/Sentinelle-la-colombe-django
git pull
workon sentinelle
pip install -r requirements.txt        # si requirements.txt a changé
python manage.py migrate               # si les modèles ont changé
python manage.py collectstatic --noinput
```

Puis onglet **Web** → **Reload**.

### Réglages (fichier `.env`)

Voir `.env.example`. Les plus utiles :

- `SENTINELLE_ORIGINES=https://votre-site.vercel.app` : une fois l'adresse Vercel connue.
- Clés de notifications push : `python generer_cles_push.py`, puis copiez les 2 lignes dans `.env`.
  Sur le compte gratuit, les connexions sortantes sont filtrées : si l'*Error log* montre « Échec push »,
  il faut un compte payant.

---

## 2. Frontend sur Vercel

**Environment Variables** :

- `NEXT_PUBLIC_API_URL` = `https://VOTRE_NOM.pythonanywhere.com`
- `NEXT_PUBLIC_WEBSOCKET` = `off`

Après toute modification de ces variables : *Deployments* → *Redeploy*.

---

## 3. Fonctionnement : établissements, types et adresses

1. **Vous (propriétaire)** : `https://votre-site/plateforme`, avec le compte créé par `createsuperuser`.
   - Onglet **Types** : Hôpital, Centre médical, Restaurant sont fournis. Créez-en d'autres (Hôtel…) et
     réglez leur vocabulaire (Chambre/Table, Lit/Place, Patient/Client, Infirmier/Serveur…). Toutes les pages
     de l'établissement l'utilisent.
   - **Nouvel établissement** : nom + type. Vous obtenez son **adresse** (ex. `/jessnahopital`) et un
     **code d'activation** (ex. `K7QM-2XPA`), valable **une seule fois** pendant **7 jours**
     (bouton « Nouveau code » s'il expire).
2. **Le responsable de l'établissement** ouvre `https://votre-site/admin`, colle le code :
   - code faux, déjà utilisé ou expiré → « Code invalide » ;
   - code valable → la page de son établissement s'ouvre ; il crée son compte administrateur et arrive dans
     **`/jessnahopital/admin`**. L'établissement démarre **vide** : il crée lui-même ses chambres et lits
     (ou tables et places…), puis imprime leurs affiches QR (liens `/jessnahopital/c/…`).
3. **Le personnel** ouvre l'adresse de l'établissement (`https://votre-site/jessnahopital`), clique sur
   « Demander un compte » ; l'administrateur valide dans `/jessnahopital/admin` (onglet Comptes).

Chaque établissement garde toutes ses pages sous son adresse : `/jessnahopital` (connexion, tableau de bord),
`/jessnahopital/admin` (administration), `/jessnahopital/c/…` (bornes). Un compte d'un autre établissement
n'y a pas accès.
4. Chaque établissement a ses propres comptes, lieux, appels et messages. Suspendre un établissement dans
   `/plateforme` déconnecte tout son personnel et arrête ses bornes.

Dépannage en ligne de commande : `python manage.py sentinelle lister`, `creer-etablissement "Nom" "Type"`,
`creer-admin <identifiant> "<Nom>" <adresse>`, `reinitialiser <identifiant> [adresse]`.

---

## 4. Plus tard : VPS Hostinger

1. Sur le VPS : `docker build -t sentinelle .` puis
   `docker run -d -p 8000:8000 -v sentinelle-donnees:/donnees --env-file .env sentinelle`
   (ou sans Docker : `pip install -r requirements-vps.txt` puis
   `uvicorn sentinelle.asgi:application --host 0.0.0.0 --port 8000 --proxy-headers`).
2. Dans `.env` : `SENTINELLE_WEBSOCKET=1`.
3. Caddy devant pour le HTTPS. Copiez `sentinelle.db` depuis PythonAnywhere (à côté de `manage.py`).
4. Sur Vercel : `NEXT_PUBLIC_API_URL` vers le VPS, **supprimez** `NEXT_PUBLIC_WEBSOCKET`, redéployez.

Les écrans repassent aux WebSockets ; si un réseau les bloque, l'AJAX prend le relais en 8 secondes.
Pour plusieurs processus uvicorn, ajoutez Redis (`SENTINELLE_REDIS_URL`, paquet `channels-redis`).

# ZX980 Server

Serveur FreeBSD pour le traceur ZX980 (module Quectel EC800M) : reçoit les trames GPS envoyées par `ZX980/usr/gps_post.py` et les affiche sur un tableau de bord web.

Python 3 standard uniquement (pas de uvicorn, fastapi ni nginx).

## Fonctionnement

| Port | Rôle | Exposition |
|------|------|------------|
| 8080 | `POST /message` `{"texte": "..."}` envoyé par le traceur | redirigé sur la box (internet) |
| 8090 | tableau de bord web + API JSON, protégé par identifiant / mot de passe | réseau local uniquement |

Deux formats de ligne sont reconnus :

- courte (toutes les 10 s) : `date,lon,lat,alt,vitesse,`
- complète (toutes les 60 s) : `date,batterie,fix,lon,lat,alt,vitesse,ip,opérateur,réseau,téléphone,`

Le champ batterie vaut par exemple `100%(4165mV)`. Seul le pourcentage est stocké dans la colonne `battery`. La tension reste disponible dans la ligne brute (`raw`).

Les messages sont stockés dans SQLite (`/var/db/zx980/zx980.sqlite`). Les lignes non reconnues sont gardées en brut.

Tableau de bord (`http://<ip>:8090/`, rafraîchi toutes les 5 s) : état en ligne / hors ligne, carte OpenStreetMap avec la trace (1 h à 7 j, ou champs « du / au » + Afficher), distance et vitesse max du trajet, position, batterie, fix, réseau, opérateur, IP, téléphone, messages de la période, export CSV de la période (`/api/export.csv`).

Données brutes : `/api/state?hours=24` ou `/api/state?from=2026-10-16T08:00&to=2026-10-16T18:00` (heure du serveur, date de réception ; la page convertit vers et depuis l'heure du navigateur, utile si le FreeBSD est en UTC). La distance ne compte que les segments à 2 km/h ou plus, pour ignorer le bruit GPS à l'arrêt.

## Installation (FreeBSD, en root)

```sh
pkg install python312 py312-sqlite3
install -m 555 zx980_server.py /usr/local/libexec/zx980_server.py
install -m 555 zx980.rc /usr/local/etc/rc.d/zx980
sysrc zx980_enable=YES
python3.12 /usr/local/libexec/zx980_server.py --auth-file /usr/local/etc/zx980.users --add-user admin
service zx980 start
tail -f /var/log/zx980.log
```

## Accès au tableau de bord

Le tableau de bord (page, `/api/state`, export CSV) demande un identifiant et un mot de passe (authentification HTTP Basic). Le port 8080 du traceur n'est pas concerné.

- Les accès sont dans `/usr/local/etc/zx980.users`, une ligne `nom:hash` par utilisateur (PBKDF2-SHA256, jamais le mot de passe en clair).
- Ajouter un accès ou changer un mot de passe (8 caractères minimum) :
  ```sh
  python3.12 /usr/local/libexec/zx980_server.py --auth-file /usr/local/etc/zx980.users --add-user NOM
  ```
  Le serveur relit le fichier à chaque changement, pas besoin de redémarrer. Pour le **premier** accès, faire `service zx980 restart` : le démarrage donne le fichier au groupe `www` en lecture (640).
- Retirer un accès : supprimer sa ligne du fichier.
- Si le fichier est absent, le tableau de bord est verrouillé (tout est refusé).
- `sysrc zx980_authfile=""` désactive le mot de passe.

Le mot de passe passe en clair (base64) sur le réseau local, car le serveur est en HTTP. Ne pas ouvrir le port 8090 sur internet.

## Configuration (`sysrc`)

| Variable | Défaut |
|----------|--------|
| `zx980_runas` | `www` |
| `zx980_python` | `/usr/local/bin/python3.12` |
| `zx980_script` | `/usr/local/libexec/zx980_server.py` |
| `zx980_dbdir` | `/var/db/zx980` |
| `zx980_logfile` | `/var/log/zx980.log` |
| `zx980_authfile` | `/usr/local/etc/zx980.users` (`""` = sans mot de passe) |
| `zx980_args` | (vide), ex. `--api-port 8080 --web-port 8090 -v` |

Ne pas utiliser `zx980_user` : rc.subr lancerait alors `daemon(8)` lui-même sous cet utilisateur, qui ne peut plus écrire ses fichiers pid.

Le service est supervisé par `daemon(8)` et redémarre automatiquement après 5 s en cas d'arrêt.

## Lancement manuel (test)

```sh
python3.12 zx980_server.py --db ./zx980.sqlite --api-port 18080 --web-port 18090 -v
```

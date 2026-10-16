# ZX980 Server

Serveur FreeBSD pour le traceur ZX980 (module Quectel EC800M) : reçoit les trames GPS envoyées par `helpers/gps_post.py` et les affiche sur un tableau de bord web.

Python 3 standard uniquement (pas de uvicorn, fastapi ni nginx).

## Fonctionnement

| Port | Rôle | Exposition |
|------|------|------------|
| 8080 | `POST /message` `{"texte": "..."}` envoyé par le traceur | redirigé sur la box (internet) |
| 8090 | tableau de bord web + API JSON | réseau local uniquement |

Deux formats de ligne sont reconnus :

- courte (toutes les 10 s) : `date,lon,lat,alt,vitesse,`
- complète (toutes les 60 s) : `date,batterie,fix,lon,lat,alt,vitesse,ip,opérateur,réseau,téléphone,`

Les messages sont stockés dans SQLite (`/var/db/zx980/zx980.sqlite`). Les lignes non reconnues sont gardées en brut.

Tableau de bord (`http://<ip>:8090/`, rafraîchi toutes les 5 s) : état en ligne / hors ligne, carte OpenStreetMap avec la trace (1 h à 7 j, ou « Dates… » pour choisir du / au), distance et vitesse max du trajet, position, batterie, fix, réseau, opérateur, IP, téléphone, messages de la période, export CSV de la période (`/api/export.csv`).

Données brutes : `/api/state?hours=24` ou `/api/state?from=2026-10-16T08:00&to=2026-10-16T18:00` (heure locale du serveur, date de réception). La distance ne compte que les segments à 2 km/h ou plus, pour ignorer le bruit GPS à l'arrêt.

## Installation (FreeBSD, en root)

```sh
pkg install python312 py312-sqlite3
install -m 555 zx980_server.py /usr/local/libexec/zx980_server.py
install -m 555 zx980.rc /usr/local/etc/rc.d/zx980
sysrc zx980_enable=YES
service zx980 start
tail -f /var/log/zx980.log
```

## Configuration (`sysrc`)

| Variable | Défaut |
|----------|--------|
| `zx980_runas` | `www` |
| `zx980_python` | `/usr/local/bin/python3.12` |
| `zx980_script` | `/usr/local/libexec/zx980_server.py` |
| `zx980_dbdir` | `/var/db/zx980` |
| `zx980_logfile` | `/var/log/zx980.log` |
| `zx980_args` | (vide), ex. `--api-port 8080 --web-port 8090 -v` |

Ne pas utiliser `zx980_user` : rc.subr lancerait alors `daemon(8)` lui-même sous cet utilisateur, qui ne peut plus écrire ses fichiers pid.

Le service est supervisé par `daemon(8)` et redémarre automatiquement après 5 s en cas d'arrêt.

## Lancement manuel (test)

```sh
python3.12 zx980_server.py --db ./zx980.sqlite --api-port 18080 --web-port 18090 -v
```

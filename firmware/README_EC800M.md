# ZX908 avec module Quectel EC800M

Adaptation du projet [FoxExe/ZX908](https://github.com/FoxExe/ZX908) (écrit pour l'EC800N-CN LA) à un boîtier ZX908 équipé d'un **Quectel EC800M-CNLE**.

## Matériel testé

| Élément | Valeur |
|---|---|
| Module | EC800M-CNLE |
| Firmware | `QPY_OCPU_V0005_EC800M_CNLE_FW` (QuecPython V0005, MicroPython 1.13) |
| GPS | AT6558R (CASIC), UART2 9600 bauds |
| Opérateur | Free Mobile, APN `free` |

## Brochage : identique à l'EC800N

D'après la doc officielle QuecPython ([machine.Pin](https://developer.quectel.com/doc/quecpython/API_reference/en/peripherals/machine.Pin.html), [machine.UART](https://developer.quectel.com/doc/quecpython/API_reference/en/peripherals/machine.UART.html), [machine.I2C](https://developer.quectel.com/doc/quecpython/API_reference/en/peripherals/machine.I2C.html)), GPIO1 à 33, 36 et 37 sont sur les mêmes broches physiques pour l'EC800N et l'EC800M. **Aucune constante de broche n'a eu besoin d'être changée.**

| Fonction | Constante | Broche |
|---|---|---|
| Alimentation GPS | `Pin.GPIO10` | 54 |
| LED rouge | `Pin.GPIO15` | 80 |
| LED bleue | `Pin.GPIO16` | 81 |
| LED jaune | `Pin.GPIO17` | 76 |
| GPS NMEA | `UART.UART2` | TX 18 / RX 17 |
| I2C0 | `I2C.I2C0` | SCL 67 / SDA 66 |

L'EC800M a en plus GPIO34 à 47, I2C1/I2C2 et UART4 (broches 28/29), non utilisés ici.

**Seul le firmware change** : ne pas flasher les `.bin` EC800N du dossier `Firmware/` sur un EC800M.

## Bilan des tests sur le module

| Fonction | État |
|---|---|
| LEDs, GPS AT6558 | ✅ |
| Scan Wi-Fi (`wifiScan`) | ✅ |
| Data 4G Free, ping google.com (~35 ms) | ✅ |
| POST HTTP vers un serveur | ✅ |
| SMS | ❌ module `sms` absent du firmware V0005 CNLE, et commandes AT SMS refusées (`AT+CMGF=1` → `ERROR`) |

V0005 est le dernier firmware public pour l'EC800M-CNLE. Pour les SMS, il faut demander une version avec `sms` au support Quectel ([forum](https://forums.quectel.com/t/missing-the-sms-module-in-ec800mcnler06a05m08/59369)).

## Modifications du tracker (`usr/`)

- **`usr/main.py`** : commentaires sur la correspondance GPIO ↔ broche, valable pour l'EC800N et l'EC800M. Aucune valeur modifiée.
- **`usr/sms_handler.py`** : `import sms` est maintenant optionnel. Sans module `sms`, le tracker démarre quand même et affiche `SMS commands disabled` au lieu de planter.
- **`usr/tracker_config.json`** : configuration avec l'APN `free`, à utiliser tant que les SMS ne marchent pas. Le serveur (`votre.serveur.com`) reste à renseigner.

## Nouveaux scripts (`helpers/`)

Chacun se copie dans `/usr` avec QPYcom et se lance depuis le REPL avec `exec(open('/usr/<script>.py').read())`.

| Script | Rôle |
|---|---|
| `check_ec800m.py` | Affiche modèle et firmware, vérifie les modules QuecPython utilisés, allume les 3 LEDs, teste la réception NMEA du GPS |
| `scan_wifi_gps.py` | Scan Wi-Fi (MAC + RSSI), puis affiche les trames NMEA en continu |
| `sms_ping.py` | Règle l'APN `free`, tente un SMS (module `sms`, puis AT), puis fait un ping de google.com (`uping`, ou connexion TCP si absent). Mettre le numéro dans `PHONE` |
| `post_message.py` | POST JSON `{"auteur": ..., "texte": ...}` vers `http://unix.server.bsd:8080/message`. Fournit `post_json()` |
| `gps_post.py` | Envoie périodiquement l'heure GPS et la position. LED jaune : position GPS acquise. LED rouge : message envoyé |
| `main.py` | Lancement automatique de `gps_post.py` au démarrage |

### Envoi automatique de la position

Copier dans `/usr` : **`main.py`, `gps_post.py`, `post_message.py`**, puis redémarrer le module. Ce `main.py` remplace celui du tracker.

Toutes les 60 s (`SEND_INTERVAL_MS`), si le GPS a une position, le serveur reçoit :

```json
{"auteur": "zx908", "texte": "2026-10-10 10:34:16 UTC 49°08'48.0\"N 0°13'36.0\"E"}
```

- L'heure est en UTC et vient du GPS. La position est en degrés, minutes, secondes, latitude puis longitude.
- Sans position GPS, rien n'est envoyé.
- La LED jaune clignote rapidement (4 Hz, `FIX_BLINK_MS`) quand le GPS a une position, et s'éteint s'il n'y a pas de trame RMC valide depuis 5 s.
- La LED rouge clignote deux fois rapidement à chaque message accepté par le serveur (réponse HTTP 2xx).
- `main.py` lance le script dans un thread, donc le REPL reste accessible. Le script redémarre 10 s après un plantage, et attend le réseau sans limite de temps au démarrage.
- Consommation : environ 0,5 Mo par jour.

Pour désactiver le lancement au démarrage, supprimer ou renommer `/usr/main.py`.

## Particularités de QuecPython V0005 rencontrées

- **MicroPython 1.13** : `bytes.partition()` n'existe pas, il faut utiliser `find()`.
- **`recv()` après fermeture par le serveur** : il lève `OSError` 107 (`ENOTCONN`) au lieu de renvoyer `b''`. `post_message.py` le traite comme une fin de réponse normale.
- **`settimeout()` avant `connect()`** : un échec de connexion n'apparaît qu'ensuite, sous forme de `ENOTCONN` sur `send()`. `connect()` est donc fait en mode bloquant.
- **`Content-Length`** : il doit compter les octets UTF-8 (`°` = 2 octets), pas les caractères.
- **`dataCall.activate()`** : il renvoie un échec si la connexion data est déjà active, ce qui est sans conséquence.
- **Pas de SSH** : aucune implémentation dans QuecPython. De toute façon, le module a une IP privée derrière le CGNAT de Free (10.x) et ne peut pas recevoir de connexion entrante.
- **TLS** : `ussl` et `umqtt` gèrent TLS 1.2 et un certificat client (mTLS). La doc ne prévoit pas de certificat CA pour vérifier le serveur.

# ZX980 — traceur GPS ZX908 / Quectel EC800M

Firmware QuecPython pour le boîtier traceur **ZX908** équipé d'un module 4G **Quectel EC800M-CNLE**. Au démarrage, le traceur lit sa position GPS et l'envoie en HTTP à un serveur. Ce serveur stocke les positions et les affiche sur une carte : voir [`ZX980_Server`](../ZX980_Server).

Ce projet est adapté de [FoxExe/ZX908](https://github.com/FoxExe/ZX908), écrit à l'origine pour l'EC800N.

![ZX908 avec sa batterie, face GPS](Images/WhatsApp2.jpeg)

## Fonctionnement

1. Au démarrage, `main.py` lance `gps_post.py` dans un thread : le REPL de QPYcom reste utilisable.
2. Le script attend la connexion 4G (APN Free `free`), allume le GPS et lit les trames NMEA.
3. Si le GPS a une position, il envoie au serveur :
   - **toutes les 10 s**, une ligne courte : la position ;
   - **toutes les 60 s**, une ligne complète : la position, plus l'état de l'appareil et du réseau.
4. Les LEDs indiquent l'état : 🟡 jaune pour le GPS, 🔴 rouge pour chaque envoi.
5. Si le script plante, il redémarre au bout de 10 s.

### Messages envoyés

Requête `POST http://<server_host>:<server_port><server_path>` (réglés dans [`usr/config.json`](usr/config.json)), avec le corps JSON `{"texte": "<ligne CSV>"}`.

**Ligne courte (toutes les 10 s), 5 champs :**

```
datefrance,posX,posY,altitude,vitesse,
16/10/2026 19:40:39,0°13'36.0"E,49°08'48.0"N,45.2m,0.0km/h,
```

**Ligne complète (toutes les 60 s, à la place de la ligne courte), 11 champs :**

```
datefrance,niveaubat,fix2Dou3D,posX,posY,altitude,vitesse,IP,operator,signal,telephone,
16/10/2026 19:40:39,100%(4165mV),3D,0°13'36.0"E,49°08'48.0"N,45.2m,0.0km/h,10.126.248.157,Free,4G -78dBm,+33600000000,
```

| Champ | Source | Détail |
|---|---|---|
| `datefrance` | GPS (RMC) | Heure UTC convertie en heure de Paris, avec le changement d'heure été/hiver |
| `niveaubat` | `Power.getVbatt()` | Pourcentage calculé à partir de la tension, suivi de la tension mesurée : `100%(4165mV)`. Moyenne de 5 mesures. Environ 100 % quand l'USB est branché |
| `fix2Dou3D` | GPS (GSA) | `2D`, `3D`, ou `?` si inconnu |
| `posX` / `posY` | GPS (RMC) | Longitude puis latitude, en degrés, minutes et secondes |
| `altitude` | GPS (GGA) | En mètres |
| `vitesse` | GPS (RMC) | En km/h |
| `IP` | `dataCall.getInfo()` | Adresse IP mobile (privée, derrière le CGNAT de Free) |
| `operator` | `net.operatorName()` | Nom de l'opérateur |
| `signal` | `net.getNetMode()`, `net.getSignal()` | Technologie (`4G`/`3G`/`2G`) et RSSI en dBm |
| `telephone` | `sim.getPhoneNumber()` | Numéro enregistré sur la carte SIM |

Rien n'est envoyé tant que le GPS n'a pas de position. Consommation : environ 10 Mo par jour, soit 300 à 350 Mo par mois en continu. Chaque message coûte environ 1,1 Ko, car il inclut une recherche DNS et une connexion TCP complète, alors que la ligne CSV ne fait qu'environ 75 octets.

### LEDs

| LED | GPIO | Comportement |
|---|---|---|
| 🟡 Jaune (orange sur la carte) | GPIO17 | Clignote vite (4 Hz) quand le GPS a une position, éteinte sinon |
| 🔴 Rouge | GPIO15 | 3 flashs rapides à chaque message accepté par le serveur (HTTP 2xx) |
| 🔵 Bleue | GPIO16 | Non utilisée par le code |

## Contenu du dossier `usr/`

Tout le dossier se copie dans `/usr` sur le module.

### Programme principal

| Fichier | Rôle |
|---|---|
| `main.py` | Lancé automatiquement au démarrage : exécute `gps_post.run()` dans un thread et le relance en cas de plantage |
| `gps_post.py` | Lecture du GPS, construction des lignes CSV, envoi périodique, gestion des LEDs |
| `post_message.py` | Fonction `post_json()` : requête POST JSON en HTTP, adaptée aux particularités de QuecPython. Fonction `load_config()` : lecture de `config.json` |
| `config.json` | Adresse du serveur API (voir ci-dessous) |

### Adresse du serveur : `config.json`

```json
{
	"server_host": "unix.server.bsd",
	"server_port": 8080,
	"server_path": "/message"
}
```

`main.py`, `gps_post.py` et `post_message.py` lisent ce fichier. Pour changer de serveur, modifiez `/usr/config.json` sur le module et redémarrez : le code ne change pas. Si le fichier est absent ou invalide, le script affiche un message et utilise les valeurs ci-dessus. Au démarrage, `main.py` affiche l'adresse utilisée (`Server: ...`).

### Autres réglages (en haut de `gps_post.py`)

| Constante | Défaut | Rôle |
|---|---|---|
| `GPS_INTERVAL_MS` | `10000` | Intervalle de la ligne courte |
| `FULL_INTERVAL_MS` | `60000` | Intervalle de la ligne complète |
| `SENT_BLINKS` | `3` | Nombre de flashs rouges par envoi |
| `FIX_BLINK_MS` | `125` | Vitesse de clignotement de la LED jaune |
| `FIX_TIMEOUT_MS` | `5000` | Délai sans trame RMC valide au-delà duquel la position est considérée comme perdue |
| `LOW_BATTERY_MV` | `3500` | Seuil de coupure batterie faible (mV) |
| `LOW_BATTERY_CHECKS` | `3` | Nombre de mesures basses consécutives avant la coupure |
| `BATTERY_CHECK_MS` | `30000` | Intervalle entre deux vérifications de la batterie |

### Scripts de test et de diagnostic

Chaque script se lance depuis le REPL de QPYcom avec :

```python
exec(open('/usr/<script>.py').read())
```

Pendant les tests GPS, renommez `main.py`, sinon `gps_post` utilise le GPS en même temps.

| Script | Rôle |
|---|---|
| `check_ec800m.py` | Vérifie le modèle, le firmware et les modules QuecPython, allume les 3 LEDs et teste la réception NMEA |
| `device_info.py` | Affiche tout ce que le module sait remonter : module, batterie, SIM, réseau, cellules, Wi-Fi, satellites |
| `scan_wifi_gps.py` | Scan Wi-Fi (MAC + RSSI), puis affichage des trames NMEA en continu |
| `sms_ping.py` | Règle l'APN `free`, tente un SMS, puis fait un ping de google.com (numéro à mettre dans `PHONE`) |
| `test_GNSS.py`, `test_UART.py`, `test_gpio.py` | Tests du GPS, des UART et des GPIO (scripts d'origine FoxExe) |
| `i2c_scanner.py`, `i2c_test.py` | Scan et lecture du bus I2C0 (composants inconnus aux adresses 0x62 et 0xE2) |
| `inspect_all.py`, `test_fullinfo.py` | Inventaire des modules et des informations du firmware (scripts d'origine FoxExe) |

## Installation

1. **Firmware :** flasher [`Firmware/QPY_OCPU_V0005_EC800M_CNLE_FW.bin`](Firmware/QPY_OCPU_V0005_EC800M_CNLE_FW.bin) avec [QPYcom](https://python.quectel.com/en/wp-content/uploads/sites/2/2024/11/QPYcom_V3.6.0.zip). Installer d'abord les [pilotes USB Quectel](https://python.quectel.com/wp-content/uploads/2024/09/Quectel_Windows_USB_DriverA_Customer_V1.1.13.zip).
2. **Code :** régler l'adresse du serveur dans `usr/config.json`, puis copier tout le contenu de `usr/` (avec `config.json`) dans `/usr` sur le module, dans l'onglet Fichier de QPYcom.
3. **Vérification :** lancer `check_ec800m.py` depuis le REPL.
4. **Démarrage :** redémarrer le module. L'envoi commence dès que la 4G est connectée et que le GPS a une position.

Pour désactiver le lancement automatique, supprimez ou renommez `/usr/main.py`.

`Firmware/Tracker_fw_alpha.bin` est le firmware complet d'origine de FoxExe pour l'**EC800N**. Ne le flashez pas sur un EC800M.

## Matériel

| Élément | Valeur |
|---|---|
| Module | Quectel EC800M-CNLE (LTE Cat 1) |
| Firmware | `QPY_OCPU_V0005_EC800M_CNLE_FW` : QuecPython V0005, MicroPython 1.13, environ 950 Ko de RAM libre, environ 540 Ko libres dans `/usr` |
| GPS | AT6558R (CASIC), GPS + BeiDou, UART2 à 9600 bauds ([fiche technique](docs/at6558.pdf)) |
| Antenne GPS | Céramique passive. La puce affiche toujours `ANTENNA OPEN`, ce qui est normal. |
| Batterie | LiPo 1S avec connecteur JST |
| Opérateur testé | Free Mobile, APN `free` |

Le bouton d'alimentation est relié à un circuit dédié : un appui court allume, un appui long (plus de 10 s) éteint. Le bouton reset coupe l'alimentation.

### Brochage

Le brochage de l'EC800M est **identique à celui de l'EC800N** pour toutes les broches utilisées. Seul le firmware change.

| GPIO | Broche | Fonctions | Usage sur le ZX908 |
|---|---|---|---|
| GPIO1 à 4 | 30 à 33 | PCM / SPI0 | Port latéral n° 3 à 6 |
| GPIO10 | 54 | CAM_MCLK | Alimentation du GPS |
| GPIO15 | 80 | CAM_SPI_CLK | LED rouge |
| GPIO16 | 81 | CAM_PWDN | LED bleue |
| GPIO17 | 76 | KP_MKOUT_2 | LED jaune (orange) |
| GPIO23 / 24 | 66 / 67 | I2C0 SDA / SCL | Composants inconnus à 0x62 et 0xE2 |
| GPIO25 / 26 | 17 / 18 | UART2 RX / TX | TX / RX du GPS |
| GPIO32 / 33 | 28 / 29 | UART1 RX / TX | UART auxiliaire |
| GPIO34 / 35 | 38 / 39 | UART0 RX / TX | UART de débogage |
| GPIO36 | 16 | NET_STATUS / PWM2 | — |

Sources : documentation QuecPython [machine.Pin](https://developer.quectel.com/doc/quecpython/API_reference/en/peripherals/machine.Pin.html), [machine.UART](https://developer.quectel.com/doc/quecpython/API_reference/en/peripherals/machine.UART.html) et [machine.I2C](https://developer.quectel.com/doc/quecpython/API_reference/en/peripherals/machine.I2C.html).

## Photos

| | |
|---|---|
| ![ZX908 avec sa batterie, face arrière](Images/WhatsApp1.jpeg) | ![ZX908 avec sa batterie, face GPS](Images/WhatsApp2.jpeg) |
| Face arrière (bouton, LED bleue) et batterie LiPo | Face avant : antenne GPS céramique, support SIM, LEDs |

![Brochage de la carte ZX908](Images/ZX908.jpg)

*Brochage de la carte : USB (GND, DP, DM, +5 V), port latéral (GND, PWR_BTN, GPIO1 à 4), batterie, haut-parleur et LEDs. Photo FoxExe, avec la version EC800N du module.*

| | |
|---|---|
| ![Puce GPS AT6558](Images/chips_gps.jpg) | ![Puces près du connecteur USB](Images/chips_audio.jpg) |
| Puce GPS AT6558R | Puce GPS et puce audio près du connecteur USB |

![Amplificateur audio 8987S](Images/Audio_codec.jpg)

*Puce audio marquée 8987S (amplificateur pour le haut-parleur).*

## Limites connues

- **Pas de SMS :** le firmware V0005 CNLE n'a pas de module `sms`, et les commandes AT SMS renvoient `ERROR`. C'est le dernier firmware public. Il faut demander au support Quectel une version qui contient `sms` ([fil du forum](https://forums.quectel.com/t/missing-the-sms-module-in-ec800mcnler06a05m08/59369)).
- **Pas d'appels :** pas de module `voiceCall`.
- **Pas d'accès distant direct (SSH…) :** le module a une IP privée derrière le CGNAT de Free, donc c'est toujours lui qui se connecte au serveur.
- **HTTP en clair, sans authentification :** la position et le numéro de téléphone circulent en clair. QuecPython gère HTTPS/TLS 1.2 (`ussl`) si besoin.
- **Batterie :** le pourcentage n'est fiable que sur batterie seule. Le chargeur USB fait remonter la tension.

### Coupure batterie faible

Un thread vérifie la batterie toutes les 30 s (`BATTERY_CHECK_MS`). Si la tension reste sous **3 500 mV** (`LOW_BATTERY_MV`) pendant **3 mesures de suite** (`LOW_BATTERY_CHECKS`, soit environ 1 min 30), le module éteint le GPS puis s'éteint lui-même (`Power.powerDown()`), pour protéger la batterie LiPo d'une décharge profonde.

- La vérification ne s'applique pas quand l'USB est branché (la batterie est en charge).
- Chaque mesure est une moyenne de 5 lectures, et il en faut 3 basses à la suite : une chute brève pendant une émission 4G ne suffit pas à éteindre le module.
- Pour le rallumer : bouton d'alimentation, ou branchement du chargeur selon la carte.

## Particularités de QuecPython V0005

- MicroPython 1.13 n'a pas `bytes.partition()` : utiliser `find()`.
- `recv()` lève `OSError` 107 (`ENOTCONN`) au lieu de renvoyer `b''` quand le serveur ferme la connexion.
- Un `settimeout()` avant `connect()` masque les échecs de connexion, qui n'apparaissent qu'ensuite sous forme de `ENOTCONN` sur `send()`.
- `Content-Length` doit compter les octets UTF-8 : `°` fait 2 octets.
- `net.getServingMcc()` et `getServingMnc()` renvoient du BCD lu en hexadécimal (`520` = `0x208` = France).
- `dataCall.activate()` renvoie un échec si la connexion data est déjà active, sans conséquence.

## Liens

- [Documentation de l'API QuecPython](https://developer.quectel.com/doc/quecpython/API_reference/en/)
- [Téléchargements QuecPython (firmwares, outils)](https://developer.quectel.com/en/resource-download?cid=4)
- [QuecPython sur GitHub](https://github.com/QuecPython)
- [Projet d'origine FoxExe/ZX908](https://github.com/FoxExe/ZX908)

## Licence

GPL-3.0, comme le projet d'origine. Voir [LICENSE](LICENSE).

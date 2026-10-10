# ZX908

> Version EC800M (Free Mobile, envoi de la position vers un serveur) : voir [README_EC800M.md](README_EC800M.md).

# ATTENTION : projet en cours de développement !

### Ce code contient encore de nombreuses erreurs et n'est pas finalisé !

## Fonctionnalités

### Fonctionnalités principales

-   ✅ Suivi GPS avec le module AT6558
-   ✅ Localisation par Wi-Fi en secours
-   ✅ Mise en veille automatique en cas d'inactivité
-   ✅ Surveillance de la batterie avec calcul précis du pourcentage
-   ✅ Configuration par commandes SMS
-   ✅ Prise en charge des appels vocaux
-   ✅ Voyants LED d'état
-   ✅ Mise en mémoire tampon des données pendant les coupures de connexion

## Téléchargements

-   [Version alpha (en cours)](Firmware/Tracker_fw_alpha.bin)

# Protocole HTTP

Dans ce mode, le tracker envoie au serveur une requête POST au format JSON :

```json
{
	"imei": "123456789012345",
	"timestamp": 1234567890,
	"latitude": 55.751244,
	"longitude": 37.618423,
	"altitude": 156.0,
	"speed": 45.5,
	"course": 180.0,
	"satellites": 12,
	"battery": 85,
	"charging": false,
	"source": "gps",
	"accuracy": 0
}
```

### Commandes SMS

| SMS (exemple)                                    | Explication                                                 |
| ------------------------------------------------ | ----------------------------------------------------------- |
| `APN,internet.mts.ru,mts,mts`                    | Configure l'accès internet mobile (APN)                     |
| `SERVER,GT06,tracker.example.com:5023`           | Configure un serveur GT06                                   |
| `SERVER,HTTP,https://example.com:8080/api/track` | Configure un serveur HTTP                                   |
| `WIFISERVER,location.example.com:80,/api/locate` | Configure le serveur de localisation Wi-Fi (facultatif)     |
| `WIFIENABLE,1`                                   | Active ou désactive la localisation Wi-Fi                   |
| `ADDNUMBER,+1234567890`                          | Ajoute un numéro autorisé à modifier la configuration       |
| `DELNUMBER,+1234567890`                          | Supprime un numéro autorisé                                 |
| `INTERVAL,10`                                    | Règle l'intervalle d'envoi des données (1 à 600 secondes)   |
| `SLEEP,30`                                       | Règle le délai d'inactivité avant mise en veille (minutes)  |
| `STATUS`                                         | Demande l'état actuel                                       |
| `POWEROFF`                                       | Éteint l'appareil                                           |
| `RESET,123456789012345`                          | Réinitialise les réglages (l'IMEI sert de mot de passe)     |

### Remarques

-   Le premier numéro qui envoie un SMS devient le numéro principal
-   Si aucun numéro autorisé n'est configuré, tous les numéros sont acceptés
-   Si aucun numéro d'appel n'est configuré, aucun appel n'est accepté
-   Les commandes ne sont pas sensibles à la casse
-   Une commande invalide reçoit une réponse d'erreur
-   La commande RESET fonctionne depuis n'importe quel numéro, mais exige l'IMEI
-   La localisation Wi-Fi est facultative et désactivée par défaut
-   Les réseaux Wi-Fi sont envoyés au serveur quand le GPS n'est pas disponible

### Voyants LED

-   **Rouge** : état du GPS
    -   Allumé : position GPS acquise
    -   Éteint : GPS désactivé
    -   Clignotant : recherche des satellites
-   **Bleu** : état du réseau
    -   Éteint : aucun serveur configuré / pas d'internet
    -   Clignotant : connexion au serveur en cours
    -   Pulsation : transmission de données
-   **Jaune** : état de la batterie
    -   Allumé : fonctionnement normal
    -   Clignotement lent : en charge
    -   Clignotement rapide : batterie faible (< 20 %)
    -   Clignotement très lent : mode veille

# Informations techniques

### Matériel

-   **Puce principale** : Quectel EC800N-CN (LA)
-   **Système** : MicroPython v1.13.0 (mPY : 10245, Python v3.4.0, QPY v0006)
-   **RAM MicroPython** : 512 256 octets
-   **ROM MicroPython** : environ 576 Ko dans /usr/ plus environ 100 Ko dans /bak
-   **Puce GPS** : AT6558 ([fiche technique](docs/at6558.pdf))
-   Un microphone est présent, mais le firmware n'a pas de classe voiceCall : impossible de passer ou de recevoir des appels
-   Le bouton d'alimentation est relié à un circuit d'alimentation dédié. Appui court : allumage. Appui long (plus de 10 secondes) : extinction.
-   Le bouton reset coupe simplement l'alimentation de l'appareil.
-   Le bus i2c0 comporte deux périphériques, aux adresses 0x62 et 0xE2. Leur rôle est inconnu : il s'agit peut-être de l'interface i2c du module GPS (la fiche technique n'en dit rien).

### Liens

-   Page officielle du module ([chinois](https://python.quectel.com/modules-cat/ec800n-series))
-   Documentation, firmwares et logiciels officiels ([chinois](https://python.quectel.com/resource-download?pid=146&cid=6))
-   Documentation de l'API QuecPython ([anglais](https://python.quectel.com/doc/API_reference/en/) / [chinois](https://python.quectel.com/doc/API_reference/zh/index.html))
-   [QuecPython sur GitHub](https://github.com/QuecPython)
-   [QPYcom v3.6.0 (Windows)](https://python.quectel.com/en/wp-content/uploads/sites/2/2024/11/QPYcom_V3.6.0.zip)
-   [Pilotes USB (Windows)](https://python.quectel.com/wp-content/uploads/2024/09/Quectel_Windows_USB_DriverA_Customer_V1.1.13.zip)
-   [EC800N_QuecPython_v0004](Firmware/QPY_OCPU_V0004_EC800N_CNLA_FW.bin)
-   [EC800N_QuecPython_v0006](Firmware/QPY_OCPU_V0006_EC800N_CNLA_FW.bin)

### GPIO

| GPIO   | Broche | Fonction 1          | Fonction 2 | Remarques             |
| ------ | ------ | ------------------- | ---------- | --------------------- |
| GPIO1  | 30     | PCM_CLK             | SPI0_CLK   | Port latéral n° 3     |
| GPIO2  | 31     | PCM_SYNC            | SPI0_CS    | Port latéral n° 4     |
| GPIO3  | 32     | PCM_DIN             | SPI0_MOSI  | Port latéral n° 5     |
| GPIO4  | 33     | PCM_DOUT            | SPI0_MISO  | Port latéral n° 6     |
| GPIO5  | 49     | LCD_RST             | PWM3       |                       |
| GPIO6  | 50     | LCD_SPI_DOUT        | SPI1_MOSI  |                       |
| GPIO7  | 51     | LCD_SPI_RS          | SPI1_MISO  |                       |
| GPIO8  | 52     | LCD_SPI_CS          | SPI1_CS    |                       |
| GPIO9  | 53     | LCD_SPI_CLK         | SPI1_CLK   |                       |
| GPIO10 | 54     | CAM_MCLK            |            | Alimentation du GPS   |
| GPIO11 | 55     | CAM_SPI_DATA0       |            |                       |
| GPIO12 | 56     | CAM_SPI_DATA1       |            |                       |
| GPIO13 | 57     | CAM_I2C_SCL         |            |                       |
| GPIO14 | 58     | CAM_I2C_SDA         |            |                       |
| GPIO15 | 80     | CAM_SPI_CLK         |            | LED rouge             |
| GPIO16 | 81     | CAM_PWDN            |            | LED bleue             |
| GPIO17 | 76     | KP_MKOUT_2          |            | LED jaune             |
| GPIO18 | 77     | KP_MKIN_2           | IN2        |                       |
| GPIO19 | 82     | USB_BOOT/KP_MKOUT_4 | OUT4       |                       |
| GPIO20 | 83     | KP_MKIN_4           | IN4        |                       |
| GPIO21 | 86     | KP_MKOUT_1          | OUT1       |                       |
| GPIO22 | 87     | KP_MKIN_1           | IN1        |                       |
| GPIO23 | 66     | I2C0_SDA            |            | Périphérique @ 0x62   |
| GPIO24 | 67     | I2C0_SCL            |            | Périphérique @ 0xE2   |
| GPIO25 | 17     | UART2_RXD           |            | TX du GPS             |
| GPIO26 | 18     | UART2_TXD           |            | RX du GPS             |
| GPIO27 | 19     | UART2_DTR           |            |                       |
| GPIO28 | 20     | UART2_RI            |            |                       |
| GPIO29 | 21     | UART2_DCD           |            |                       |
| GPIO30 | 22     | UART2_CTS           |            |                       |
| GPIO31 | 23     | UART2_RTS           |            |                       |
| GPIO32 | 28     | UART1_RXD           | IN0        | UART auxiliaire       |
| GPIO33 | 29     | UART1_TXD           | OUT0       |                       |
| GPIO34 | 38     | UART0_RXD           |            | UART de débogage      |
| GPIO35 | 39     | UART0_TXD           |            |                       |
| GPIO36 | 16     | NET_STATUS          | PWM2       |                       |
| GPIO37 | 78     | LCD_TE              | PWM1       |                       |
|        | 79     |                     | PWM0       |                       |

### Images

![Carte ZX908](Images/ZX908.jpg)
![AT6558](Images/chips_gps.jpg)

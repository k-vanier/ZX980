# ZX980

GPS tracker built on the ZX908 box with a Quectel EC800M 4G module, with its server and Android app.

| Folder | Role | Stack |
|---|---|---|
| [firmware](firmware) | Runs on the tracker: reads the GPS position and sends it over HTTP to the server | QuecPython |
| [server](server) | Receives the positions, stores them in SQLite and shows them on a web dashboard with a map | Python 3 (standard library), FreeBSD |
| [android](android) | Android app that shows the server dashboard: map, track, status, battery, network | Kotlin, osmdroid |

```
tracker (firmware) --HTTP POST--> server --JSON API--> web dashboard / Android app
```

The QuecPython firmware image (`QPY_OCPU_V0005_EC800M_CNLE_FW.bin`) and the Android app (`ZX980.apk`) are on the [Releases](https://github.com/k-vanier/ZX980/releases/latest) page.

Each folder has its own README with installation and usage details.

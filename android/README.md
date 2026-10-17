# ZX980 Android

Android app that shows the [`ZX980_Server`](../ZX980_Server) dashboard: OpenStreetMap map with the track, online / offline status, position, trip (distance, max speed), battery, network and the messages of the selected period.

It reads the same JSON API as the web page (`/api/state`, port 8090, HTTP Basic authentication) and refreshes every 5 s. The server and the tracker do not change.

Kotlin, [osmdroid](https://github.com/osmdroid/osmdroid) map, Android 8.0 (API 26) minimum.

## Usage

1. Copy `ZX980.apk` to the phone and open it (allow installing apps from unknown sources).
2. On first launch, the Settings screen asks for:
   - the dashboard address, for example `http://192.168.1.10:8090` (`http://` is added if missing);
   - the username and password created with `--add-user`.
3. **Enregistrer et tester** (save and test) checks the connection. The gear icon at the top right reopens the settings.

Features (the app's interface is in French):

| Element | Role |
|---|---|
| Status badge | `en ligne · N s` (online, green under 30 s), `vu il y a N min` (seen N min ago, orange under 5 min), `hors ligne` (offline, red) |
| Period menu | 1 h, 6 h, 24 h, 7 days, or « Période choisie » (custom period) with the from / to buttons |
| Map | Track in blue, start in green, last position in red. Changing the period fits the map to the track |
| Target button | Centres the map on the last position |
| Open in Maps | Opens the position in the phone's maps app |

Server times are converted to the phone's time zone, as on the web page. The GPS time is shown as the tracker sends it (Paris time).

No CSV export in the app: use the web page.

## Access from outside the local network

Port 8090 is only open on the local network, over HTTP. The app therefore works on Wi-Fi at home. To use it over 4G, go through a VPN to the router (WireGuard on the Freebox) rather than opening port 8090 to the internet, because the password would travel in clear text.

The password is stored in the app's private preferences, on the phone.

## Build

Tools installed in `C:\Users\user\AndroidDev`: Temurin JDK 17 (`jdk17`), Android SDK (`sdk`, platform 35). `local.properties` (not versioned) points to the SDK.

```powershell
$env:JAVA_HOME = "C:\Users\user\AndroidDev\jdk17"
.\gradlew.bat assembleDebug
```

The APK is built in `app\build\outputs\apk\debug\app-debug.apk`. To update `ZX980.apk` in the repository:

```powershell
Copy-Item app\build\outputs\apk\debug\app-debug.apk ZX980.apk
```

It is signed with the debug key: it installs directly, but cannot be published on the Play Store.

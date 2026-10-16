# ZX980 Android

Application Android qui affiche le tableau de bord de [`ZX980_Server`](../ZX980_Server) : carte OpenStreetMap avec la trace, état en ligne / hors ligne, position, trajet (distance, vitesse max), batterie, réseau et messages de la période.

Elle lit la même API JSON que la page web (`/api/state`, port 8090, authentification HTTP Basic) et se rafraîchit toutes les 5 s. Le serveur et le traceur ne changent pas.

Kotlin, carte [osmdroid](https://github.com/osmdroid/osmdroid), Android 8.0 (API 26) minimum.

## Utilisation

1. Copier `ZX980.apk` sur le téléphone et l'ouvrir (autoriser l'installation d'applications inconnues).
2. Au premier lancement, l'écran Réglages demande :
   - l'adresse du tableau de bord, par exemple `http://192.168.1.10:8090` (`http://` est ajouté s'il manque) ;
   - l'identifiant et le mot de passe créés avec `--add-user`.
3. **Enregistrer et tester** vérifie la connexion. L'engrenage en haut à droite rouvre les réglages.

Fonctions :

| Élément | Rôle |
|---|---|
| Pastille d'état | `en ligne · N s` (vert sous 30 s), `vu il y a N min` (orange sous 5 min), `hors ligne` (rouge) |
| Menu de période | 1 h, 6 h, 24 h, 7 j, ou « Période choisie » avec les boutons du / au |
| Carte | Trace en bleu, départ en vert, dernière position en rouge. Changer de période recadre la carte sur la trace |
| Bouton cible | Recentre la carte sur la dernière position |
| Ouvrir dans Maps | Ouvre la position dans l'appli de cartes du téléphone |

Les heures du serveur sont converties dans le fuseau du téléphone, comme sur la page web. L'heure GPS est affichée telle que le traceur l'envoie (heure de Paris).

Pas d'export CSV dans l'appli : utiliser la page web.

## Accès hors du réseau local

Le port 8090 n'est ouvert que sur le réseau local, en HTTP. L'appli fonctionne donc en Wi-Fi à la maison. Pour l'utiliser en 4G, passer par un VPN vers la box (WireGuard sur la Freebox) plutôt que d'ouvrir le port 8090 sur internet, car le mot de passe passerait en clair.

Le mot de passe est enregistré dans les préférences privées de l'appli, sur le téléphone.

## Compilation

Outils installés dans `C:\Users\bsdouille\AndroidDev` : JDK Temurin 17 (`jdk17`), SDK Android (`sdk`, plateforme 35). `local.properties` (non versionné) pointe vers le SDK.

```powershell
$env:JAVA_HOME = "C:\Users\bsdouille\AndroidDev\jdk17"
.\gradlew.bat assembleDebug
```

L'APK est produit dans `app\build\outputs\apk\debug\app-debug.apk`. Pour mettre à jour `ZX980.apk` dans le dépôt :

```powershell
Copy-Item app\build\outputs\apk\debug\app-debug.apk ZX980.apk
```

Il est signé avec la clé de débogage : il s'installe directement, mais ne peut pas être publié sur le Play Store.

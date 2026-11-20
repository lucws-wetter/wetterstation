# Wetterstation LucWS

Ein Dashboard für eine Ecowitt-Wetterstation: Live-Werte, Verläufe, Rekorde,
Kenntage, Kalender-Heatmap, Regen-Jahresvergleich, Windrose und mehr.

**Live:** https://lucws-wetter.github.io/wetterstation/

## So läuft die Webseite

Eine GitHub Action (`.github/workflows/wetter.yml`) holt etwa alle 15 Minuten die Daten
von api.ecowitt.net, baut die Seite und veröffentlicht sie über GitHub Pages.
Die Tageswerte seit Messbeginn liegen in `data/daily.json` und werden einmal am Tag ergänzt.

Die API-Schlüssel stehen **nicht** im Repository, sondern als Secrets unter
*Settings → Secrets and variables → Actions*:

| Name | Inhalt |
|---|---|
| `ECOWITT_APPLICATION_KEY` | Application Key aus ecowitt.net |
| `ECOWITT_API_KEY` | API Key aus ecowitt.net |
| `ECOWITT_MAC` | MAC-Adresse der Station |
| `METEOCORE_CLIENT_ID` | Client-ID aus account.meteocore.com → API-Zugänge (optional, für die Vorhersage) |
| `METEOCORE_CLIENT_SECRET` | Client-Secret dazu |

GitHub Pages muss auf *Settings → Pages → Source: GitHub Actions* stehen.

## Vorhersage

Mit den meteoCORE-Zugangsdaten holt der Ablauf einen frischen Token (OAuth2 Client Credentials, 5 Minuten gültig) und fragt
für den Stationsstandort ab (API-Dokumentation: https://docs.meteocore.com/):

- **meteoCORE Mix**: 7 Tage gewichtet aus allen Modellen (`/api/v1/weather/daily`) und 14-Tage-Trend (`/api/v1/weather/trend`)
- **16 Einzelmodelle** (Stundenreihe und Tagesprodukt): ICON-D2-RUC, ICON-D2, AROME, ICON-CH2, ECMWF, ICON, ICON-EU, GFS,
  UKMO, GEM, AIFS, AICON, ICON-D2-EPS, ECMWF-ENS, GEFS und DWD-MOSMIX. Die Liste steht in `scripts/fetch_data.py` (`MODELS`).
- **Warnungen** des DWD und **Gewitterzellen** der nächsten 60 Minuten bei jedem Lauf

Jedes Modell wird nur nach seinem Takt neu abgefragt (schnelle Modelle stündlich, globale alle 6 Stunden); dazwischen
übernimmt der Ablauf den letzten veröffentlichten Stand (`PREVIOUS_DATA_URL`). Das hält die Last bei etwa 500 Anfragen am Tag.
Gespeichert werden nur ausgewählte Felder, keine Koordinaten oder Ortsnamen. Fehlen die Zugangsdaten oder antwortet
meteoCORE nicht, wird die Seite ohne bzw. mit dem letzten Vorhersagestand gebaut.

## Dateien

- `src/app.html`: das Dashboard (HTML, CSS, JavaScript)
- `scripts/fetch_data.py`: holt die Daten von Ecowitt
- `build.py`: baut daraus die Seite (`python3 build.py site`)
- `settings.json`: `ignoreBefore` (Einrichtungsphase ausblenden) und `maxGust` (Böen darüber gelten als Messfehler)

## Lokales Programm

`python3 build.py live` baut `wetterstation.html`, die direkt im Browser mit Ecowitt spricht.
Dafür braucht es eine `config.local.json` mit `applicationKey`, `apiKey` und `mac`.
Beide Dateien enthalten Schlüssel und sind per `.gitignore` vom Hochladen ausgeschlossen.

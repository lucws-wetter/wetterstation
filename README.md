# Wetterstation LucWS

Ein Dashboard für eine Ecowitt-Wetterstation: Live-Werte, Verläufe, Rekorde,
Kenntage, Kalender-Heatmap, Regen-Jahresvergleich, Windrose und mehr.

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

GitHub Pages muss auf *Settings → Pages → Source: GitHub Actions* stehen.

## Dateien

- `src/app.html`: das Dashboard (HTML, CSS, JavaScript)
- `scripts/fetch_data.py`: holt die Daten von Ecowitt
- `build.py`: baut daraus die Seite (`python3 build.py site`)
- `settings.json`: `ignoreBefore` (Einrichtungsphase ausblenden) und `maxGust` (Böen darüber gelten als Messfehler)

## Lokales Programm

`python3 build.py live` baut `wetterstation.html`, die direkt im Browser mit Ecowitt spricht.
Dafür braucht es eine `config.local.json` mit `applicationKey`, `apiKey` und `mac`.
Beide Dateien enthalten Schlüssel und sind per `.gitignore` vom Hochladen ausgeschlossen.

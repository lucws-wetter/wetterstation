# Radar-Proxy (Cloudflare Worker)

Die meteoCORE-Radarkacheln brauchen ein Token. Damit das Client-Secret nicht öffentlich in der Webseite steht,
holt dieser kleine Proxy das Token (5 Minuten gültig) und reicht **nur Radar-Daten** an die Wetterseite weiter.
Er nimmt nur Anfragen von `https://lucws-wetter.github.io` an und speichert Kacheln zwischen, damit die meteoCORE-API
pro Kachel nur einmal gefragt wird.

## Einrichten über das Cloudflare-Dashboard

1. Kostenloses Konto auf https://dash.cloudflare.com anlegen
2. *Workers & Pages* → *Create* → *Create Worker* → Name `lucws-radar` → *Deploy*
3. *Edit code* → Inhalt von `radar-proxy.js` einfügen → *Deploy*
4. *Settings* → *Variables and Secrets* → zwei Einträge vom Typ **Secret**: `MC_CLIENT_ID` und `MC_CLIENT_SECRET`
5. Die Worker-Adresse (`https://lucws-radar.<name>.workers.dev`) in `settings.json` bei `radarProxy` eintragen

Optional: Variable `ALLOWED_ORIGINS` (kommagetrennt), falls die Seite unter einer anderen Adresse läuft.

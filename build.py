#!/usr/bin/env python3
"""Baut das Wetter-Dashboard aus src/app.html.

  python3 build.py site                  -> site/index.html   (Webseite, lädt site/data.json; ohne Schlüssel)
  python3 build.py live  [snapshot.json] -> wetterstation.html (lokales Programm MIT deinen Schlüsseln)
  python3 build.py share [snapshot.json] -> dist/wetterstation-share.html (Schnappschuss ohne Schlüssel)

Einstellungen: settings.json (öffentlich). Schlüssel: config.local.json (privat, nicht im Repository).
"""
import json, os, sys, pathlib
root = pathlib.Path(__file__).parent
mode = sys.argv[1] if len(sys.argv) > 1 else "site"
snap_path = sys.argv[2] if len(sys.argv) > 2 else None
settings = json.loads((root / "settings.json").read_text())
src = (root / "src" / "app.html").read_text()
base = {"applicationKey": "", "apiKey": "", "mac": "", "ignoreBefore": settings.get("ignoreBefore", ""), "maxGust": settings.get("maxGust", 140)}

snapshot = json.loads(pathlib.Path(snap_path).read_text()) if snap_path else None
if snapshot and mode != "live" and snapshot.get("info"):
    for k in ("lat", "lon"):
        if isinstance(snapshot["info"].get(k), (int, float)):
            snapshot["info"][k] = round(snapshot["info"][k], 1)

if mode == "live":
    keys = json.loads((root / "config.local.json").read_text())
    conf = {**base, **{k: keys.get(k, "") for k in ("applicationKey", "apiKey", "mac")}}
elif mode == "share":
    conf = {**base, "hideControls": True, "hideLocation": True}
elif mode == "site":
    conf = {**base, "dataUrl": "data.json", "hideLocation": True,
            "radarProxy": os.environ.get("RADAR_PROXY", settings.get("radarProxy", ""))}
    snapshot = None
else:
    sys.exit("Modus muss site, live oder share sein.")

js_snap = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/") if snapshot else "null"
body = src.replace("__CONFIG__", json.dumps(conf, ensure_ascii=False, indent=2), 1).replace("__SNAPSHOT__", js_snap, 1)
head = ('<!doctype html>\n<html lang="de">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n')

if mode == "share":
    out = root / "dist" / "wetterstation-share.html"; content = body
elif mode == "site":
    out = root / "site" / "index.html"; content = head + body + "\n</html>\n"
else:
    out = root / "wetterstation.html"; content = head + body + "\n</html>\n"
out.parent.mkdir(exist_ok=True)
out.write_text(content)
print("geschrieben:", out.relative_to(root), f"{out.stat().st_size/1024:.0f} KB")

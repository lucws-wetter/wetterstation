#!/usr/bin/env python3
"""Holt die Daten der Ecowitt-Station und schreibt sie für die Webseite.

Schlüssel kommen aus den Umgebungsvariablen ECOWITT_APPLICATION_KEY, ECOWITT_API_KEY
und ECOWITT_MAC (auf GitHub als Secrets), lokal ersatzweise aus config.local.json.
Optional: METEOCORE_CLIENT_ID und METEOCORE_CLIENT_SECRET für Vorhersage und Warnungen.

Schreibt:
  data/daily.json  Tageswerte seit Messbeginn (wird im Repository versioniert)
  site/data.json   alles, was die Seite braucht (Live-Werte, 24 h, 7 Tage, Tageswerte)
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
API = "https://api.ecowitt.net/api/v3/"
UNITS = {"temp_unitid": 1, "pressure_unitid": 3, "wind_speed_unitid": 7, "rainfall_unitid": 12, "solar_irradiance_unitid": 16}
CB_HIST = "outdoor,indoor,solar_and_uvi,rainfall,rainfall_piezo,wind,pressure"
FIELD = {
    "outdoor.temperature": "t", "outdoor.temperature_high": "tx", "outdoor.temperature_low": "tn",
    "outdoor.feels_like": "fl", "outdoor.dew_point": "td", "outdoor.humidity": "h",
    "outdoor.humidity_high": "hx", "outdoor.humidity_low": "hn",
    "indoor.temperature": "ti", "indoor.temperature_high": "tix", "indoor.temperature_low": "tin", "indoor.humidity": "hi",
    "solar_and_uvi.solar": "sr", "solar_and_uvi.uvi": "uv",
    "rainfall.daily": "rd", "rainfall.rain_rate": "rr",
    "rainfall_piezo.daily": "rd", "rainfall_piezo.rain_rate": "rr",
    "wind.wind_speed": "ws", "wind.wind_gust": "wg", "wind.wind_direction": "wd",
    "pressure.relative": "p", "pressure.relative_high": "px", "pressure.relative_low": "pn",
}


def load_keys():
    k = {
        "applicationKey": os.environ.get("ECOWITT_APPLICATION_KEY", "").strip(),
        "apiKey": os.environ.get("ECOWITT_API_KEY", "").strip(),
        "mac": os.environ.get("ECOWITT_MAC", "").strip().upper(),
    }
    if not all(k.values()):
        p = ROOT / "config.local.json"
        if p.exists():
            c = json.loads(p.read_text())
            k = {x: str(c.get(x, "")).strip() for x in k}
    if not all(k.values()):
        sys.exit("Fehlende Schlüssel: bitte ECOWITT_APPLICATION_KEY, ECOWITT_API_KEY und ECOWITT_MAC setzen.")
    return k


KEYS = load_keys()
_last_call = 0.0


def api(path, params):
    """Eine Ecowitt-Abfrage mit kurzer Pause zwischen den Aufrufen und Wiederholung bei Netzfehlern."""
    global _last_call
    wait = _last_call + 0.6 - time.time()
    if wait > 0:
        time.sleep(wait)
    _last_call = time.time()
    q = {"application_key": KEYS["applicationKey"], "api_key": KEYS["apiKey"], "mac": KEYS["mac"], **UNITS, **params}
    url = API + path + "?" + urllib.parse.urlencode(q)
    last_err = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=40) as r:
                j = json.load(r)
            break
        except Exception as e:  # Netzwerk oder Zeitüberschreitung: Schlüssel nie mit ausgeben
            last_err = type(e).__name__
            time.sleep(4 * (attempt + 1))
    else:
        raise RuntimeError(f"Ecowitt nicht erreichbar ({last_err}) bei {path}")
    if j.get("code") != 0:
        raise RuntimeError(f"Ecowitt meldet bei {path}: {j.get('msg')} (Code {j.get('code')})")
    return j.get("data")


def parse_hist(data, daily):
    out = {}
    if not isinstance(data, dict):
        return out
    for g, fields in data.items():
        if not isinstance(fields, dict):
            continue
        for f, s in fields.items():
            k = FIELD.get(f"{g}.{f}")
            if not k or not isinstance(s, dict) or not isinstance(s.get("list"), dict):
                continue
            piezo = g == "rainfall_piezo"
            for ts, v in s["list"].items():
                try:
                    n = float(v)
                except (TypeError, ValueError):
                    continue
                if daily:
                    key = datetime.fromtimestamp(int(ts), timezone.utc).date().isoformat()
                    r = out.setdefault(key, {"d": key})
                else:
                    key = int(ts) * 1000
                    r = out.setdefault(key, {"ts": key})
                if piezo and k in r:
                    continue
                r[k] = n
    return out


# ---------------------------------------------------------------------------
# meteoCORE: Vorhersage, Warnungen, Gewitterzellen (optional)
# OAuth2 Client Credentials, Token gilt 5 Minuten -> bei jedem Lauf frisch holen.
# Aus den Antworten werden nur ausgewählte Felder übernommen: keine Koordinaten,
# keine Ortsnamen, damit der Standort über die öffentlichen Daten nicht ablesbar ist.
# ---------------------------------------------------------------------------
MC_API = "https://api.meteocore.com"
DAY_KEYS = ["date", "coverage", "systems:count", "ww:code", "clct_eff:percent", "is_day",
            "tmax2m:c", "tmax2m_p10:c", "tmax2m_p90:c", "tmin2m:c", "tmin2m_p10:c", "tmin2m_p90:c",
            "precip:mm", "precip_p10:mm", "precip_p90:mm", "precip_prob_0_1mm:percent", "precip_prob_1mm:percent",
            "precip_prob_10mm:percent", "precip_type", "precip_level:count", "snowfall:mm",
            "sunshine:min", "wind:kmh", "wind:bft", "windgust:kmh", "windgust_p90:kmh", "risks"]
SECTION_KEYS = ["name", "start", "end", "is_day", "ww:code", "clct_eff:percent", "tmax2m:c", "tmin2m:c",
                "precip:mm", "precip_prob_0_1mm:percent", "precip_prob_1mm:percent", "snowfall:mm",
                "sunshine:min", "wind:kmh", "windgust:kmh", "risks"]
HOUR_KEYS = ["t2m:c", "dp2m:c", "precip:mm", "wind:kmh", "windgust:kmh", "winddir:deg", "clct_eff:percent",
             "ww:code", "pmsl:hpa", "relhum2m:percent", "sunshine:min"]
WARN_KEYS = ["identifier", "event", "headline", "description", "instruction", "severity", "urgency", "certainty",
             "onset", "valid_from", "expires", "meteocore_group", "meteocore_event", "web"]


def meteocore_forecast(lat, lon, tz_name):
    cid = os.environ.get("METEOCORE_CLIENT_ID", "").strip()
    sec = os.environ.get("METEOCORE_CLIENT_SECRET", "").strip()
    if not (cid and sec):
        print("meteoCORE: keine Zugangsdaten gesetzt, Vorhersage übersprungen")
        return None
    out = {"fetchedAt": int(time.time() * 1000), "errors": []}
    try:
        body = urllib.parse.urlencode({"grant_type": "client_credentials", "client_id": cid, "client_secret": sec}).encode()
        req = urllib.request.Request(MC_API + "/auth/token", data=body, method="POST",
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=30) as r:
            token = json.load(r)["access_token"]
    except Exception as e:
        code = getattr(e, "code", type(e).__name__)
        out["errors"].append(f"Anmeldung bei meteoCORE fehlgeschlagen ({code})")
        return out

    def get(path, **q):
        url = MC_API + path + "?" + urllib.parse.urlencode(q)
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)

    def pick(d, keys):
        return {k: d[k] for k in keys if isinstance(d, dict) and k in d}

    # 7 Tage aus allen Modellen
    try:
        wd = get("/api/v1/weather/daily", lat=lat, lon=lon, days=7, tz=tz_name)
        info = wd.get("info", {})
        srcs = info.get("sources") or []
        out["daily"] = {
            "days": [{**pick(d, DAY_KEYS), "sections": [pick(s, SECTION_KEYS) for s in d.get("sections") or []]} for d in wd.get("days", [])],
            "models": len(srcs),
            "members": (info.get("data_points") or {}).get("members"),
            "attributions": sorted({s.get("attribution") for s in srcs if s.get("attribution")}),
            "licences": sorted({s.get("licence") for s in srcs if s.get("licence")}),
            "elevation_attribution": info.get("elevation_attribution"),
        }
    except Exception as e:
        out["errors"].append(f"Tagesvorhersage nicht verfügbar ({getattr(e, 'code', type(e).__name__)})")

    # 48 Stunden stündlich aus ICON-D2
    try:
        ts = get("/api/v1/forecast/icon-d2/timeseries/latest", lat=lat, lon=lon)
        fc, info = ts.get("forecast") or {}, ts.get("info") or {}
        times = [int(datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp() * 1000) for t in fc.get("valid_time", [])]
        out["hourly"] = {"t": times, **{k: fc[k] for k in HOUR_KEYS if k in fc},
                         "model": info.get("display_name") or "ICON-D2", "run": info.get("run"),
                         "attribution": info.get("attribution"), "licence": info.get("licence")}
    except Exception as e:
        out["errors"].append(f"Stundenvorhersage nicht verfügbar ({getattr(e, 'code', type(e).__name__)})")

    # Amtliche Warnungen am Standort
    try:
        w = get("/api/v1/warnings/point", lat=lat, lon=lon)
        items = []
        for x in w.get("warnings") or []:
            it = pick(x, WARN_KEYS)
            color = ((x.get("event_codes") or {}).get("AREA_COLOR") or "").split()
            if len(color) == 3:
                it["color"] = "#" + "".join(f"{int(c):02x}" for c in color)
            items.append(it)
        out["warnings"] = {"count": len(items), "max_severity": w.get("max_severity") or "", "items": items}
    except Exception as e:
        out["errors"].append(f"Warnungen nicht verfügbar ({getattr(e, 'code', type(e).__name__)})")

    # Gewitterzellen, die den Standort in den nächsten 60 Minuten treffen
    try:
        sc = get("/api/v1/stormcells/point", lat=lat, lon=lon, lead=60)
        cells = []
        for c in sc.get("cells") or []:
            # nur einfache Kennwerte, keine Positionen
            cells.append({k: v for k, v in c.items() if isinstance(v, (int, float, str, bool)) and k not in ("lat", "lon", "latitude", "longitude", "x", "y")})
        out["stormcells"] = {"count": len(cells), "cells": cells, "reference_time": sc.get("reference_time")}
    except Exception as e:
        out["errors"].append(f"Gewitterzellen nicht verfügbar ({getattr(e, 'code', type(e).__name__)})")

    print(f"meteoCORE: {len((out.get('daily') or {}).get('days', []))} Tage, {len((out.get('hourly') or {}).get('t', []))} Stunden, "
          f"{(out.get('warnings') or {}).get('count', '?')} Warnungen, {(out.get('stormcells') or {}).get('count', '?')} Gewitterzellen"
          + (f", Fehler: {out['errors']}" if out["errors"] else ""))
    return out


def main():
    now = datetime.now(timezone.utc)
    info_raw = api("device/info", {})
    tz = ZoneInfo(info_raw.get("date_zone_id") or "Europe/Berlin")
    info = {
        "name": info_raw.get("name") or "Wetterstation",
        "tz": info_raw.get("date_zone_id") or "Europe/Berlin",
        # Für die öffentliche Seite nur grob (etwa 10 km), reicht für Sonnenauf- und -untergang
        "lat": round(float(info_raw.get("latitude") or 0), 1),
        "lon": round(float(info_raw.get("longitude") or 0), 1),
        "created": int(info_raw.get("createtime") or 0),
        "type": info_raw.get("stationtype") or "",
    }
    local_now = now.astimezone(tz)
    today = local_now.date()
    yesterday = today - timedelta(days=1)
    fmt = lambda d: d.strftime("%Y-%m-%d %H:%M:%S")

    rt = api("device/real_time", {"call_back": "all"})
    rt_at = int(time.time() * 1000)

    # Tageswerte: vorhandene laden, die letzten Tage auffrischen, Neues anhängen
    daily_path = ROOT / "data" / "daily.json"
    daily = {}
    if daily_path.exists():
        for r in json.loads(daily_path.read_text()):
            daily[r["d"]] = r
    first = datetime.fromtimestamp(info["created"], tz).date() if info["created"] else today - timedelta(days=365)
    start = (datetime.fromisoformat(max(daily)).date() - timedelta(days=3)) if daily else first
    start = max(start, first)
    while start <= yesterday:
        end = min(start + timedelta(days=360), yesterday)
        data = api("device/history", {"start_date": f"{start} 00:00:00", "end_date": f"{end} 23:59:59", "cycle_type": "1day", "call_back": CB_HIST})
        for k, r in parse_hist(data, True).items():
            if k < today.isoformat():
                daily[k] = r
        start = end + timedelta(days=1)

    h5 = parse_hist(api("device/history", {"start_date": fmt(local_now - timedelta(hours=24)), "end_date": fmt(local_now + timedelta(minutes=1)), "cycle_type": "5min", "call_back": CB_HIST}), False)
    h30 = parse_hist(api("device/history", {"start_date": fmt(local_now - timedelta(days=7) + timedelta(minutes=1)), "end_date": fmt(local_now + timedelta(minutes=1)), "cycle_type": "30min", "call_back": CB_HIST}), False)

    rows = [daily[k] for k in sorted(daily)]
    daily_path.parent.mkdir(exist_ok=True)
    # Eine Zeile pro Tag mit fester Reihenfolge: Git sieht nur echte Änderungen
    daily_path.write_text("[\n" + ",\n".join(json.dumps(r, sort_keys=True, ensure_ascii=False, separators=(",", ":")) for r in rows) + "\n]\n")

    # Vorhersage mit der genauen Position abfragen; die Position selbst wird nicht gespeichert
    forecast = None
    try:
        forecast = meteocore_forecast(float(info_raw.get("latitude")), float(info_raw.get("longitude")), info["tz"])
    except Exception as e:  # die Wetterseite soll auch ohne Vorhersage gebaut werden
        print(f"meteoCORE: unerwarteter Fehler ({type(e).__name__}), Vorhersage übersprungen")

    out = {"v": 1, "savedAt": int(time.time() * 1000), "info": info, "rt": rt, "rtAt": rt_at, "daily": rows,
           "h5": [h5[k] for k in sorted(h5)], "h30": [h30[k] for k in sorted(h30)]}
    if forecast:
        out["forecast"] = forecast
    site = ROOT / "site"
    site.mkdir(exist_ok=True)
    (site / "data.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    t = rt.get("outdoor", {}).get("temperature", {}).get("value", "?") if isinstance(rt, dict) else "?"
    print(f"OK: {len(rows)} Tage, {len(h5)} 5-Min-Werte, {len(h30)} 30-Min-Werte, aktuell {t} °C")


if __name__ == "__main__":
    main()

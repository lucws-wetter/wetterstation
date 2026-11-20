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
# Modelle, die die Seite zur Auswahl anbietet. every = Minuten, nach denen frisch abgefragt wird
# (orientiert an den Laufzeiten der Modelle); dazwischen wird der letzte Stand weiterverwendet.
MODELS = [
    {"id": "icon-d2-ruc", "label": "ICON-D2-RUC", "group": "short", "every": 60, "desc": "DWD, 2,2 km. Wird jede Stunde neu gerechnet und ist damit das aktuellste Modell für die nächsten Stunden."},
    {"id": "icon-d2", "label": "ICON-D2", "group": "short", "every": 150, "desc": "DWD, 2,2 km. Hochauflösendes Regionalmodell, bildet Schauer, Gewitter und Starkregen besonders direkt ab."},
    {"id": "arome", "label": "AROME", "group": "short", "every": 150, "desc": "Météo-France, 1,3 km. Feinmaschiges französisches Regionalmodell mit eigener Physik, gut als Gegenprobe zu ICON-D2."},
    {"id": "icon-ch2", "label": "ICON-CH2", "group": "short", "every": 300, "desc": "MeteoSchweiz, 2,1 km. Hochauflösend bis 5 Tage, stündliche Werte."},
    {"id": "ecmwf", "label": "ECMWF", "group": "global", "every": 360, "desc": "Europäisches Zentrum für mittelfristige Vorhersage (IFS HRES). Gilt als bestes Globalmodell, bis 15 Tage."},
    {"id": "icon-global", "label": "ICON", "group": "global", "every": 360, "desc": "DWD, globales Modell mit 13 km Gitter, bis 7,5 Tage."},
    {"id": "icon-eu", "label": "ICON-EU", "group": "global", "every": 180, "desc": "DWD, Europa-Ausschnitt mit 6,5 km, stündlich bis 5 Tage."},
    {"id": "gfs", "label": "GFS", "group": "global", "every": 360, "desc": "US-Wetterdienst NOAA. Globales Modell bis 16 Tage."},
    {"id": "ukmo-global", "label": "UKMO", "group": "global", "every": 360, "desc": "Britischer Wetterdienst (Met Office), globales Modell mit 10 km, bis 7 Tage."},
    {"id": "gem-gdps", "label": "GEM", "group": "global", "every": 720, "desc": "Kanadischer Wetterdienst, globales Modell bis 10 Tage."},
    {"id": "aifs-single", "label": "AIFS", "group": "ai", "every": 360, "desc": "ECMWF-Modell auf Basis künstlicher Intelligenz, lernt aus jahrzehntelangen Wetterdaten, bis 15 Tage."},
    {"id": "aicon", "label": "AICON", "group": "ai", "every": 360, "desc": "KI-Modell des DWD, gelernt aus der ICON-Reanalyse, bis 5 Tage."},
    {"id": "icon-d2-eps", "label": "ICON-D2-EPS", "group": "ens", "every": 180, "desc": "DWD-Ensemble aus 20 leicht veränderten Läufen. Linie = Median, Fläche = Spanne der mittleren 80 %."},
    {"id": "ecmwf-ens", "label": "ECMWF-ENS", "group": "ens", "every": 360, "desc": "ECMWF-Ensemble mit 51 Läufen. Zeigt, wie sicher die nächsten Tage sind."},
    {"id": "gefs", "label": "GEFS", "group": "ens", "every": 360, "desc": "NOAA-Ensemble mit 31 Läufen, bis 16 Tage."},
    {"id": "mosmix-l", "label": "MOSMIX", "group": "stat", "every": 360, "desc": "DWD-Stationsprognose: Modelldaten statistisch mit Messungen am nächstgelegenen DWD-Messort korrigiert."},
]
HORIZON_H = 14 * 24
TS_PARAMS = "t2m:c,dp2m:c,precip:mm,clct_eff:percent,wind:kmh,windgust:kmh,winddir:deg,ww:code,pmsl:hpa,sunshine:min,cape:jkg"
HOUR_KEYS = ["t2m:c", "dp2m:c", "precip:mm", "clct_eff:percent", "wind:kmh", "windgust:kmh", "winddir:deg", "ww:code",
             "pmsl:hpa", "sunshine:min", "cape:jkg",
             "t2m_p10:c", "t2m_p90:c", "precip_p10:mm", "precip_p90:mm", "windgust_p10:kmh", "windgust_p90:kmh"]
DAY_KEYS = ["date", "coverage", "systems:count", "ww:code", "clct_eff:percent", "is_day",
            "tmax2m:c", "tmax2m_p10:c", "tmax2m_p90:c", "tmin2m:c", "tmin2m_p10:c", "tmin2m_p90:c",
            "precip:mm", "precip_p10:mm", "precip_p90:mm", "precip_max_1h:mm", "precip_prob_0_1mm:percent", "precip_prob_1mm:percent",
            "precip_prob_10mm:percent", "precip_type", "precip_level:count", "snowfall:mm",
            "sunshine:min", "sunshine_p10:min", "sunshine_p90:min", "wind:kmh", "wind:bft", "winddir:deg",
            "windgust:kmh", "windgust_p10:kmh", "windgust_p90:kmh", "risks"]
SECTION_KEYS = ["name", "start", "end", "is_day", "ww:code", "clct_eff:percent", "tmax2m:c", "tmin2m:c",
                "precip:mm", "precip_prob_0_1mm:percent", "precip_prob_1mm:percent", "snowfall:mm",
                "sunshine:min", "wind:kmh", "windgust:kmh", "risks"]
WARN_KEYS = ["identifier", "event", "headline", "description", "instruction", "severity", "urgency", "certainty",
             "onset", "valid_from", "expires", "meteocore_group", "meteocore_event", "web"]


def rnd(v, nd=1):
    return round(v, nd) if isinstance(v, float) else v


def pick(d, keys):
    return {k: d[k] for k in keys if isinstance(d, dict) and k in d}


def trim_days(days, limit=14):
    out = []
    for d in (days or [])[:limit]:
        x = {k: rnd(v) for k, v in pick(d, DAY_KEYS).items()}
        x["sections"] = [{k: rnd(v) for k, v in pick(s, SECTION_KEYS).items()} for s in d.get("sections") or []]
        out.append(x)
    return out


def load_previous():
    """Letzten veröffentlichten Stand laden, damit nicht jedes Modell bei jedem Lauf neu abgefragt wird."""
    local = ROOT / "site" / "data.json"
    try:
        if local.exists():
            return json.loads(local.read_text())
        url = os.environ.get("PREVIOUS_DATA_URL", "").strip()
        if url:
            with urllib.request.urlopen(url + "?t=" + str(int(time.time())), timeout=20) as r:
                return json.load(r)
    except Exception as e:
        print(f"Vorheriger Stand nicht lesbar ({type(e).__name__}), alles wird neu geholt")
    return {}


def meteocore_forecast(lat, lon, tz_name, prev):
    cid = os.environ.get("METEOCORE_CLIENT_ID", "").strip()
    sec = os.environ.get("METEOCORE_CLIENT_SECRET", "").strip()
    if not (cid and sec):
        print("meteoCORE: keine Zugangsdaten gesetzt, Vorhersage übersprungen")
        return None
    now_ms = int(time.time() * 1000)
    old = (prev or {}).get("forecast") or {}
    out = {"fetchedAt": now_ms, "errors": [], "models": {}, "groups": {"short": "Kurzfrist", "global": "Global", "ai": "KI-Modelle", "ens": "Ensembles", "stat": "Statistik"}}
    try:
        body = urllib.parse.urlencode({"grant_type": "client_credentials", "client_id": cid, "client_secret": sec}).encode()
        req = urllib.request.Request(MC_API + "/auth/token", data=body, method="POST", headers={"Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=30) as r:
            token = json.load(r)["access_token"]
    except Exception as e:
        out["errors"].append(f"Anmeldung bei meteoCORE fehlgeschlagen ({getattr(e, 'code', type(e).__name__)})")
        for k in ("mix", "trend", "models"):  # alten Stand behalten
            if old.get(k):
                out[k] = old[k]
        return out
    calls = 0

    def get(path, **q):
        nonlocal calls
        calls += 1
        url = MC_API + path + "?" + urllib.parse.urlencode(q)
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)

    def due(block, minutes):
        return not block or now_ms - (block.get("fetchedAt") or 0) > minutes * 60e3

    # meteoCORE-Mix: 7 Tage aus allen Modellen (stündlich frisch), 14-Tage-Trend (alle 3 Stunden)
    mix_old, trend_old = old.get("mix"), old.get("trend")
    if due(mix_old, 55):
        try:
            wd = get("/api/v1/weather/daily", lat=lat, lon=lon, days=7, tz=tz_name)
            info = wd.get("info", {}); srcs = info.get("sources") or []
            out["mix"] = {"fetchedAt": now_ms, "days": trim_days(wd.get("days"), 7), "models": len(srcs),
                          "members": (info.get("data_points") or {}).get("members"),
                          "attributions": sorted({s.get("attribution") for s in srcs if s.get("attribution")}),
                          "elevation_attribution": info.get("elevation_attribution")}
        except Exception as e:
            out["errors"].append(f"Mix nicht verfügbar ({getattr(e, 'code', type(e).__name__)})")
            if mix_old: out["mix"] = mix_old
    else:
        out["mix"] = mix_old
    if due(trend_old, 175):
        try:
            tr = get("/api/v1/weather/trend", lat=lat, lon=lon, days=14, tz=tz_name)
            out["trend"] = {"fetchedAt": now_ms, "days": [{k: rnd(v) for k, v in pick(d, DAY_KEYS).items()} for d in tr.get("days", [])],
                            "models": len((tr.get("info") or {}).get("sources") or [])}
        except Exception as e:
            out["errors"].append(f"14-Tage-Trend nicht verfügbar ({getattr(e, 'code', type(e).__name__)})")
            if trend_old: out["trend"] = trend_old
    else:
        out["trend"] = trend_old

    # Einzelmodelle: Stundenreihe + Tagesprodukt
    old_models = old.get("models") or {}
    horizon_ms = now_ms + HORIZON_H * 3600e3
    for m in MODELS:
        mid, prev_m = m["id"], old_models.get(m["id"])
        if not due(prev_m, m["every"] - 5):
            out["models"][mid] = prev_m
            continue
        try:
            ts = get(f"/api/v1/forecast/{mid}/timeseries/latest", lat=lat, lon=lon, params=TS_PARAMS)
            fc, info = ts.get("forecast") or {}, ts.get("info") or {}
            times = [int(datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp() * 1000) for t in fc.get("valid_time", [])]
            keep = [i for i, t in enumerate(times) if now_ms - 3 * 3600e3 <= t <= horizon_ms]
            hourly = {"t": [times[i] for i in keep]}
            for k in HOUR_KEYS:
                if k in fc and isinstance(fc[k], list):
                    nd = 2 if k.startswith("precip") else 1
                    hourly[k] = [rnd(fc[k][i], nd) if i < len(fc[k]) else None for i in keep]
            daily = []
            try:
                dd = get(f"/api/v1/forecast/{mid}/daily/latest", lat=lat, lon=lon, tz=tz_name)
                daily = trim_days(dd.get("days"), 14)
            except Exception as e:
                out["errors"].append(f"{m['label']}: Tageswerte fehlen ({getattr(e, 'code', type(e).__name__)})")
            ens = info.get("ensemble") or {}
            out["models"][mid] = {"fetchedAt": now_ms, "label": m["label"], "group": m["group"], "desc": m["desc"],
                                  "name": info.get("display_name") or m["label"], "run": info.get("run"),
                                  "attribution": info.get("attribution"), "licence": info.get("licence"),
                                  "members": ens.get("members"), "hourly": hourly, "daily": daily}
        except Exception as e:
            out["errors"].append(f"{m['label']} nicht verfügbar ({getattr(e, 'code', type(e).__name__)})")
            if prev_m and now_ms - (prev_m.get("fetchedAt") or 0) < 24 * 3600e3:
                out["models"][mid] = prev_m

    # Amtliche Warnungen und Gewitterzellen: bei jedem Lauf
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
    try:
        sc = get("/api/v1/stormcells/point", lat=lat, lon=lon, lead=60)
        cells = [{k: v for k, v in c.items() if isinstance(v, (int, float, str, bool)) and k not in ("lat", "lon", "latitude", "longitude", "x", "y")}
                 for c in sc.get("cells") or []]
        out["stormcells"] = {"count": len(cells), "cells": cells, "reference_time": sc.get("reference_time")}
    except Exception as e:
        out["errors"].append(f"Gewitterzellen nicht verfügbar ({getattr(e, 'code', type(e).__name__)})")

    fresh = sum(1 for v in out["models"].values() if v and v.get("fetchedAt") == now_ms)
    print(f"meteoCORE: {calls} Abfragen, {len(out['models'])} Modelle ({fresh} neu geholt), "
          f"{(out.get('warnings') or {}).get('count', '?')} Warnungen, {(out.get('stormcells') or {}).get('count', '?')} Gewitterzellen"
          + (f", Hinweise: {out['errors']}" if out["errors"] else ""))
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
        forecast = meteocore_forecast(float(info_raw.get("latitude")), float(info_raw.get("longitude")), info["tz"], load_previous())
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

#!/usr/bin/env python3
"""Holt die Daten der Ecowitt-Station und schreibt sie für die Webseite.

Schlüssel kommen aus den Umgebungsvariablen ECOWITT_APPLICATION_KEY, ECOWITT_API_KEY
und ECOWITT_MAC (auf GitHub als Secrets), lokal ersatzweise aus config.local.json.

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

    out = {"v": 1, "savedAt": int(time.time() * 1000), "info": info, "rt": rt, "rtAt": rt_at, "daily": rows,
           "h5": [h5[k] for k in sorted(h5)], "h30": [h30[k] for k in sorted(h30)]}
    site = ROOT / "site"
    site.mkdir(exist_ok=True)
    (site / "data.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    t = rt.get("outdoor", {}).get("temperature", {}).get("value", "?") if isinstance(rt, dict) else "?"
    print(f"OK: {len(rows)} Tage, {len(h5)} 5-Min-Werte, {len(h30)} 30-Min-Werte, aktuell {t} °C")


if __name__ == "__main__":
    main()

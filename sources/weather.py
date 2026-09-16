"""
Kickoff-hour weather for upcoming games, from Open-Meteo (free, no API key).

For every current-season game that hasn't been played and kicks off within
WEATHER_DAYS_AHEAD days, look up the home stadium, ask Open-Meteo for the
hourly forecast, and keep the hour closest to kickoff. Domes are skipped
(weather doesn't matter indoors); retractable roofs are fetched, because the
roof is often open. Neutral-site games (London, Germany, Brazil...) are
skipped unless the stadium is listed in NEUTRAL_SITES.

Each run replaces the forecasts it fetched; a game whose fetch fails keeps its
last good forecast, and played games are dropped. A failed request
is a warning, never a crash: the board just shows "forecast unavailable".
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from config import CURRENT_SEASON, WEATHER_URL, WEATHER_DAYS_AHEAD
from db import start_run, finish_run

EASTERN = ZoneInfo("America/New_York")    # nflverse kickoff times are Eastern

# Home stadium coordinates (latitude, longitude) and roof type by team.
# roof: "dome" = always closed (skipped), "retractable", "open".
STADIUMS = {
    "ARI": (33.528, -112.263, "retractable"), "ATL": (33.755, -84.401, "retractable"),
    "BAL": (39.278, -76.623, "open"),         "BUF": (42.774, -78.787, "open"),
    "CAR": (35.226, -80.853, "open"),         "CHI": (41.862, -87.617, "open"),
    "CIN": (39.095, -84.516, "open"),         "CLE": (41.506, -81.700, "open"),
    "DAL": (32.748, -97.093, "retractable"),  "DEN": (39.744, -105.020, "open"),
    "DET": (42.340, -83.046, "dome"),         "GB":  (44.501, -88.062, "open"),
    "HOU": (29.685, -95.411, "retractable"),  "IND": (39.760, -86.164, "retractable"),
    "JAX": (30.324, -81.637, "open"),         "KC":  (39.049, -94.484, "open"),
    "LA":  (33.953, -118.339, "dome"),        "LAC": (33.953, -118.339, "dome"),
    "LV":  (36.091, -115.184, "dome"),        "MIA": (25.958, -80.239, "open"),
    "MIN": (44.974, -93.258, "dome"),         "NE":  (42.091, -71.264, "open"),
    "NO":  (29.951, -90.081, "dome"),         "NYG": (40.814, -74.074, "open"),
    "NYJ": (40.814, -74.074, "open"),         "PHI": (39.901, -75.168, "open"),
    "PIT": (40.447, -80.016, "open"),         "SEA": (47.595, -122.332, "open"),
    "SF":  (37.403, -121.970, "open"),        "TB":  (27.976, -82.503, "open"),
    "TEN": (36.166, -86.771, "open"),         "WAS": (38.908, -76.864, "open"),
}
NEUTRAL_SITES: dict[str, tuple[float, float, str]] = {}   # stadium name -> (lat, lon, roof)


def roof_type(home_team: str, location: str | None, stadium: str | None, nflverse_roof: str | None) -> str | None:
    """'dome', 'retractable', 'open', or None when we don't know the site."""
    if nflverse_roof in ("dome", "closed"):
        return "dome"
    if location == "Neutral":
        site = NEUTRAL_SITES.get(stadium or "")
        return site[2] if site else None
    st = STADIUMS.get(home_team)
    return st[2] if st else None


def kickoff_utc(gameday: str, gametime: str | None) -> datetime:
    hh, mm = (gametime or "13:00").split(":")[:2]
    local = datetime.fromisoformat(gameday).replace(hour=int(hh), minute=int(mm), tzinfo=EASTERN)
    return local.astimezone(timezone.utc)


def pick_hour(hourly: dict, when: datetime) -> dict | None:
    """The forecast row whose hour is closest to `when` (UTC). None if > 90 min away."""
    times = hourly.get("time") or []
    best, best_gap = None, None
    for i, t in enumerate(times):
        ts = datetime.fromisoformat(t).replace(tzinfo=timezone.utc)
        gap = abs((ts - when).total_seconds())
        if best_gap is None or gap < best_gap:
            best, best_gap = i, gap
    if best is None or best_gap > 90 * 60:
        return None
    def val(key):
        arr = hourly.get(key) or []
        return arr[best] if best < len(arr) else None
    return {"time": times[best], "temp_f": val("temperature_2m"), "wind_mph": val("wind_speed_10m"),
            "gust_mph": val("wind_gusts_10m"), "precip_pct": val("precipitation_probability")}


def fetch(lat: float, lon: float, start: datetime, end: datetime) -> dict:
    resp = requests.get(WEATHER_URL, timeout=30, params={
        "latitude": lat, "longitude": lon,
        "hourly": "temperature_2m,precipitation_probability,wind_speed_10m,wind_gusts_10m",
        "temperature_unit": "fahrenheit", "wind_speed_unit": "mph",
        "timezone": "UTC",
        "start_date": start.date().isoformat(), "end_date": end.date().isoformat(),
    })
    resp.raise_for_status()
    return resp.json().get("hourly", {})


def load_weather(conn, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    run_id = start_run(conn, "open_meteo_weather")
    games = conn.execute("""
        SELECT game_id, gameday, gametime, home_team, location, stadium, roof
        FROM games WHERE season = ? AND result IS NULL
    """, (CURRENT_SEASON,)).fetchall()
    rows, skipped, failed = [], 0, 0
    fetched_at = now.isoformat(timespec="seconds")
    for g in games:
        ko = kickoff_utc(g["gameday"], g["gametime"])
        if not (now - timedelta(hours=4) <= ko <= now + timedelta(days=WEATHER_DAYS_AHEAD)):
            continue
        roof = roof_type(g["home_team"], g["location"], g["stadium"], g["roof"])
        if roof in (None, "dome"):
            skipped += 1
            continue
        lat, lon, _ = (NEUTRAL_SITES.get(g["stadium"] or "") if g["location"] == "Neutral"
                       else STADIUMS[g["home_team"]])
        try:
            h = pick_hour(fetch(lat, lon, ko - timedelta(hours=2), ko + timedelta(hours=2)), ko)
        except Exception:
            failed += 1
            continue
        if h:
            rows.append((g["game_id"], fetched_at, h["time"], h["temp_f"], h["wind_mph"],
                         h["gust_mph"], h["precip_pct"], "open-meteo"))
    with conn:
        # Replace what we fetched; keep the last good forecast for games whose
        # fetch failed this time; drop games that have been played.
        conn.executemany("INSERT OR REPLACE INTO weather_forecasts VALUES (?,?,?,?,?,?,?,?)", rows)
        conn.execute("""DELETE FROM weather_forecasts WHERE game_id IN
                        (SELECT game_id FROM games WHERE result IS NOT NULL)""")
    msg = f"{len(rows)} forecasts, {skipped} indoor/unknown skipped, {failed} failed"
    finish_run(conn, run_id, "ok" if not failed else "partial", len(rows), msg)
    return msg

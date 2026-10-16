"""
ESPN's league-wide NFL injury list (free, no key): config.ESPN_INJURIES_URL.

The official NFL injury report (nflverse) only changes Wednesday to Friday and
says nothing about when a player will be back. ESPN's list updates through the
week and adds an estimated return date and the time of its last update on
each player.

Stored per player (table espn_injuries): status, injury, estimated return,
when ESPN last updated him, and his gsis_id so the board can line him up with
the depth chart. ESPN's written notes (shortComment / longComment) are NOT
stored: the site is public, so the board pulls them live in the viewer's
browser instead (the injury refresh icon).

Each pull replaces the whole table. A failed pull is a warning, never a crash,
and keeps the last good list.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

import requests

from config import CURRENT_SEASON, ESPN_INJURIES_URL
from db import start_run, finish_run
from queries import name_key

# ESPN abbreviations that differ from nflverse's. The template's ESPN_TEAM
# map must match this one.
TEAM_FIX = {"LAR": "LA", "WSH": "WAS"}

_ID_RE = re.compile(r"/id/(\d+)")
_UNSPECIFIED = {"", "not specified"}


def injury_text(details: dict | None) -> str | None:
    """'Foot (surgery, right)' from ESPN's details block; None if there's no injury type.
    The template's espnInjury() builds the same text from the live feed."""
    d = details or {}
    kind = (d.get("type") or "").strip()
    if kind.lower() in _UNSPECIFIED:
        return None
    extra = [x.strip() for x in (d.get("detail"), d.get("side"))
             if isinstance(x, str) and x.strip().lower() not in _UNSPECIFIED and x.strip().lower() != kind.lower()]
    return f"{kind} ({', '.join(x.lower() for x in extra)})" if extra else kind


def espn_player_id(athlete: dict) -> str | None:
    """ESPN's player id, read from the player-page link (the feed has no plain id field)."""
    for link in athlete.get("links") or []:
        m = _ID_RE.search(link.get("href") or "")
        if m:
            return m.group(1)
    return None


def parse_feed(data: dict) -> list[dict]:
    """One dict per listed player, structured fields only (no ESPN text)."""
    out = {}
    for team in data.get("injuries") or []:
        for x in team.get("injuries") or []:
            a = x.get("athlete") or {}
            eid = espn_player_id(a)
            abbr = ((a.get("team") or {}).get("abbreviation") or "").upper()
            if not eid or not abbr or not x.get("status"):
                continue
            ret = (x.get("details") or {}).get("returnDate")
            out[eid] = {
                "espn_id": eid,
                "team": TEAM_FIX.get(abbr, abbr),
                "player_name": a.get("displayName"),
                "position": (a.get("position") or {}).get("abbreviation"),
                "status": x["status"],
                "injury": injury_text(x.get("details")),
                "return_date": ret[:10] if isinstance(ret, str) and re.match(r"\d{4}-\d{2}-\d{2}", ret) else None,
                "updated_at": x.get("date"),
            }
    return list(out.values())


def match_gsis(conn, rows: list[dict], season: int = CURRENT_SEASON) -> None:
    """Fill gsis_id in place: by ESPN id (weekly roster, then opening depth
    chart), else by team + name when exactly one player on that team matches."""
    by_id = {}
    for r in conn.execute("""SELECT espn_id, gsis_id FROM depth_chart_opening
                             WHERE espn_id IS NOT NULL AND gsis_id IS NOT NULL"""):
        by_id[r["espn_id"]] = r["gsis_id"]
    # Weekly roster last, so the newer source wins.
    for r in conn.execute("""SELECT espn_id, gsis_id FROM roster_status
                             WHERE season = ? AND espn_id IS NOT NULL ORDER BY week""", (season,)):
        by_id[r["espn_id"]] = r["gsis_id"]
    by_name: dict[tuple, set] = {}
    for r in conn.execute("""SELECT DISTINCT team, full_name, gsis_id FROM roster_status
                             WHERE season = ? AND week = (SELECT MAX(week) FROM roster_status WHERE season = ?)""",
                          (season, season)):
        by_name.setdefault((r["team"], name_key(r["full_name"])), set()).add(r["gsis_id"])
    for row in rows:
        gid = by_id.get(row["espn_id"])
        if gid is None:
            hits = by_name.get((row["team"], name_key(row["player_name"])), set())
            gid = next(iter(hits)) if len(hits) == 1 else None
        row["gsis_id"] = gid


COLS = ["espn_id", "team", "gsis_id", "player_name", "position", "status",
        "injury", "return_date", "updated_at", "fetched_at"]


def load_espn_injuries(conn, data: dict | None = None, now: datetime | None = None) -> str:
    """Pull ESPN's list and replace espn_injuries. `data` skips the download (tests)."""
    now = now or datetime.now(timezone.utc)
    run_id = start_run(conn, "espn_injuries")
    try:
        if data is None:
            resp = requests.get(ESPN_INJURIES_URL, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        rows = parse_feed(data)
        if not rows:
            raise ValueError("ESPN returned an empty injury list")
        match_gsis(conn, rows)
    except Exception as exc:
        finish_run(conn, run_id, "error", 0, str(exc)[:200])
        raise
    fetched = now.isoformat(timespec="seconds")
    with conn:
        conn.execute("DELETE FROM espn_injuries")
        conn.executemany(f"INSERT INTO espn_injuries ({', '.join(COLS)}) VALUES ({', '.join('?' * len(COLS))})",
                         [tuple({**r, "fetched_at": fetched}[c] for c in COLS) for r in rows])
    listed = sum(r["status"] != "Active" for r in rows)
    unmatched = sum(r["gsis_id"] is None for r in rows if r["status"] != "Active")
    msg = f"{listed} players listed ({len(rows) - listed} 'Active' news items), {unmatched} listed players unmatched"
    finish_run(conn, run_id, "ok", len(rows), msg)
    return msg

"""
Player prop lines from PropLine, through the odds Worker (worker/props.js),
into prop_lines.

The Worker holds the PropLine key and has already picked one line per player
and market (DraftKings' main line, else the line most books deal), so this
only matches PropLine's games to nflverse games and PropLine's players to
nflverse player ids:
  - games: same two teams (PropLine uses full names, "Denver Broncos") and a
    kickoff within 36 hours of nflverse's;
  - players: PropLine's player_id is ESPN's athlete id ("espn:4430878"),
    matched through roster_status / depth_chart_opening espn_id; else a
    unique name match on either team's roster.
Each game's lines are replaced only when its pull works; a failed pull keeps
the last good lines. Without the Worker's PropLine secret the Worker answers
503 and this is skipped with a note.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import requests

from config import CURRENT_SEASON, PROPS_RELAY_URL
from db import start_run, finish_run
from queries import name_key
from sources.weather import kickoff_utc

NICK = {"Cardinals": "ARI", "Falcons": "ATL", "Ravens": "BAL", "Bills": "BUF", "Panthers": "CAR",
        "Bears": "CHI", "Bengals": "CIN", "Browns": "CLE", "Cowboys": "DAL", "Broncos": "DEN",
        "Lions": "DET", "Packers": "GB", "Texans": "HOU", "Colts": "IND", "Jaguars": "JAX",
        "Chiefs": "KC", "Rams": "LA", "Chargers": "LAC", "Raiders": "LV", "Dolphins": "MIA",
        "Vikings": "MIN", "Patriots": "NE", "Saints": "NO", "Giants": "NYG", "Jets": "NYJ",
        "Eagles": "PHI", "Steelers": "PIT", "Seahawks": "SEA", "49ers": "SF", "Buccaneers": "TB",
        "Titans": "TEN", "Commanders": "WAS"}


def team_abbr(full_name: str | None) -> str | None:
    """'Denver Broncos' -> 'DEN' (by nickname; None if unknown)."""
    s = (full_name or "").strip()
    return next((a for nick, a in NICK.items() if s.endswith(nick)), None)


def match_game(ev: dict, games: list) -> str | None:
    """nflverse game_id for a PropLine event, or None."""
    home, away = team_abbr(ev.get("home")), team_abbr(ev.get("away"))
    try:
        when = datetime.fromisoformat(str(ev.get("commence")).replace("Z", "+00:00"))
    except ValueError:
        return None
    for g in games:
        if {g["home_team"], g["away_team"]} == {home, away} \
                and abs(kickoff_utc(g["gameday"], g["gametime"]) - when) < timedelta(hours=36):
            return g["game_id"]
    return None


def player_lookup(conn, teams: set):
    """(espn id -> (gsis, team), {(team, name key) -> {(gsis, team)}}) for the given teams."""
    by_espn, by_name = {}, {}
    for r in conn.execute("SELECT espn_id, gsis_id, team FROM depth_chart_opening WHERE espn_id IS NOT NULL AND gsis_id IS NOT NULL"):
        by_espn[r["espn_id"]] = (r["gsis_id"], r["team"])
    latest = conn.execute("SELECT MAX(week) FROM roster_status WHERE season = ?", (CURRENT_SEASON,)).fetchone()[0]
    for r in conn.execute("""SELECT espn_id, gsis_id, team, full_name FROM roster_status
                             WHERE season = ? AND week = ?""", (CURRENT_SEASON, latest)):
        if r["espn_id"]:
            by_espn[r["espn_id"]] = (r["gsis_id"], r["team"])     # current team wins over Week 1's
        if r["team"] in teams:
            by_name.setdefault((r["team"], name_key(r["full_name"])), set()).add((r["gsis_id"], r["team"]))
    return by_espn, by_name


def match_player(row: dict, teams: tuple, by_espn: dict, by_name: dict):
    """(gsis_id, team) for a PropLine prop row, or (None, None)."""
    pid = row.get("pid") or ""
    if pid.startswith("espn:"):
        hit = by_espn.get(pid[5:])
        if hit and hit[1] in teams:
            return hit
    hits = set().union(*(by_name.get((t, name_key(row.get("n"))), set()) for t in teams))
    return next(iter(hits)) if len(hits) == 1 else (None, None)


def _get(path: str) -> dict:
    resp = requests.get(PROPS_RELAY_URL.rstrip("/") + path, timeout=60)
    body = resp.json()
    if not resp.ok:
        raise RuntimeError(body.get("error") or f"status {resp.status_code}")
    return body


def load_props(conn, now: datetime | None = None, fetch=_get) -> str:
    now = now or datetime.now(timezone.utc)
    if not PROPS_RELAY_URL:
        return "skipped (no PROPS_RELAY_URL)"
    run_id = start_run(conn, "propline_props")
    try:
        events = fetch("/props/events")["events"]
    except Exception as exc:
        finish_run(conn, run_id, "error", 0, str(exc)[:200])
        raise
    games = conn.execute("""SELECT game_id, gameday, gametime, home_team, away_team FROM games
                            WHERE season = ? AND result IS NULL""", (CURRENT_SEASON,)).fetchall()
    teams_of = {g["game_id"]: (g["away_team"], g["home_team"]) for g in games}
    by_espn, by_name = player_lookup(conn, {t for g in games for t in (g["home_team"], g["away_team"])})
    n_games = n_rows = unmatched = failed = 0
    kickoff = {g["game_id"]: kickoff_utc(g["gameday"], g["gametime"]) for g in games}
    for ev in events:
        gid = match_game(ev, games)
        # Once a game kicks off its lines are in-play prices, not pregame ones:
        # keep the last pregame pull instead of overwriting it.
        if not gid or kickoff[gid] <= now:
            continue
        try:
            body = fetch(f"/props/event/{ev['id']}")
        except Exception:
            failed += 1
            continue
        rows = []
        for p in body.get("props") or []:
            gsis, team = match_player(p, teams_of[gid], by_espn, by_name)
            unmatched += gsis is None
            rows.append((gid, str(ev["id"]), p["m"], p["n"], p.get("pid"), gsis, team, p["pt"],
                         p.get("o"), p.get("u"), p.get("bk"), p.get("nb"), body.get("fetched_at") or now.isoformat()))
        if not rows:
            continue
        with conn:
            conn.execute("DELETE FROM prop_lines WHERE game_id = ?", (gid,))
            conn.executemany("INSERT OR REPLACE INTO prop_lines VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        n_games += 1; n_rows += len(rows)
    with conn:   # drop games that have been played
        conn.execute("DELETE FROM prop_lines WHERE game_id IN (SELECT game_id FROM games WHERE result IS NOT NULL)")
    msg = f"{n_rows} props for {n_games} games, {unmatched} players unmatched, {failed} games failed"
    finish_run(conn, run_id, "ok" if not failed else "partial", n_rows, msg)
    return msg

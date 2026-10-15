"""
Player availability loaders: depth charts, injury reports, roster status
(injured reserve and other lists) and snap counts. All nflverse, no API key.

Where each piece comes from and how fresh it is:

  depth_charts_{season}   ESPN depth charts, snapshotted daily since the
                          offseason. We keep two slices of this: each team's
                          most recent snapshot (depth_chart), and the first
                          snapshot on/after that season's roster cutdown
                          (depth_chart_opening) -- the "beginning of the
                          year" roster the availability board is overlaid
                          onto. One CSV download gives us both, since
                          nflverse keeps the whole season's daily history in
                          the same file rather than only the latest day.
  injuries_{season}       The official NFL injury report (Wed-Fri). Each week's
                          report shows up once teams start filing it, so the
                          upcoming week can be missing early in the week.
  roster_weekly_{season}  Weekly roster status. Injured reserve, PUP and similar
                          lists live here, not on the injury report.
  snap_counts_{season}    Pro Football Reference snap counts per game.

Every table is replaced wholesale for the seasons loaded, inside one
transaction, so a failed download never leaves a table half-written.
"""
from __future__ import annotations

import io
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional

import pandas as pd
import requests

from config import (CURRENT_SEASON, DEPTH_CHARTS_URL, INJURIES_URL,
                    WEEKLY_ROSTERS_URL, SNAP_COUNTS_URL)
from db import start_run, finish_run


def _get(url: str) -> requests.Response:
    resp = requests.get(url, timeout=300)
    if resp.status_code == 404:
        raise FileNotFoundError(url)
    resp.raise_for_status()
    return resp


def _csv(url: str, **kw) -> pd.DataFrame:
    return pd.read_csv(io.BytesIO(_get(url).content), compression="gzip", low_memory=False, **kw)


def _last_modified(resp: requests.Response) -> Optional[str]:
    """The download's Last-Modified header as a UTC ISO timestamp, or None."""
    raw = resp.headers.get("Last-Modified")
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw).astimezone(timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError):
        return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean(df: pd.DataFrame) -> list[tuple]:
    """DataFrame -> rows with None instead of NaN."""
    df = df.astype(object).where(pd.notna(df), None)
    return list(df.itertuples(index=False, name=None))


def _replace(conn, table: str, cols: list[str], rows: list[tuple], where: str = "", params=()) -> int:
    with conn:
        conn.execute(f"DELETE FROM {table} {where}", params)
        conn.executemany(
            f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
            rows,
        )
    return len(rows)


# ---------------------------------------------------------------------------

def _tag_side(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    grp = df["pos_grp"].fillna("")
    df["side"] = "O"
    df.loc[grp.str.endswith(" D") | grp.str.contains("Defense"), "side"] = "D"
    df.loc[grp.str.contains("Special"), "side"] = "ST"
    return df


def _depth_rows(df: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({
        "team": df["team"], "side": df["side"], "formation": df["pos_grp"],
        "pos_abb": df["pos_abb"], "pos_slot": df["pos_slot"], "pos_rank": df["pos_rank"],
        "player_name": df["player_name"], "gsis_id": df["gsis_id"],
        "espn_id": df["espn_id"].astype("Int64").astype(str), "snapshot_at": df["dt"],
    })


def _opening_day_anchor(conn, season: int) -> Optional[str]:
    """
    The dt cutoff that marks "the beginning of the year" for a depth chart.

    ESPN's daily snapshots go back to the offseason (roster building, OTAs,
    training camp), so the earliest snapshot on file is NOT what anyone means
    by "opening day" -- it's March or April. Final roster cutdowns happen a
    few days before Week 1 kicks off, so we anchor to one week before that
    season's Week 1 kickoff and take the first snapshot on/after it. Using
    the schedule instead of a hardcoded calendar date means this keeps
    working automatically as Week 1's date moves year to year.

    Returns None if Week 1 hasn't been loaded into `games` yet.
    """
    row = conn.execute(
        "SELECT MIN(gameday) AS d FROM games WHERE season = ? AND week = 1", (season,),
    ).fetchone()
    if row is None or row["d"] is None:
        return None
    anchor = pd.Timestamp(row["d"]) - pd.Timedelta(days=7)
    return anchor.strftime("%Y-%m-%d")


def load_depth_charts(conn, season: int = CURRENT_SEASON) -> int:
    df = _tag_side(_csv(DEPTH_CHARTS_URL.format(season=season)))

    latest = df[df["dt"] == df.groupby("team")["dt"].transform("max")].copy()
    n = _replace(conn, "depth_chart", list(_depth_rows(latest).columns), _clean(_depth_rows(latest)))

    anchor = _opening_day_anchor(conn, season)
    if anchor is not None:
        eligible = df[df["dt"] >= anchor]
        if not eligible.empty:
            opening = eligible[eligible["dt"] == eligible.groupby("team")["dt"].transform("min")].copy()
            rows = _depth_rows(opening)
            _replace(conn, "depth_chart_opening", list(rows.columns), _clean(rows))
    return n


def load_injuries(conn, season: int = CURRENT_SEASON) -> int:
    resp = _get(INJURIES_URL.format(season=season))
    df = pd.read_csv(io.BytesIO(resp.content), compression="gzip", low_memory=False)
    # The file has no per-player timestamp, but the download says when
    # nflverse last rebuilt it -- shown on the board as "injury data updated".
    with conn:
        conn.execute("INSERT OR REPLACE INTO source_updates (source, updated_at, checked_at) VALUES (?, ?, ?)",
                     (f"injuries_{season}", _last_modified(resp), _now()))
    df = df.drop_duplicates(["season", "week", "team", "gsis_id"], keep="last")
    out = pd.DataFrame({
        "season": df["season"], "week": df["week"], "game_type": df["game_type"],
        "team": df["team"], "gsis_id": df["gsis_id"], "full_name": df["full_name"],
        "position": df["position"], "report_status": df["report_status"],
        "report_injury": df["report_primary_injury"],
        "report_injury2": df["report_secondary_injury"],
        "practice_status": df["practice_status"],
        "practice_injury": df["practice_primary_injury"],
    })
    return _replace(conn, "injury_reports", list(out.columns), _clean(out),
                    "WHERE season = ?", (season,))


def load_roster_status(conn, season: int) -> int:
    df = _csv(WEEKLY_ROSTERS_URL.format(season=season))
    df = df[df["gsis_id"].notna()]
    if season < CURRENT_SEASON:
        # Last season is only needed to link snap counts to players, so keep
        # one row per player: his last week on any roster.
        df = df.sort_values("week").drop_duplicates("gsis_id", keep="last")
    df = df.drop_duplicates(["season", "week", "team", "gsis_id"], keep="last")
    out = pd.DataFrame({
        "season": df["season"], "week": df["week"], "team": df["team"],
        "gsis_id": df["gsis_id"], "pfr_id": df["pfr_id"], "full_name": df["full_name"],
        "position": df["position"], "jersey": df["jersey_number"].astype("Int64"),
        "status": df["status"], "status_code": df["status_description_abbr"],
        "years_exp": df["years_exp"].astype("Int64"),
    })
    return _replace(conn, "roster_status", list(out.columns), _clean(out),
                    "WHERE season = ?", (season,))


def load_snap_counts(conn, season: int) -> int:
    df = _csv(SNAP_COUNTS_URL.format(season=season))
    df = df.drop_duplicates(["season", "week", "team", "pfr_player_id"], keep="last")
    out = pd.DataFrame({
        "season": df["season"], "week": df["week"], "game_type": df["game_type"],
        "team": df["team"], "pfr_player_id": df["pfr_player_id"], "player": df["player"],
        "position": df["position"],
        "offense_snaps": df["offense_snaps"], "offense_pct": df["offense_pct"],
        "defense_snaps": df["defense_snaps"], "defense_pct": df["defense_pct"],
    })
    return _replace(conn, "snap_counts", list(out.columns), _clean(out),
                    "WHERE season = ?", (season,))


def _part(x) -> str:
    return x if isinstance(x, str) else ""


def current_listings(conn, season: int = CURRENT_SEASON) -> dict[tuple[str, str], str]:
    """
    {(team, gsis_id): status_key} for every player currently listed: on the
    team's latest injury report, or on a reserve list (IR, PUP, NFI...) in the
    team's latest weekly roster. A reserve list wins, the same way the board
    shows a reserve-list player as OUT whatever the report says.
    """
    out: dict[tuple[str, str], str] = {}
    for r in conn.execute("""
        SELECT i.team, i.gsis_id, i.week, i.report_status, i.practice_status,
               i.report_injury, i.report_injury2, i.practice_injury
        FROM injury_reports i
        JOIN (SELECT team, MAX(week) AS w FROM injury_reports WHERE season = ? GROUP BY team) m
          ON m.team = i.team AND m.w = i.week
        WHERE i.season = ?
    """, (season, season)):
        out[(r["team"], r["gsis_id"])] = "|".join([
            f"W{r['week']}", _part(r["report_status"]), _part(r["practice_status"]),
            _part(r["report_injury"]), _part(r["report_injury2"]), _part(r["practice_injury"])])
    for r in conn.execute("""
        SELECT s.team, s.gsis_id, s.status_code
        FROM roster_status s
        JOIN (SELECT team, MAX(week) AS w FROM roster_status WHERE season = ? GROUP BY team) m
          ON m.team = s.team AND m.w = s.week
        WHERE s.season = ? AND s.status = 'RES'
    """, (season, season)):
        out[(r["team"], r["gsis_id"])] = f"RES|{_part(r['status_code'])}"
    return out


def merge_status_log(existing: dict, current: dict, now: str, first_run: bool) -> dict:
    """
    Pure merge step of the status tracker (see db.py, injury_status_log).
    existing: {(team, gsis_id): {"status_key", "first_seen_at", "last_seen_at", "tracking_start"}}
    current:  {(team, gsis_id): status_key}
    Returns the new log: unchanged keys keep first_seen_at, changed or new
    keys start at `now`, players no longer listed drop out.
    """
    out = {}
    for k, key in current.items():
        old = existing.get(k)
        if old is not None and old["status_key"] == key:
            out[k] = {**old, "last_seen_at": now}
        else:
            out[k] = {"status_key": key, "first_seen_at": now, "last_seen_at": now,
                      "tracking_start": 1 if first_run else 0}
    return out


def track_injury_status(conn, season: int = CURRENT_SEASON, now: Optional[str] = None) -> int:
    now = now or _now()
    existing = {(r["team"], r["gsis_id"]): dict(r) for r in conn.execute(
        "SELECT team, gsis_id, status_key, first_seen_at, last_seen_at, tracking_start "
        "FROM injury_status_log WHERE season = ?", (season,))}
    new = merge_status_log(existing, current_listings(conn, season), now, first_run=not existing)
    rows = [(season, t, g, v["status_key"], v["first_seen_at"], v["last_seen_at"], v["tracking_start"])
            for (t, g), v in new.items()]
    return _replace(conn, "injury_status_log",
                    ["season", "team", "gsis_id", "status_key", "first_seen_at", "last_seen_at", "tracking_start"],
                    rows, "WHERE season = ?", (season,))


def load_availability(conn) -> dict[str, str]:
    """Run every loader. A missing file (early season) is a warning, not a crash."""
    jobs = [
        ("depth_charts", lambda: load_depth_charts(conn)),
        ("injuries", lambda: load_injuries(conn)),
        (f"roster_status_{CURRENT_SEASON}", lambda: load_roster_status(conn, CURRENT_SEASON)),
        (f"roster_status_{CURRENT_SEASON - 1}", lambda: load_roster_status(conn, CURRENT_SEASON - 1)),
        (f"snap_counts_{CURRENT_SEASON}", lambda: load_snap_counts(conn, CURRENT_SEASON)),
        (f"snap_counts_{CURRENT_SEASON - 1}", lambda: load_snap_counts(conn, CURRENT_SEASON - 1)),
        # Last: compares the freshly loaded injury report and reserve lists
        # with the previous refresh's to timestamp each player's status.
        ("injury_status_log", lambda: track_injury_status(conn)),
    ]
    out = {}
    for name, fn in jobs:
        # Comparing against a stale download would restart (or drop) players'
        # "first seen" times, so only track when both inputs loaded fresh.
        if name == "injury_status_log" and any(
                out[k].startswith("FAILED") for k in ("injuries", f"roster_status_{CURRENT_SEASON}")):
            out[name] = "skipped (an input download failed)"
            continue
        run_id = start_run(conn, f"nflverse_{name}")
        try:
            n = fn()
            finish_run(conn, run_id, "ok", n)
            out[name] = f"{n:,} rows"
        except Exception as exc:
            finish_run(conn, run_id, "error", 0, str(exc))
            out[name] = f"FAILED ({exc.__class__.__name__}: {str(exc)[:80]})"
    return out

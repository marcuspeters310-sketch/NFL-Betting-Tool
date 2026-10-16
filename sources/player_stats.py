"""
nflverse weekly player stats -> player_game_stats, for the player props
history (how often a player cleared a line in past games).

Loads this season and last (config.PROP_HISTORY_SEASONS). Last season is
downloaded once and then kept; this season re-downloads every refresh so new
weeks come in. A failed download is a warning, never a crash.
"""
from __future__ import annotations

import io

import pandas as pd
import requests

from config import CURRENT_SEASON, PLAYER_STATS_URL, PROP_HISTORY_SEASONS
from db import start_run, finish_run

COUNT_COLS = ["completions", "attempts", "passing_yards", "passing_tds", "passing_interceptions",
              "carries", "rushing_yards", "rushing_tds", "receptions", "targets",
              "receiving_yards", "receiving_tds", "special_teams_tds"]
COLS = ["season", "week", "season_type", "gsis_id", "player_name", "position", "team", "opponent"] + COUNT_COLS


def frame(df: pd.DataFrame) -> pd.DataFrame:
    """nflverse columns -> player_game_stats columns (counts as integers, blanks as 0)."""
    df = df[df["player_id"].notna()].drop_duplicates(["season", "week", "player_id"], keep="last")
    out = pd.DataFrame({
        "season": df["season"].astype(int), "week": df["week"].astype(int),
        "season_type": df["season_type"], "gsis_id": df["player_id"],
        "player_name": df["player_display_name"], "position": df["position"],
        "team": df["team"], "opponent": df["opponent_team"],
    })
    for c in COUNT_COLS:
        out[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).round().astype(int) if c in df else 0
    return out


def load_player_stats(conn, force: bool = False) -> dict[int, str]:
    out = {}
    for season in range(CURRENT_SEASON - PROP_HISTORY_SEASONS + 1, CURRENT_SEASON + 1):
        have = conn.execute("SELECT COUNT(*) FROM player_game_stats WHERE season = ?", (season,)).fetchone()[0]
        if season < CURRENT_SEASON and have and not force:
            out[season] = "cached"
            continue
        run_id = start_run(conn, f"nflverse_player_stats_{season}")
        try:
            resp = requests.get(PLAYER_STATS_URL.format(season=season), timeout=300)
            resp.raise_for_status()
            df = frame(pd.read_csv(io.BytesIO(resp.content), compression="gzip", low_memory=False))
            rows = list(df.astype(object).where(pd.notna(df), None).itertuples(index=False, name=None))
            with conn:
                conn.execute("DELETE FROM player_game_stats WHERE season = ?", (season,))
                conn.executemany(f"INSERT INTO player_game_stats ({', '.join(COLS)}) VALUES ({', '.join('?' * len(COLS))})", rows)
            finish_run(conn, run_id, "ok", len(rows))
            out[season] = len(rows)
        except Exception as exc:
            finish_run(conn, run_id, "error", 0, str(exc)[:200])
            out[season] = f"FAILED ({exc.__class__.__name__}: {str(exc)[:80]})"
    return out

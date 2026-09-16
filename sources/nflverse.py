"""
nflverse loader.

nflverse is a volunteer-run project that publishes clean NFL data as flat files
on GitHub. The games file is the one that matters for Screen 1: every game since
1999 with its closing spread, closing total, final score, rest days, roof type,
and kickoff weather. It is free, it needs no API key, and it is the single
highest-value data source in this project.

The whole loader is: download CSV -> select the columns we want -> upsert into
SQLite. Upsert (not append) is what makes it safe to re-run any time.
"""
from __future__ import annotations

import io
from datetime import datetime, timezone

import pandas as pd
import requests

from config import GAMES_CSV_URL, FIRST_SEASON
from db import start_run, finish_run

# The columns we keep, in the same order as the games table.
GAME_COLUMNS = [
    "game_id", "season", "game_type", "week", "gameday", "weekday", "gametime",
    "away_team", "home_team", "away_score", "home_score", "location",
    "result", "total", "overtime", "away_rest", "home_rest",
    "away_moneyline", "home_moneyline",
    "spread_line", "away_spread_odds", "home_spread_odds",
    "total_line", "under_odds", "over_odds",
    "div_game", "roof", "surface", "temp", "wind",
    "away_qb_name", "home_qb_name", "away_coach", "home_coach",
    "referee", "stadium_id", "stadium", "espn",
]


def download_games() -> pd.DataFrame:
    """Fetch the nflverse games CSV and return it as a DataFrame."""
    resp = requests.get(GAMES_CSV_URL, timeout=120)
    resp.raise_for_status()
    df = pd.read_csv(io.StringIO(resp.text), low_memory=False)

    # Keep only columns we declared, and only seasons we care about.
    missing = [c for c in GAME_COLUMNS if c not in df.columns]
    if missing:
        # Loud failure beats silently loading a table with holes in it. If
        # nflverse renames a column, you want to know on the run that broke it.
        raise RuntimeError(f"nflverse games.csv is missing expected columns: {missing}")

    df = df[GAME_COLUMNS].copy()
    df = df[df["season"] >= FIRST_SEASON]

    # pandas uses NaN for missing values; SQLite wants None.
    df = df.astype(object).where(pd.notna(df), None)
    return df


def load_games(conn) -> int:
    """
    Download and upsert every game. Returns the number of rows written.

    ON CONFLICT ... DO UPDATE means re-running this after a stat correction
    overwrites the old row rather than erroring or duplicating. You can run it
    as often as you like.
    """
    run_id = start_run(conn, "nflverse_games")
    try:
        df = download_games()
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")

        cols = GAME_COLUMNS + ["updated_at"]
        placeholders = ", ".join("?" for _ in cols)
        updates = ", ".join(f"{c} = excluded.{c}" for c in cols if c != "game_id")

        sql = (
            f"INSERT INTO games ({', '.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT(game_id) DO UPDATE SET {updates}"
        )

        rows = [tuple(rec) + (now,) for rec in df.itertuples(index=False, name=None)]
        conn.executemany(sql, rows)
        conn.commit()

        finish_run(conn, run_id, "ok", len(rows), f"seasons {df.season.min()}-{df.season.max()}")
        return len(rows)

    except Exception as exc:
        finish_run(conn, run_id, "error", 0, str(exc))
        raise

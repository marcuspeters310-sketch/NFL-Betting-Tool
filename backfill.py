"""
One-time (and safely repeatable) historical load.

Run this once to build the database, then again any time you want the latest
scores and lines:

    python backfill.py

It takes about 10 seconds (about a minute the first time, while it downloads
last season's play-by-play). Nothing is destroyed -- existing rows are updated in
place, so running it after a Sunday pulls in that week's results.
"""
from db import connect, init_schema
import sys

from sources.nflverse import load_games
from sources.pbp import load_pbp
from sources.rosters import load_availability
from sources.weather import load_weather
from config import DB_PATH


def main() -> None:
    print(f"Database: {DB_PATH}")

    conn = connect()
    init_schema(conn)
    print("Schema ready.")

    print("Downloading nflverse games (all seasons since 1999)...")
    n = load_games(conn)
    print(f"Loaded {n:,} games.")

    # Quick summary so you can see it worked without opening the file.
    row = conn.execute("""
        SELECT COUNT(*) AS games,
               MIN(season) AS first_season,
               MAX(season) AS last_season,
               SUM(CASE WHEN result IS NOT NULL THEN 1 ELSE 0 END) AS played,
               SUM(CASE WHEN spread_line IS NOT NULL THEN 1 ELSE 0 END) AS with_spread
        FROM games
    """).fetchone()

    print(
        f"\n  {row['games']:,} games, seasons {row['first_season']}-{row['last_season']}"
        f"\n  {row['played']:,} played"
        f"\n  {row['with_spread']:,} with a closing spread"
    )

    # Play-by-play for team metrics. Last season downloads once (~20 MB);
    # this season re-downloads every run so new weeks come in.
    # Pass --full to force last season to re-download too.
    print("\nLoading play-by-play for team metrics...")
    for season, n in load_pbp(conn, force="--full" in sys.argv).items():
        label = "already loaded" if n == "cached" else (f"{n} team-games" if isinstance(n, int) else n)
        print(f"  {season}: {label}")


    print("\nLoading depth charts, injury reports, roster status, snap counts...")
    for name, msg in load_availability(conn).items():
        print(f"  {name}: {msg}")

    print("\nLoading kickoff weather forecasts (Open-Meteo)...")
    try:
        print(f"  {load_weather(conn)}")
    except Exception as exc:   # weather is a nice-to-have; never block a refresh
        print(f"  FAILED ({exc.__class__.__name__}: {str(exc)[:80]})")

    conn.close()


if __name__ == "__main__":
    main()

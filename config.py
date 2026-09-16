"""
Central configuration. Every other file imports paths and constants from here
so there is exactly one place to change them.
"""
import os
from pathlib import Path

# Resolve paths relative to THIS file, not the working directory, so the
# scripts work the same whether you run them from the project folder,
# from an IDE, or from a scheduled task.
PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"

# The database lives in data/nfl.db by default. Set the NFL_DB_PATH environment
# variable to point somewhere else -- useful for testing against a throwaway
# copy, and required if the project folder ever sits on a network drive
# (SQLite needs real file locking, which network shares often do not provide).
DB_PATH = Path(os.environ.get("NFL_DB_PATH", DATA_DIR / "nfl.db"))

# nflverse publishes one CSV containing every NFL game since 1999 with its
# closing spread, closing total, and final score. This single file is the
# entire factual basis for Screen 1's ATS and over/under records.
GAMES_CSV_URL = "https://github.com/nflverse/nfldata/raw/master/data/games.csv"

# nflverse play-by-play, one file per season. Every play since 1999 with EPA
# (expected points added) and success already calculated. It is big -- about
# 20 MB compressed for a full season -- so we never store it raw. The loader
# boils each season down to one row per team per game (team_game_stats).
PBP_CSV_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/pbp/"
    "play_by_play_{season}.csv.gz"
)

# Rosters, depth charts, injury reports and snap counts (all nflverse).
NFLVERSE_RELEASE = "https://github.com/nflverse/nflverse-data/releases/download"
DEPTH_CHARTS_URL = NFLVERSE_RELEASE + "/depth_charts/depth_charts_{season}.csv.gz"
INJURIES_URL = NFLVERSE_RELEASE + "/injuries/injuries_{season}.csv.gz"
WEEKLY_ROSTERS_URL = NFLVERSE_RELEASE + "/weekly_rosters/roster_weekly_{season}.csv.gz"
SNAP_COUNTS_URL = NFLVERSE_RELEASE + "/snap_counts/snap_counts_{season}.csv.gz"

# Weather: Open-Meteo forecast API (free, no key). Forecasts reach 16 days out.
WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
WEATHER_DAYS_AHEAD = 10          # only fetch games kicking off within this many days
WIND_FLAG_MPH = 15               # flag a game at or above this sustained wind
RAIN_FLAG_PCT = 50               # ... or at or above this chance of precipitation

FIRST_SEASON = 1999          # earliest season nflverse carries betting lines for
CURRENT_SEASON = 2026

# Seasons before roughly 2015 were a meaningfully different sport (rule changes
# around passing and defensive contact). Analysis defaults start here; you can
# always pass an explicit season range to override it.
DEFAULT_ANALYSIS_START = 2015

# Team metrics use last season plus this season. PBP_SEASONS is what the
# loader pulls; a completed season is only downloaded once.
# Two seasons back is loaded too, only so the model spread can be backtested
# on last season (last season's early weeks lean on the season before it).
PBP_SEASONS = [CURRENT_SEASON - 2, CURRENT_SEASON - 1, CURRENT_SEASON]

# How fast this season takes over from last season in the blended ranks.
# A team's current-season share is (games played / BLEND_FULL_AT_GAMES),
# capped at 100%. At 6: one game played = 17%, three = 50%, six or more = 100%.
BLEND_FULL_AT_GAMES = 6

DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

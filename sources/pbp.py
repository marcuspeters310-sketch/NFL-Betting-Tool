"""
nflverse play-by-play loader -> team_game_stats.

A season of play-by-play is ~50,000 rows and 370 columns. We need maybe 25 of
those columns, and we never need individual plays for the board, so this
loader downloads a season, adds it up to one row per team per game, and throws
the plays away. The result is about 570 rows per season.

Which plays count: the standard analytics filter -- play_type 'pass' or 'run'
with an EPA value. That keeps sacks and scrambles (as passes) and drops
kneel-downs, spikes, special teams, two-point tries and plays wiped out by
penalty. It's the same filter the public EPA leaderboards use, so our numbers
should line up with rbsdm.com and similar sites.

Run through backfill.py. A finished season is only downloaded once; the
current season is re-downloaded every time so new weeks and stat corrections
flow in.
"""
from __future__ import annotations

import io
import re
from datetime import datetime, timezone

import pandas as pd
import requests

from config import PBP_CSV_URL, PBP_SEASONS, CURRENT_SEASON
from db import start_run, finish_run

USECOLS = [
    "game_id", "season", "season_type", "week", "posteam", "defteam",
    "play_type", "pass", "rush", "epa", "success", "yards_gained",
    "yardline_100", "third_down_converted", "third_down_failed",
    "interception", "fumble_lost", "sack", "two_point_attempt",
    "fixed_drive", "fixed_drive_result",
    "wp", "half_seconds_remaining", "game_seconds_remaining", "down", "fumble",
    "play_id", "desc",
]

# The NFL's official gamebook charting writes exactly these two phrases into
# the play-by-play "desc" text -- confirmed by sampling real 2025 data. No
# other wording has ever been seen for either event.
#   "ARI-50-C.Simon was injured during the play."
#   "** Injury Update: ARI-25-Z.Collins has returned to the game."
HURT_RE = re.compile(r"([A-Z]{2,3})-(\d{1,2})-([A-Za-z][A-Za-z.'\-]*)\s+was injured during the play")
RETURN_RE = re.compile(r"Injury Update:\s*([A-Z]{2,3})-(\d{1,2})-([A-Za-z][A-Za-z.'\-]*)\s+has returned to the game")

INJURY_COLUMNS = ["game_id", "season", "week", "season_type", "team", "jersey",
                   "short_name", "hurt_play_id", "returned"]

STAT_COLUMNS = [
    "game_id", "team", "opponent", "season", "week", "season_type",
    "plays", "epa_sum", "successes",
    "pass_plays", "pass_epa_sum", "pass_successes",
    "rush_plays", "rush_epa_sum", "rush_successes",
    "yards", "rz_drives", "rz_tds", "third_att", "third_conv",
    "giveaways", "sacks_taken",
    "neutral_plays", "neutral_passes", "neutral_sec_sum", "neutral_sec_n",
    "explosives", "fumbles", "fumbles_lost",
]


def download_pbp(season: int) -> pd.DataFrame:
    url = PBP_CSV_URL.format(season=season)
    resp = requests.get(url, timeout=300)
    if resp.status_code == 404:
        raise FileNotFoundError(f"no play-by-play file yet for {season}")
    resp.raise_for_status()
    # usecols keeps memory small: 21 of ~370 columns.
    return pd.read_csv(io.BytesIO(resp.content), usecols=USECOLS,
                       low_memory=False, compression="gzip")


def summarize(pbp: pd.DataFrame) -> pd.DataFrame:
    """Plays in, one row per (game, offense) out."""
    keys = ["game_id", "posteam"]

    scrim = pbp[
        pbp["play_type"].isin(["pass", "run"])
        & pbp["epa"].notna()
        & pbp["posteam"].notna()
        & (pbp["two_point_attempt"].fillna(0) != 1)
    ].copy()

    scrim["is_pass"] = (scrim["pass"] == 1).astype(int)
    scrim["is_rush"] = 1 - scrim["is_pass"]
    scrim["succ"] = (scrim["epa"] > 0).astype(int)
    scrim["pass_epa"] = scrim["epa"] * scrim["is_pass"]
    scrim["rush_epa"] = scrim["epa"] * scrim["is_rush"]
    scrim["pass_succ"] = scrim["succ"] * scrim["is_pass"]
    scrim["rush_succ"] = scrim["succ"] * scrim["is_rush"]
    scrim["giveaway"] = scrim["interception"].fillna(0) + scrim["fumble_lost"].fillna(0)
    for c in ("third_down_converted", "third_down_failed", "sack", "yards_gained",
              "fumble", "fumble_lost"):
        scrim[c] = scrim[c].fillna(0)

    # Explosive: 20+ yard pass or 10+ yard run.
    scrim["explosive"] = (
        ((scrim["is_pass"] == 1) & (scrim["yards_gained"] >= 20))
        | ((scrim["is_rush"] == 1) & (scrim["yards_gained"] >= 10))
    ).astype(int)

    # Neutral situations.
    neutral = (scrim["wp"].between(0.2, 0.8)) & (scrim["half_seconds_remaining"] > 120)
    early = scrim["down"].isin([1, 2])
    scrim["n_play"] = (neutral & early).astype(int)
    scrim["n_pass"] = scrim["n_play"] * scrim["is_pass"]

    # Pace: seconds from this snap to the next snap by the same offense on the
    # same drive. Big gaps (timeouts, injuries, quarter breaks) are dropped.
    scrim = scrim.sort_values(["game_id", "play_id"])
    nxt = scrim.groupby(["game_id", "posteam", "fixed_drive"])["game_seconds_remaining"].shift(-1)
    gap = scrim["game_seconds_remaining"] - nxt
    ok = neutral & gap.between(1, 60)
    scrim["sec"] = gap.where(ok, 0.0)
    scrim["sec_n"] = ok.astype(int)

    g = scrim.groupby(keys).agg(
        opponent=("defteam", "first"),
        season=("season", "first"),
        week=("week", "first"),
        season_type=("season_type", "first"),
        plays=("epa", "size"),
        epa_sum=("epa", "sum"),
        successes=("succ", "sum"),
        pass_plays=("is_pass", "sum"),
        pass_epa_sum=("pass_epa", "sum"),
        pass_successes=("pass_succ", "sum"),
        rush_plays=("is_rush", "sum"),
        rush_epa_sum=("rush_epa", "sum"),
        rush_successes=("rush_succ", "sum"),
        yards=("yards_gained", "sum"),
        third_conv=("third_down_converted", "sum"),
        third_fail=("third_down_failed", "sum"),
        giveaways=("giveaway", "sum"),
        sacks_taken=("sack", "sum"),
        neutral_plays=("n_play", "sum"),
        neutral_passes=("n_pass", "sum"),
        neutral_sec_sum=("sec", "sum"),
        neutral_sec_n=("sec_n", "sum"),
        explosives=("explosive", "sum"),
        fumbles=("fumble", "sum"),
        fumbles_lost=("fumble_lost", "sum"),
    ).reset_index()
    g["third_att"] = g["third_conv"] + g["third_fail"]

    # Red zone: a drive counts once if it ran any scrimmage play or field goal
    # try at the opponent's 20 or closer. It scores if the drive ended in a TD.
    rz = pbp[
        pbp["play_type"].isin(["pass", "run", "field_goal"])
        & (pbp["yardline_100"] <= 20)
        & pbp["posteam"].notna()
    ]
    drives = rz.groupby(keys + ["fixed_drive"]).agg(result=("fixed_drive_result", "first")).reset_index()
    drives["td"] = (drives["result"] == "Touchdown").astype(int)
    rzg = drives.groupby(keys).agg(rz_drives=("td", "size"), rz_tds=("td", "sum")).reset_index()

    g = g.merge(rzg, on=keys, how="left")
    g[["rz_drives", "rz_tds"]] = g[["rz_drives", "rz_tds"]].fillna(0)
    g = g.rename(columns={"posteam": "team"})

    int_cols = ["plays", "successes", "pass_plays", "pass_successes", "rush_plays",
                "rush_successes", "yards", "rz_drives", "rz_tds", "third_att",
                "third_conv", "giveaways", "sacks_taken", "season", "week",
                "neutral_plays", "neutral_passes", "neutral_sec_n", "explosives",
                "fumbles", "fumbles_lost"]
    g[int_cols] = g[int_cols].astype(int)
    return g[STAT_COLUMNS]


def parse_injuries(pbp: pd.DataFrame) -> pd.DataFrame:
    """
    Every "was injured during the play" mention, one row per (game, team,
    jersey): the play_id of their LAST such mention that game, and whether a
    later "has returned to the game" line followed it. Empty frame (right
    columns, no rows) if the season has none -- rare, but a slow week happens.
    """
    sub = pbp[["game_id", "season", "week", "season_type", "play_id", "desc"]].dropna(subset=["desc"])

    hurt_rows, return_rows = [], []
    for game_id, season, week, season_type, play_id, desc in sub.itertuples(index=False, name=None):
        for team, jersey, name in HURT_RE.findall(desc):
            hurt_rows.append((game_id, season, week, season_type, team, int(jersey), name, play_id))
        for team, jersey, name in RETURN_RE.findall(desc):
            return_rows.append((game_id, team, int(jersey), play_id))

    if not hurt_rows:
        return pd.DataFrame(columns=INJURY_COLUMNS)

    hurt = pd.DataFrame(hurt_rows, columns=["game_id", "season", "week", "season_type",
                                             "team", "jersey", "short_name", "play_id"])
    # A player can go down more than once; keep only their last exit that game.
    hurt = (hurt.sort_values("play_id")
                .groupby(["game_id", "team", "jersey"], as_index=False).last()
                .rename(columns={"play_id": "hurt_play_id"}))

    ret = pd.DataFrame(return_rows, columns=["game_id", "team", "jersey", "play_id"])

    def came_back(row) -> int:
        if ret.empty:
            return 0
        m = ret[(ret["game_id"] == row["game_id"]) & (ret["team"] == row["team"])
                & (ret["jersey"] == row["jersey"]) & (ret["play_id"] > row["hurt_play_id"])]
        return int(len(m) > 0)

    hurt["returned"] = hurt.apply(came_back, axis=1)
    return hurt[INJURY_COLUMNS]


def season_loaded(conn, season: int) -> bool:
    """True if the season is loaded with every current column filled in.
    Rows from before a column was added count as not loaded, so they refresh."""
    r = conn.execute(
        "SELECT COUNT(*) AS n, SUM(neutral_plays IS NULL) AS stale "
        "FROM team_game_stats WHERE season = ?", (season,)
    ).fetchone()
    return r["n"] > 0 and not r["stale"]


def load_season(conn, season: int) -> int:
    """Replace one season's rows. Delete + insert in one transaction, so a
    failed download never leaves a half-loaded season behind."""
    pbp = download_pbp(season)
    stats = summarize(pbp)
    injuries = parse_injuries(pbp)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    cols = STAT_COLUMNS + ["updated_at"]
    rows = [tuple(r) + (now,) for r in stats.astype(object).itertuples(index=False, name=None)]

    icols = INJURY_COLUMNS + ["updated_at"]
    irows = [tuple(r) + (now,) for r in injuries.astype(object).itertuples(index=False, name=None)]

    with conn:
        conn.execute("DELETE FROM team_game_stats WHERE season = ?", (season,))
        conn.executemany(
            f"INSERT INTO team_game_stats ({', '.join(cols)}) "
            f"VALUES ({', '.join('?' for _ in cols)})",
            rows,
        )
        conn.execute("DELETE FROM game_injury_events WHERE season = ?", (season,))
        if irows:
            conn.executemany(
                f"INSERT INTO game_injury_events ({', '.join(icols)}) "
                f"VALUES ({', '.join('?' for _ in icols)})",
                irows,
            )
    return len(rows)


def load_pbp(conn, force: bool = False) -> dict[int, int | str]:
    """Load every season in PBP_SEASONS. Returns {season: rows or 'cached'}."""
    out: dict[int, int | str] = {}
    for season in PBP_SEASONS:
        if season < CURRENT_SEASON and season_loaded(conn, season) and not force:
            out[season] = "cached"
            continue
        run_id = start_run(conn, f"nflverse_pbp_{season}")
        try:
            n = load_season(conn, season)
            finish_run(conn, run_id, "ok", n, f"season {season}")
            out[season] = n
        except Exception as exc:
            finish_run(conn, run_id, "error", 0, str(exc))
            # The current season's file doesn't exist until Week 1 is played.
            # That's expected in the preseason, so warn instead of crashing.
            if season == CURRENT_SEASON:
                out[season] = f"not available ({exc.__class__.__name__})"
            else:
                raise
    return out

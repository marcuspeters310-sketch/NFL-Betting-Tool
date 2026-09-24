"""
Database connection and schema.

Design notes, since you'll be reading this rather than writing it:

1. Raw columns are stored exactly as nflverse publishes them. Nothing derived
   is stored. All the ATS / over-under logic lives in SQL VIEWS defined below.
   That means if nflverse issues a stat correction, you re-run the loader and
   every derived number updates automatically -- there is no second copy of the
   truth to fall out of sync.

2. The key view is v_team_games. The raw data has one row per GAME (with a home
   team and an away team). Almost every question you want to ask is about a
   TEAM ("how are the Bears doing ATS on the road"). v_team_games flips each
   game into two rows, one per team, with everything already stated from that
   team's point of view. After that, every team query is a plain WHERE clause
   instead of a pile of CASE statements.
"""
import sqlite3
from datetime import datetime, timezone

from config import DB_PATH


def connect(read_only: bool = False) -> sqlite3.Connection:
    """
    Open the database. Pass read_only=True for anything that only queries --
    especially the Claude-generated SQL in Screen 3 later on.
    """
    if read_only:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, check_same_thread=False)
    else:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    # Return rows that behave like dicts (row["home_team"]) instead of tuples.
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


SCHEMA = """
-- ---------------------------------------------------------------------------
-- games: one row per NFL game, 1999 to present. Raw nflverse fields only.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS games (
    game_id           TEXT PRIMARY KEY,   -- e.g. '2025_01_DAL_PHI'
    season            INTEGER NOT NULL,
    game_type         TEXT,               -- REG, WC, DIV, CON, SB
    week              INTEGER,
    gameday           TEXT,               -- 'YYYY-MM-DD'
    weekday           TEXT,
    gametime          TEXT,               -- 'HH:MM' local kickoff, 24-hour
    away_team         TEXT,
    home_team         TEXT,
    away_score        INTEGER,
    home_score        INTEGER,
    location          TEXT,               -- 'Home' or 'Neutral'
    result            INTEGER,            -- home_score - away_score
    total             INTEGER,            -- home_score + away_score
    overtime          INTEGER,
    away_rest         INTEGER,            -- days since that team's last game
    home_rest         INTEGER,
    away_moneyline    INTEGER,
    home_moneyline    INTEGER,
    spread_line       REAL,               -- CLOSING spread. POSITIVE = home favored.
    away_spread_odds  INTEGER,
    home_spread_odds  INTEGER,
    total_line        REAL,               -- CLOSING total
    under_odds        INTEGER,
    over_odds         INTEGER,
    div_game          INTEGER,            -- 1 if divisional matchup
    roof              TEXT,               -- outdoors, dome, closed, open
    surface           TEXT,
    temp              REAL,               -- kickoff temp, outdoor games only
    wind              REAL,               -- kickoff wind mph, outdoor games only
    away_qb_name      TEXT,
    home_qb_name      TEXT,
    away_coach        TEXT,
    home_coach        TEXT,
    referee           TEXT,
    stadium_id        TEXT,
    stadium           TEXT,
    espn              TEXT,               -- ESPN event id, used later for injuries
    updated_at        TEXT                -- when this row was last written
);

CREATE INDEX IF NOT EXISTS idx_games_season      ON games(season);
CREATE INDEX IF NOT EXISTS idx_games_home        ON games(home_team, season);
CREATE INDEX IF NOT EXISTS idx_games_away        ON games(away_team, season);
CREATE INDEX IF NOT EXISTS idx_games_gameday     ON games(gameday);

-- ---------------------------------------------------------------------------
-- team_game_stats: one row per team per game, OFFENSE only, built from
-- nflverse play-by-play. A team's defensive numbers are simply its opponent's
-- row for the same game, so they are never stored twice.
--
-- Only counts and sums are stored (plays, total EPA, conversions...). Every
-- rate -- EPA per play, success rate, red zone TD % -- is calculated at query
-- time, so combining games or seasons is always correct: you add the counts,
-- then divide, instead of averaging averages.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS team_game_stats (
    game_id       TEXT NOT NULL,
    team          TEXT NOT NULL,      -- the offense
    opponent      TEXT NOT NULL,      -- the defense it faced
    season        INTEGER NOT NULL,
    week          INTEGER NOT NULL,
    season_type   TEXT NOT NULL,      -- REG or POST
    plays         INTEGER,            -- dropbacks + designed runs with an EPA value
    epa_sum       REAL,
    successes     INTEGER,            -- plays with EPA > 0
    pass_plays    INTEGER,            -- includes sacks and scrambles
    pass_epa_sum  REAL,
    pass_successes INTEGER,
    rush_plays    INTEGER,
    rush_epa_sum  REAL,
    rush_successes INTEGER,
    yards         INTEGER,            -- net yards on those plays (sacks count against)
    rz_drives     INTEGER,            -- drives that ran a play at the opponent's 20 or closer
    rz_tds        INTEGER,            -- ...of which ended in a touchdown
    third_att     INTEGER,
    third_conv    INTEGER,
    giveaways     INTEGER,            -- interceptions thrown + fumbles lost, offense only
    sacks_taken   INTEGER,
    -- Pace and style (added Sept 2026). "Neutral" = win probability 20-80%,
    -- outside the last two minutes of each half: game script isn't forcing
    -- the offense's hand, so these show what a team chooses to do.
    neutral_plays     INTEGER,        -- neutral 1st/2nd-down plays
    neutral_passes    INTEGER,        -- ...of which were dropbacks
    neutral_sec_sum   REAL,           -- seconds between consecutive neutral snaps, same drive
    neutral_sec_n     INTEGER,
    explosives        INTEGER,        -- passes of 20+ yards, runs of 10+
    fumbles           INTEGER,        -- offense fumbles on scrimmage plays (lost or not)
    fumbles_lost      INTEGER,
    updated_at    TEXT,
    PRIMARY KEY (game_id, team)
);

CREATE INDEX IF NOT EXISTS idx_tgs_season ON team_game_stats(season, week);

-- ---------------------------------------------------------------------------
-- Player availability. All four tables are replaced wholesale on each load.
--
-- depth_chart: the latest daily depth chart snapshot for every team. One row
--   per player per slot; pos_rank 1 is the starter, 2 the next man up. Used
--   for year-over-year "who's new" comparisons (queries.roster_changes) --
--   NOT for the injury-status board, which uses depth_chart_opening below.
-- depth_chart_opening: the SAME shape, frozen at the first snapshot on/after
--   that season's roster cutdown (see sources/rosters.py::_opening_day_anchor).
--   This is what the availability board displays: the roster as it stood
--   going into the season, so a Week 1 starter who's since been hurt, cut or
--   replaced still shows up in their opening slot -- current status (hurt,
--   off the roster, etc.) is overlaid on top of this fixed structure, rather
--   than the board silently "moving on" to whoever plays that slot now.
-- injury_reports: the official Wed-Fri report, final version per week.
-- roster_status: weekly roster status -- this is where injured reserve and
--   other long-term lists live (they are not on the weekly injury report).
-- snap_counts: share of snaps each player was on the field, per game. Used to
--   tell a 95% starter from a 10% rotational player.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS depth_chart (
    team         TEXT NOT NULL,
    side         TEXT NOT NULL,      -- 'O', 'D', 'ST'
    formation    TEXT,               -- e.g. '3WR 1TE', 'Base 4-3 D'
    pos_abb      TEXT NOT NULL,      -- QB, LT, WR, LDE, NB ...
    pos_slot     INTEGER,            -- display order of the position within the formation
    pos_rank     INTEGER NOT NULL,   -- 1 = starter
    player_name  TEXT,
    gsis_id      TEXT,
    espn_id      TEXT,
    snapshot_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_dc_team ON depth_chart(team, side);

CREATE TABLE IF NOT EXISTS depth_chart_opening (
    team         TEXT NOT NULL,
    side         TEXT NOT NULL,      -- 'O', 'D', 'ST'
    formation    TEXT,               -- e.g. '3WR 1TE', 'Base 4-3 D'
    pos_abb      TEXT NOT NULL,      -- QB, LT, WR, LDE, NB ...
    pos_slot     INTEGER,            -- display order of the position within the formation
    pos_rank     INTEGER NOT NULL,   -- 1 = starter
    player_name  TEXT,
    gsis_id      TEXT,
    espn_id      TEXT,
    snapshot_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_dco_team ON depth_chart_opening(team, side);

CREATE TABLE IF NOT EXISTS injury_reports (
    season           INTEGER NOT NULL,
    week             INTEGER NOT NULL,
    game_type        TEXT,
    team             TEXT NOT NULL,
    gsis_id          TEXT NOT NULL,
    full_name        TEXT,
    position         TEXT,
    report_status    TEXT,           -- Out, Doubtful, Questionable, or NULL (practiced, no designation)
    report_injury    TEXT,
    report_injury2   TEXT,
    practice_status  TEXT,           -- final practice day of the week
    practice_injury  TEXT,
    PRIMARY KEY (season, week, team, gsis_id)
);

CREATE TABLE IF NOT EXISTS roster_status (
    season       INTEGER NOT NULL,
    week         INTEGER NOT NULL,
    team         TEXT NOT NULL,
    gsis_id      TEXT NOT NULL,
    pfr_id       TEXT,
    full_name    TEXT,
    position     TEXT,
    jersey       INTEGER,
    status       TEXT,               -- ACT, RES (reserve lists), INA, DEV (practice squad) ...
    status_code  TEXT,               -- finer NFL code, e.g. R01
    years_exp    INTEGER,
    PRIMARY KEY (season, week, team, gsis_id)
);

CREATE TABLE IF NOT EXISTS snap_counts (
    season          INTEGER NOT NULL,
    week            INTEGER NOT NULL,
    game_type       TEXT,
    team            TEXT NOT NULL,
    pfr_player_id   TEXT NOT NULL,
    player          TEXT,
    position        TEXT,
    offense_snaps   INTEGER,
    offense_pct     REAL,            -- 0-1
    defense_snaps   INTEGER,
    defense_pct     REAL,
    PRIMARY KEY (season, week, team, pfr_player_id)
);

-- ---------------------------------------------------------------------------
-- ingest_runs: every loader run logs itself here. This is what will drive the
-- "last refreshed" indicator on the screens, and how you find out that a
-- scheduled job has been quietly failing since week 3.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ingest_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_name    TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT,               -- 'running' | 'ok' | 'error'
    rows_written INTEGER,
    message     TEXT
);

CREATE INDEX IF NOT EXISTS idx_runs_job ON ingest_runs(job_name, started_at DESC);

-- ---------------------------------------------------------------------------
-- line_snapshots: EMPTY until Phase 2, created now on purpose.
--
-- Odds get APPENDED here, never overwritten. Historical odds cost 10x normal
-- on The Odds API, so every snapshot you take is data you'd otherwise have to
-- buy. Starting this table in week one is the single cheapest good decision in
-- the project.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS line_snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    captured_at   TEXT NOT NULL,      -- UTC ISO timestamp of the fetch
    game_id       TEXT,               -- our game_id once matched
    odds_api_id   TEXT,               -- The Odds API's event id
    commence_time TEXT,
    home_team     TEXT,
    away_team     TEXT,
    bookmaker     TEXT NOT NULL,
    market        TEXT NOT NULL,      -- 'spreads' | 'totals' | 'h2h'
    outcome_name  TEXT,               -- team name, or 'Over' / 'Under'
    point         REAL,               -- the spread or total
    price         INTEGER             -- American odds
);

CREATE INDEX IF NOT EXISTS idx_snap_game ON line_snapshots(game_id, market, captured_at);

-- ---------------------------------------------------------------------------
-- game_injury_events: in-game injuries parsed from the play-by-play text.
-- nflverse's official injury report only exists Wed-Fri, so from the end of
-- a game until then, this is the only free signal that a player got hurt.
-- The NFL's own gamebook charting writes two exact phrases into the "desc"
-- column of every play: "TEAM-##-Name was injured during the play." and,
-- later in the same game if they came back, "** Injury Update: TEAM-##-Name
-- has returned to the game." sources/pbp.py regexes those out of the same
-- play-by-play download already used for team_game_stats -- no extra fetch.
-- One row per player per game: their LAST injury mention that game, and
-- whether a later return line followed it. No return line is the strongest
-- signal (players who returned mid-game usually aren't hurt badly enough to
-- change next week's report; one who never shows a return line might be).
-- qtr / game_clock / notes are read straight off that same play: notes is
-- the play's full "desc" text, which already narrates what happened before
-- the injury phrase ("(Shotgun) T.Tagovailoa pass short right to J.Waddle to
-- MIA 45 for 12 yards (J.Smith). ARI-50-C.Simon was injured during the
-- play."), so there's no second lookup for context.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS game_injury_events (
    game_id       TEXT NOT NULL,
    season        INTEGER NOT NULL,
    week          INTEGER NOT NULL,
    season_type   TEXT,
    team          TEXT NOT NULL,      -- 3-letter code as written in the play text
    jersey        INTEGER NOT NULL,
    short_name    TEXT,               -- e.g. "C.Simon", as charted -- not a full roster name
    hurt_play_id  INTEGER,            -- play_id of their last "was injured" mention
    returned      INTEGER,            -- 1 if a later "has returned to the game" followed
    qtr           INTEGER,            -- quarter of the injury play (5 = OT)
    game_clock    TEXT,               -- game clock at that play, "MM:SS"
    notes         TEXT,               -- that play's full charted description
    updated_at    TEXT,
    PRIMARY KEY (game_id, team, jersey)
);
CREATE INDEX IF NOT EXISTS idx_gie_team_week ON game_injury_events(team, season, week);

-- ---------------------------------------------------------------------------
-- weather_forecasts: the latest kickoff-hour forecast for upcoming outdoor
-- (and retractable-roof) games, from Open-Meteo (free, no key). One row per
-- game, replaced on every refresh. Indoor games get no row.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS weather_forecasts (
    game_id       TEXT PRIMARY KEY,
    fetched_at    TEXT NOT NULL,      -- UTC ISO timestamp of the fetch
    kickoff_utc   TEXT,               -- the forecast hour used
    temp_f        REAL,
    wind_mph      REAL,
    gust_mph      REAL,
    precip_pct    REAL,               -- chance of precipitation, 0-100
    source        TEXT
);
"""

# ---------------------------------------------------------------------------
# Derived views. Dropped and recreated on every init so edits here take effect
# immediately -- views hold no data, so this is free.
# ---------------------------------------------------------------------------
VIEWS = """
DROP VIEW IF EXISTS v_team_games;
DROP VIEW IF EXISTS v_games_ats;

-- One row per game, with the betting outcomes worked out.
CREATE VIEW v_games_ats AS
SELECT
    g.*,

    -- How much the home team beat the spread by.
    -- spread_line is positive when the home team is favored, and result is the
    -- home team's margin, so the two are on the same scale and subtract cleanly.
    --   > 0  home covered
    --   = 0  push
    --   < 0  away covered
    CASE WHEN g.result IS NULL OR g.spread_line IS NULL
         THEN NULL ELSE g.result - g.spread_line END          AS home_ats_margin,

    CASE
        WHEN g.result IS NULL OR g.spread_line IS NULL THEN NULL
        WHEN g.result - g.spread_line > 0 THEN 'W'
        WHEN g.result - g.spread_line < 0 THEN 'L'
        ELSE 'P'
    END                                                        AS home_ats,

    CASE
        WHEN g.result IS NULL OR g.spread_line IS NULL THEN NULL
        WHEN g.result - g.spread_line > 0 THEN 'L'
        WHEN g.result - g.spread_line < 0 THEN 'W'
        ELSE 'P'
    END                                                        AS away_ats,

    CASE
        WHEN g.total IS NULL OR g.total_line IS NULL THEN NULL
        WHEN g.total > g.total_line THEN 'OVER'
        WHEN g.total < g.total_line THEN 'UNDER'
        ELSE 'PUSH'
    END                                                        AS ou_result,

    CASE WHEN g.spread_line IS NULL THEN NULL
         WHEN g.spread_line > 0 THEN 1
         WHEN g.spread_line < 0 THEN 0
         ELSE NULL END                                         AS home_is_favorite,

    -- Domes and closed roofs mean weather is irrelevant.
    CASE WHEN g.roof IN ('dome', 'closed') THEN 1 ELSE 0 END   AS is_indoor,

    -- Heuristic: local kickoff at or after 19:00. Catches SNF/MNF/TNF and the
    -- occasional late-window game. Good enough for splits, not exact.
    CASE WHEN g.gametime >= '19:00' THEN 1 ELSE 0 END          AS is_primetime,

    CAST(substr(g.gameday, 6, 2) AS INTEGER)                   AS month
FROM games g;


-- Two rows per game -- one per team -- with everything already flipped to that
-- team's perspective. This is the view almost every query should use.
CREATE VIEW v_team_games AS
SELECT
    game_id, season, week, game_type, gameday, gametime, month,
    home_team           AS team,
    away_team           AS opponent,
    'home'              AS side,
    home_score          AS points_for,
    away_score          AS points_against,
    result              AS margin,          -- this team's margin of victory
    spread_line         AS team_spread,     -- positive = THIS team favored
    home_ats_margin     AS ats_margin,
    home_ats            AS ats,
    home_is_favorite    AS is_favorite,
    ou_result, total_line, total,
    div_game, is_indoor, is_primetime,
    home_rest           AS rest,
    temp, wind, roof, location, overtime
FROM v_games_ats

UNION ALL

SELECT
    game_id, season, week, game_type, gameday, gametime, month,
    away_team, home_team, 'away',
    away_score, home_score,
    CASE WHEN result IS NULL THEN NULL ELSE -result END,
    CASE WHEN spread_line IS NULL THEN NULL ELSE -spread_line END,
    CASE WHEN home_ats_margin IS NULL THEN NULL ELSE -home_ats_margin END,
    away_ats,
    CASE WHEN home_is_favorite IS NULL THEN NULL ELSE 1 - home_is_favorite END,
    ou_result, total_line, total,
    div_game, is_indoor, is_primetime,
    away_rest,
    temp, wind, roof, location, overtime
FROM v_games_ats;
"""


# Columns added after a table first shipped. CREATE TABLE IF NOT EXISTS won't
# add them to an existing database, so init_schema adds any that are missing.
MIGRATIONS = {
    "team_game_stats": [
        ("neutral_plays", "INTEGER"), ("neutral_passes", "INTEGER"),
        ("neutral_sec_sum", "REAL"), ("neutral_sec_n", "INTEGER"),
        ("explosives", "INTEGER"), ("fumbles", "INTEGER"), ("fumbles_lost", "INTEGER"),
    ],
    "game_injury_events": [
        ("qtr", "INTEGER"), ("game_clock", "TEXT"), ("notes", "TEXT"),
    ],
}


def init_schema(conn: sqlite3.Connection) -> None:
    """Create tables and (re)create views. Safe to run repeatedly."""
    conn.executescript(SCHEMA)
    for table, cols in MIGRATIONS.items():
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, typ in cols:
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {typ}")
    conn.executescript(VIEWS)
    conn.commit()


# ---------------------------------------------------------------------------
# Run logging
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def start_run(conn: sqlite3.Connection, job_name: str) -> int:
    cur = conn.execute(
        "INSERT INTO ingest_runs (job_name, started_at, status) VALUES (?, ?, 'running')",
        (job_name, _now()),
    )
    conn.commit()
    return cur.lastrowid


def finish_run(conn, run_id: int, status: str, rows: int = 0, message: str = "") -> None:
    conn.execute(
        "UPDATE ingest_runs SET finished_at = ?, status = ?, rows_written = ?, message = ? WHERE id = ?",
        (_now(), status, rows, message[:500], run_id),
    )
    conn.commit()

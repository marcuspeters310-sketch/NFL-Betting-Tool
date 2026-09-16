"""
The query layer.

Every number any screen displays should come from a function in this file. The
Streamlit UI will call these; so will the phone version later. Keeping the SQL
here rather than inside the UI is what makes swapping the frontend a swap
instead of a rewrite.

House rule for this file: NO function returns a percentage without also
returning n. A 62% ATS record over 26 games and a 62% ATS record over 400 games
are completely different claims, and the screen must be able to say which one
it is showing you.
"""
from __future__ import annotations

import math
from typing import Optional

import pandas as pd

from config import DEFAULT_ANALYSIS_START, CURRENT_SEASON, BLEND_FULL_AT_GAMES
from db import connect


# ---------------------------------------------------------------------------
# Statistical honesty helpers
# ---------------------------------------------------------------------------

def margin_of_error(wins: int, losses: int) -> Optional[float]:
    """
    95% margin of error on a win rate, in percentage points.

    Why this matters: betting records are small samples of near-coin-flips.
    Three seasons of home games is about 26 results. At n=26 the margin of error
    is roughly +/- 19 points, so a 62% record is statistically indistinguishable
    from a coin flip. Without this number next to it, that 62% reads as a trend.
    """
    n = wins + losses
    if n == 0:
        return None
    p = wins / n
    return 1.96 * math.sqrt(p * (1 - p) / n) * 100


MIN_SIGNAL_N = 10


def _s(x) -> Optional[str]:
    """A string, or None. Blank values arrive as None or NaN depending on the
    pandas version, and NaN is truthy -- so always go through this."""
    return x if isinstance(x, str) else None


def _summarize(wins: int, losses: int, pushes: int) -> dict:
    """Turn a W/L/P count into the record dict every caller gets back."""
    decided = wins + losses
    pct = (wins / decided * 100) if decided else None
    moe = margin_of_error(wins, losses)

    # Is this record actually distinguishable from 50/50 at 95% confidence?
    # Almost always the answer is no, which is the point. Below MIN_SIGNAL_N
    # games the margin-of-error formula breaks down (a 1-0 record has a margin
    # of zero), so nothing that small can be a signal.
    meaningful = bool(pct is not None and moe is not None and decided >= MIN_SIGNAL_N
                      and abs(pct - 50) > moe)

    return {
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "n": decided,                 # pushes excluded -- they return your stake
        "n_including_pushes": decided + pushes,
        "record": f"{wins}-{losses}" + (f"-{pushes}" if pushes else ""),
        "pct": round(pct, 1) if pct is not None else None,
        "moe": round(moe, 1) if moe is not None else None,
        "beats_coinflip": meaningful,
    }


def _season_bounds(n_seasons: int, through_season: int) -> tuple[int, int]:
    """Turn 'last 3 seasons' into an inclusive (start, end) season pair."""
    return through_season - n_seasons + 1, through_season


# ---------------------------------------------------------------------------
# ATS records
# ---------------------------------------------------------------------------

def ats_record(
    team: str,
    n_seasons: int = 3,
    through_season: int = CURRENT_SEASON,
    side: Optional[str] = None,          # None | 'home' | 'away'
    regular_season_only: bool = True,
    conn=None,
) -> dict:
    """
    A team's against-the-spread record.

    side=None gives all games, 'home' and 'away' give the splits you asked for
    on Screen 1. Pushes are reported but excluded from the percentage, which is
    how sportsbooks and every record you'll see elsewhere count them.
    """
    close = conn is None
    conn = conn or connect(read_only=True)
    start, end = _season_bounds(n_seasons, through_season)

    sql = """
        SELECT ats, COUNT(*) AS n
        FROM v_team_games
        WHERE team = ?
          AND season BETWEEN ? AND ?
          AND ats IS NOT NULL
    """
    params: list = [team, start, end]

    if side:
        sql += " AND side = ?"
        params.append(side)
    if regular_season_only:
        sql += " AND game_type = 'REG'"

    sql += " GROUP BY ats"

    counts = {r["ats"]: r["n"] for r in conn.execute(sql, params)}
    if close:
        conn.close()

    out = _summarize(counts.get("W", 0), counts.get("L", 0), counts.get("P", 0))
    out.update({"team": team, "side": side or "all", "seasons": f"{start}-{end}"})
    return out


def ou_record(
    team: str,
    n_seasons: int = 3,
    through_season: int = CURRENT_SEASON,
    side: Optional[str] = None,
    month: Optional[int] = None,
    regular_season_only: bool = True,
    conn=None,
) -> dict:
    """
    A team's over/under record. 'wins' here means OVER, 'losses' means UNDER --
    so a low pct is an under team.

    The month argument is there because it is exactly the question you gave as
    your Screen 3 example ("the Patriots against the under in November").
    """
    close = conn is None
    conn = conn or connect(read_only=True)
    start, end = _season_bounds(n_seasons, through_season)

    sql = """
        SELECT ou_result, COUNT(*) AS n
        FROM v_team_games
        WHERE team = ?
          AND season BETWEEN ? AND ?
          AND ou_result IS NOT NULL
    """
    params: list = [team, start, end]

    if side:
        sql += " AND side = ?"
        params.append(side)
    if month:
        sql += " AND month = ?"
        params.append(month)
    if regular_season_only:
        sql += " AND game_type = 'REG'"

    sql += " GROUP BY ou_result"

    counts = {r["ou_result"]: r["n"] for r in conn.execute(sql, params)}
    if close:
        conn.close()

    out = _summarize(counts.get("OVER", 0), counts.get("UNDER", 0), counts.get("PUSH", 0))
    out.update({
        "team": team,
        "overs": out["wins"],
        "unders": out["losses"],
        "over_pct": out["pct"],
        "seasons": f"{start}-{end}",
        "month": month,
    })
    return out


def ats_splits(
    team: str,
    n_seasons: int = 3,
    through_season: int = CURRENT_SEASON,
    conn=None,
) -> pd.DataFrame:
    """
    The Screen 1 splits table: one row per situation, each with its own sample
    size, margin of error, and how far the team beat or missed the spread.

    Read the 'n' column first and the 'pct' column second. Most of these rows
    will have n under 30, and the 'meaningful' column will be False for nearly
    all of them. That is not a bug in the data -- it is what three seasons of
    NFL games actually supports.

    avg_margin / median_margin: points per game by which they beat (+) or
    missed (-) the spread. A 9-8 team at +4.0 average won its covers big; a
    9-8 team at -0.5 was a coin flip that landed well.
    """
    close = conn is None
    conn = conn or connect(read_only=True)
    start, end = _season_bounds(n_seasons, through_season)
    g = _team_games(conn, team, since_season=start)
    if close:
        conn.close()
    g = g[(g["season"] <= end) & (g["game_type"] == "REG")]

    spread = g["team_spread"].abs()
    splits = {
        "Overall":          g,
        "Home":             g[g["side"] == "home"],
        "Away":             g[g["side"] == "away"],
        "As favorite":      g[g["is_favorite"] == 1],
        "As underdog":      g[g["is_favorite"] == 0],
        "Home favorite":    g[(g["side"] == "home") & (g["is_favorite"] == 1)],
        "Road underdog":    g[(g["side"] == "away") & (g["is_favorite"] == 0)],
        "Division games":   g[g["div_game"] == 1],
        "Non-division":     g[g["div_game"] == 0],
        "Primetime":        g[g["is_primetime"] == 1],
        "Outdoors":         g[g["is_indoor"] == 0],
        "Indoors":          g[g["is_indoor"] == 1],
        "Spread within 3":  g[spread <= 3],
        "Spread 3.5 to 7":  g[(spread > 3) & (spread <= 7)],
        "Spread over 7":    g[spread > 7],
        "Short rest (<7d)": g[g["rest"] < 7],
        "Off a bye (>9d)":  g[g["rest"] > 9],
    }

    rows = []
    for label, df in splits.items():
        s = summarize_games(df)
        rows.append({
            "split": label,
            "record": s["record"],
            "n": s["n"],
            "pct": s["pct"],
            "moe": s["moe"],
            "meaningful": s["sig"],
            "avg_margin": s["avg_margin"],
            "median_margin": s["median_margin"],
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Slate
# ---------------------------------------------------------------------------

def week_slate(
    season: int = CURRENT_SEASON,
    week: Optional[int] = None,
    conn=None,
) -> pd.DataFrame:
    """
    The games for one week, in kickoff order -- the spine of Screen 1.

    If week is None, picks the earliest week in the season that still has an
    unplayed game, which is almost always the week you want to bet.
    """
    close = conn is None
    conn = conn or connect(read_only=True)

    if week is None:
        row = conn.execute(
            "SELECT MIN(week) AS w FROM games WHERE season = ? AND result IS NULL",
            (season,),
        ).fetchone()
        week = row["w"]
        if week is None:  # season complete
            row = conn.execute(
                "SELECT MAX(week) AS w FROM games WHERE season = ?", (season,)
            ).fetchone()
            week = row["w"]

    df = pd.read_sql_query(
        """
        SELECT game_id, season, week, gameday, weekday, gametime,
               away_team, home_team, location,
               spread_line, total_line,
               away_moneyline, home_moneyline,
               roof, surface, stadium, div_game,
               away_rest, home_rest,
               away_score, home_score, result, total,
               home_ats, away_ats, ou_result,
               is_indoor, is_primetime
        FROM v_games_ats
        WHERE season = ? AND week = ?
        ORDER BY gameday, gametime
        """,
        conn, params=(season, week),
    )

    if close:
        conn.close()
    return df


def latest_ingest(conn=None) -> pd.DataFrame:
    """Most recent run of each job -- feeds the freshness indicator later."""
    close = conn is None
    conn = conn or connect(read_only=True)
    df = pd.read_sql_query(
        """
        SELECT job_name, MAX(started_at) AS last_run, status, rows_written, message
        FROM ingest_runs GROUP BY job_name ORDER BY job_name
        """,
        conn,
    )
    if close:
        conn.close()
    return df


# ---------------------------------------------------------------------------
# Team metrics and league ranks
# ---------------------------------------------------------------------------
#
# Each metric is (key, label, format, higher_is_better). Rank 1 is always the
# BEST team, for offense and defense alike -- so on a game card, "offense #3
# vs defense #28" means a strong unit facing a weak one, whatever the stat.
#
# OFFENSE and DEFENSE line up row for row: row i of OFFENSE is the stat that
# row i of DEFENSE tries to stop. The game card relies on that pairing.

OFFENSE = [
    ("off_epa",        "EPA / play",        "{:+.3f}", True),
    ("off_pass_epa",   "Pass EPA / play",   "{:+.3f}", True),
    ("off_rush_epa",   "Rush EPA / play",   "{:+.3f}", True),
    ("off_success",    "Success rate",      "{:.1%}",  True),
    ("off_ppg",        "Points / game",     "{:.1f}",  True),
    ("off_ypg",        "Yards / game",      "{:.0f}",  True),
    ("off_rz_td",      "Red zone TD %",     "{:.1%}",  True),
    ("off_third",      "3rd down %",        "{:.1%}",  True),
    ("off_giveaways",  "Giveaways / game",  "{:.2f}",  False),
    ("off_sacks",      "Sacks taken / game", "{:.2f}", False),
    ("off_explosive",  "Explosive play rate", "{:.1%}", True),
]

DEFENSE = [
    ("def_epa",        "EPA / play allowed",       "{:+.3f}", False),
    ("def_pass_epa",   "Pass EPA allowed",         "{:+.3f}", False),
    ("def_rush_epa",   "Rush EPA allowed",         "{:+.3f}", False),
    ("def_success",    "Success rate allowed",     "{:.1%}",  False),
    ("def_ppg",        "Points allowed / game",    "{:.1f}",  False),
    ("def_ypg",        "Yards allowed / game",     "{:.0f}",  False),
    ("def_rz_td",      "Red zone TD % allowed",    "{:.1%}",  False),
    ("def_third",      "3rd down % allowed",       "{:.1%}",  False),
    ("def_takeaways",  "Takeaways / game",         "{:.2f}",  True),
    ("def_sacks",      "Sacks / game",             "{:.2f}",  True),
    ("def_explosive",  "Explosive plays allowed",  "{:.1%}",  False),
]

# Style, not quality: rank 1 = fastest / most plays / most pass-heavy. These
# feed the totals profile and are never colored good or bad.
PACE = [
    ("pace_sec",       "Seconds between snaps", "{:.1f}", False),
    ("pace_plays",     "Plays / game",          "{:.1f}", True),
    ("neutral_pass",   "Neutral pass rate",     "{:.1%}", True),
]

METRICS = OFFENSE + DEFENSE + PACE


def _season_rates(conn, season: int, before_week: Optional[int]) -> pd.DataFrame:
    """
    One season's raw rates per team, regular season only.

    before_week limits it to games played before that week, so looking at
    Week 5 uses only Weeks 1-4 -- what you'd have known when betting it.
    Counts are summed first and divided last.
    """
    week_clause = "AND o.week < ?" if before_week else ""
    params = [season] + ([before_week] if before_week else [])

    df = pd.read_sql_query(f"""
        SELECT
            o.team,
            COUNT(*)                                    AS games,
            SUM(o.plays)                                AS off_plays,
            SUM(o.epa_sum)      / SUM(o.plays)          AS off_epa,
            SUM(o.pass_epa_sum) / SUM(o.pass_plays)     AS off_pass_epa,
            SUM(o.rush_epa_sum) / SUM(o.rush_plays)     AS off_rush_epa,
            1.0 * SUM(o.successes) / SUM(o.plays)       AS off_success,
            1.0 * SUM(o.yards) / COUNT(*)               AS off_ypg,
            1.0 * SUM(o.rz_tds) / NULLIF(SUM(o.rz_drives), 0)     AS off_rz_td,
            1.0 * SUM(o.third_conv) / NULLIF(SUM(o.third_att), 0) AS off_third,
            1.0 * SUM(o.giveaways) / COUNT(*)           AS off_giveaways,
            1.0 * SUM(o.sacks_taken) / COUNT(*)         AS off_sacks,

            SUM(d.plays)                                AS def_plays,
            SUM(d.epa_sum)      / SUM(d.plays)          AS def_epa,
            SUM(d.pass_epa_sum) / SUM(d.pass_plays)     AS def_pass_epa,
            SUM(d.rush_epa_sum) / SUM(d.rush_plays)     AS def_rush_epa,
            1.0 * SUM(d.successes) / SUM(d.plays)       AS def_success,
            1.0 * SUM(d.yards) / COUNT(*)               AS def_ypg,
            1.0 * SUM(d.rz_tds) / NULLIF(SUM(d.rz_drives), 0)     AS def_rz_td,
            1.0 * SUM(d.third_conv) / NULLIF(SUM(d.third_att), 0) AS def_third,
            1.0 * SUM(d.giveaways) / COUNT(*)           AS def_takeaways,
            1.0 * SUM(d.sacks_taken) / COUNT(*)         AS def_sacks,

            1.0 * SUM(g.points_for) / COUNT(*)          AS off_ppg,
            1.0 * SUM(g.points_against) / COUNT(*)      AS def_ppg,

            1.0 * SUM(o.explosives) / SUM(o.plays)      AS off_explosive,
            1.0 * SUM(d.explosives) / SUM(d.plays)      AS def_explosive,
            SUM(o.neutral_sec_sum) / NULLIF(SUM(o.neutral_sec_n), 0)          AS pace_sec,
            1.0 * SUM(o.plays) / COUNT(*)               AS pace_plays,
            1.0 * SUM(o.neutral_passes) / NULLIF(SUM(o.neutral_plays), 0)     AS neutral_pass,

            -- raw counts for the luck table
            SUM(g.margin > 0)                           AS wins,
            SUM(g.margin < 0)                           AS losses,
            SUM(g.margin = 0)                           AS ties,
            SUM(g.margin > 0 AND g.margin <= 8)         AS close_wins,
            SUM(g.margin < 0 AND g.margin >= -8)        AS close_losses,
            SUM(g.points_for)                           AS pf,
            SUM(g.points_against)                       AS pa,
            SUM(o.giveaways)                            AS giveaways,
            SUM(d.giveaways)                            AS takeaways,
            SUM(o.fumbles)                              AS own_fumbles,
            SUM(o.fumbles_lost)                         AS own_fumbles_lost,
            SUM(d.fumbles)                              AS opp_fumbles,
            SUM(d.fumbles_lost)                         AS opp_fumbles_lost
        FROM team_game_stats o
        JOIN team_game_stats d                 -- the opponent's offense = our defense
          ON d.game_id = o.game_id AND d.team = o.opponent
        JOIN v_team_games g
          ON g.game_id = o.game_id AND g.team = o.team
        WHERE o.season = ? AND o.season_type = 'REG' {week_clause}
        GROUP BY o.team
    """, conn, params=params)
    return df.set_index("team")


def team_metrics(
    season: int = CURRENT_SEASON,
    before_week: Optional[int] = None,
    conn=None,
) -> pd.DataFrame:
    """
    Blended team metrics with league ranks -- one row per team.

    blended = w x this season + (1 - w) x last season
    w       = this season's games played / BLEND_FULL_AT_GAMES, capped at 1

    Each team gets its own w, so a team coming off a bye leans a little more on
    last season than one that has played every week. If a stat has no value
    yet this season (no red zone trips, say) last season's number is used alone.

    Columns: every metric key, '<key>_rank' (1 = best of 32), 'games_now',
    'games_prior', 'weight_now', and 'plays' for the sample-size house rule.
    Returns an empty frame if there is no play-by-play for either season.
    """
    close = conn is None
    conn = conn or connect(read_only=True)
    now = _season_rates(conn, season, before_week)
    prior = _season_rates(conn, season - 1, None)
    if close:
        conn.close()

    teams = sorted(set(now.index) | set(prior.index))
    if not teams:
        return pd.DataFrame()

    # An empty season comes back as text columns; force everything numeric.
    now = now.reindex(teams).apply(pd.to_numeric, errors="coerce")
    prior = prior.reindex(teams).apply(pd.to_numeric, errors="coerce")

    out = pd.DataFrame(index=teams)
    out.index.name = "team"
    out["games_now"] = now["games"].fillna(0).astype(int)
    out["games_prior"] = prior["games"].fillna(0).astype(int)
    w = (out["games_now"] / BLEND_FULL_AT_GAMES).clip(upper=1.0)
    # With no prior season loaded, this season is all there is.
    w = w.where(out["games_prior"] > 0, 1.0)
    out["weight_now"] = w
    out["plays"] = (
        (now["off_plays"].fillna(0) + now["def_plays"].fillna(0)) * w
        + (prior["off_plays"].fillna(0) + prior["def_plays"].fillna(0)) * (1 - w)
    ).round().astype(int)

    for key, _label, _fmt, higher_better in METRICS:
        a, b = now[key], prior[key]
        blended = w * a + (1 - w) * b
        blended = blended.where(a.notna(), b)          # nothing this season yet
        blended = blended.where(b.notna(), a)          # nothing last season
        out[key] = blended
        out[f"{key}_rank"] = (
            blended.rank(ascending=not higher_better, method="min").astype("Int64")
        )

    return out


def matchup_table(metrics: pd.DataFrame, offense_team: str, defense_team: str) -> pd.DataFrame:
    """
    One team's offense against the other team's defense, stat by stat.

    'edge' flags a gap of 12+ rank places -- roughly a top-third unit against a
    bottom-third one. It's a pointer to look closer, not a bet.
    """
    rows = []
    for (ok, olabel, ofmt, _), (dk, _dl, dfmt, _) in zip(OFFENSE, DEFENSE, strict=True):
        o = metrics.loc[offense_team] if offense_team in metrics.index else None
        d = metrics.loc[defense_team] if defense_team in metrics.index else None
        ov = None if o is None or pd.isna(o[ok]) else o[ok]
        dv = None if d is None or pd.isna(d[dk]) else d[dk]
        orank = None if o is None or pd.isna(o[f"{ok}_rank"]) else int(o[f"{ok}_rank"])
        drank = None if d is None or pd.isna(d[f"{dk}_rank"]) else int(d[f"{dk}_rank"])

        edge = ""
        if orank is not None and drank is not None:
            gap = drank - orank          # positive = defense ranks worse than offense
            if gap >= 12:
                edge = f"{offense_team} O"
            elif gap <= -12:
                edge = f"{defense_team} D"

        rows.append({
            "stat": olabel.replace(" / play", "/play"),
            "off": ofmt.format(ov) if ov is not None else "-",
            "off_rank": orank,
            "def": dfmt.format(dv) if dv is not None else "-",
            "def_rank": drank,
            "edge": edge,
        })
    return pd.DataFrame(rows)


def blend_note(metrics: pd.DataFrame, team: str, season: int) -> str:
    """Plain-English description of what a team's numbers are built from."""
    if team not in metrics.index:
        return "no play-by-play for this team"
    r = metrics.loc[team]
    pct = int(round(r["weight_now"] * 100))
    gn, gp = int(r["games_now"]), int(r["games_prior"])
    if gp == 0:
        return f"{season} only · {gn} games"
    if gn == 0:
        return f"{season - 1} only ({gp} g) · no {season} games yet"
    return f"{pct}% {season} ({gn} g) · {100 - pct}% {season - 1} ({gp} g)"


# ===========================================================================
# ATS angles, cover margins and recent games
# ===========================================================================
#
# ats_margin is from the team's side: +6.5 means they beat the spread by 6.5
# points, -3 means they missed it by 3. Averages and medians include pushes
# (a push is a margin of 0, which is real information about the spread).

ANGLE_START = CURRENT_SEASON - 3          # "since 2023" windows
H2H_START = DEFAULT_ANALYSIS_START        # head-to-head goes back to 2015


def _team_games(conn, team: str, before: Optional[str] = None,
                since_season: int = 1999) -> pd.DataFrame:
    """Every completed game with a line for one team, newest first."""
    sql = """
        SELECT game_id, season, week, game_type, gameday, opponent, side,
               points_for, points_against, margin, team_spread, ats_margin, ats,
               is_favorite, total, total_line, ou_result, div_game, is_primetime,
               is_indoor, rest
        FROM v_team_games
        WHERE team = ? AND season >= ? AND ats IS NOT NULL
    """
    params: list = [team, since_season]
    if before:
        sql += " AND gameday < ?"
        params.append(before)
    sql += " ORDER BY gameday DESC"
    return pd.read_sql_query(sql, conn, params=params)


def summarize_games(df: pd.DataFrame) -> dict:
    """ATS + over/under summary for any slice of _team_games rows."""
    ats = _summarize(int((df["ats"] == "W").sum()), int((df["ats"] == "L").sum()),
                     int((df["ats"] == "P").sum()))
    ou = _summarize(int((df["ou_result"] == "OVER").sum()),
                    int((df["ou_result"] == "UNDER").sum()),
                    int((df["ou_result"] == "PUSH").sum()))
    has_total = df["total_line"].notna() & df["total"].notna()
    def r1(x):
        return None if x is None or pd.isna(x) else round(float(x), 1)
    return {
        "games": int(len(df)),
        "record": ats["record"], "n": ats["n"], "pct": ats["pct"],
        "moe": ats["moe"], "sig": ats["beats_coinflip"],
        "avg_margin": r1(df["ats_margin"].mean()) if len(df) else None,
        "median_margin": r1(df["ats_margin"].median()) if len(df) else None,
        "ou_record": ou["record"], "ou_n": ou["n"], "over_pct": ou["pct"],
        "ou_moe": ou["moe"], "ou_sig": ou["beats_coinflip"],
        "avg_total_vs_line": r1((df.loc[has_total, "total"] - df.loc[has_total, "total_line"]).mean())
                             if has_total.any() else None,
    }


def ats_angles(team: str, opponent: Optional[str] = None, side: Optional[str] = None,
               before: Optional[str] = None, season: int = CURRENT_SEASON,
               conn=None) -> pd.DataFrame:
    """
    The trend table for one team going into one game.

    before is the game date: only games before it count. Recent windows
    (last 3/5/10) include playoffs and cross seasons -- they're about form.
    The 'since' windows use every game from ANGLE_START on.
    """
    close = conn is None
    conn = conn or connect(read_only=True)
    all_games = _team_games(conn, team, before)
    h2h = all_games[(all_games["opponent"] == opponent) & (all_games["season"] >= H2H_START)] \
        if opponent else None
    if close:
        conn.close()

    since = all_games[all_games["season"] >= ANGLE_START]
    windows = [
        ("Last 3 games", all_games.head(3)),
        ("Last 5 games", all_games.head(5)),
        ("Last 10 games", all_games.head(10)),
        (f"{season} season", all_games[all_games["season"] == season]),
        (f"All games since {ANGLE_START}", since),
        (f"At home since {ANGLE_START}", since[since["side"] == "home"]),
        (f"On the road since {ANGLE_START}", since[since["side"] == "away"]),
    ]
    if h2h is not None:
        windows.append((f"vs {opponent} since {H2H_START}", h2h))

    rows = []
    for label, df in windows:
        r = summarize_games(df)
        r["angle"] = label
        r["this_game"] = bool(side and label.startswith("At home" if side == "home" else "On the road")) \
            or bool(opponent and label.startswith(f"vs {opponent}"))
        rows.append(r)
    return pd.DataFrame(rows)


def recent_games(team: str, before: Optional[str] = None, n: int = 10, conn=None) -> pd.DataFrame:
    """Last n games with the line and how far they beat or missed it. Oldest first."""
    close = conn is None
    conn = conn or connect(read_only=True)
    df = _team_games(conn, team, before).head(n).iloc[::-1].reset_index(drop=True)
    if close:
        conn.close()
    return df[["gameday", "season", "week", "game_type", "opponent", "side",
               "points_for", "points_against", "margin", "team_spread",
               "ats_margin", "ats", "total", "total_line", "ou_result"]]


# ===========================================================================
# Depth charts + injuries
# ===========================================================================

STARTERS_AT = {"WR": 3}      # every other depth-chart position has one starter

RESERVE_LABELS = {"R01": "Injured reserve", "R04": "PUP list", "R05": "Non-football injury list"}

# Display order within each side. Depth chart positions not listed go last.
POSITION_ORDER = {
    "O": ["QB", "RB", "FB", "WR", "TE", "LT", "LG", "C", "RG", "RT"],
    "D": ["LDE", "LDT", "NT", "RDT", "RDE", "WLB", "LILB", "MLB", "RILB", "SLB",
          "LCB", "RCB", "NB", "SS", "FS"],
    "ST": ["PK", "P", "LS", "H", "KR", "PR"],
}


def _status_bucket(report_status, practice_status, roster_status) -> str:
    """One word the board can color: out, doubtful, questionable, limited, active."""
    # Blank values arrive as None or NaN depending on the pandas version.
    report_status = report_status if isinstance(report_status, str) else ""
    practice_status = practice_status if isinstance(practice_status, str) else ""
    if roster_status == "RES":
        return "out"
    rs = report_status.lower()
    if rs == "out":
        return "out"
    if rs == "doubtful":
        return "doubtful"
    if rs == "questionable":
        return "questionable"
    ps = practice_status.lower()
    if "did not" in ps or "limited" in ps:
        return "limited"
    return "active"


def _snap_shares(conn, season: int) -> pd.DataFrame:
    """Average snap share per player (by gsis_id), this season, else last season."""
    return pd.read_sql_query("""
        WITH ids AS (SELECT DISTINCT pfr_id, gsis_id FROM roster_status WHERE pfr_id IS NOT NULL),
        per AS (
            SELECT i.gsis_id, s.season,
                   COUNT(*) AS games,
                   AVG(CASE WHEN s.offense_pct >= s.defense_pct THEN s.offense_pct ELSE s.defense_pct END) AS share
            FROM snap_counts s JOIN ids i ON i.pfr_id = s.pfr_player_id
            WHERE s.season IN (?, ?)
              AND (s.offense_pct > 0 OR s.defense_pct > 0)
            GROUP BY i.gsis_id, s.season
        )
        SELECT gsis_id,
               MAX(CASE WHEN season = ? THEN share END) AS share_now,
               MAX(CASE WHEN season = ? THEN games END) AS games_now,
               MAX(CASE WHEN season = ? THEN share END) AS share_prior,
               MAX(CASE WHEN season = ? THEN games END) AS games_prior
        FROM per GROUP BY gsis_id
    """, conn, params=[season, season - 1, season, season, season - 1, season - 1]).set_index("gsis_id")


def availability(team: str, season: int = CURRENT_SEASON, week: Optional[int] = None,
                 conn=None) -> dict:
    """
    A team's depth chart with injury status on every player, plus who's on
    reserve lists and who else is on the injury report.

    Returns {"report_week", "report_is_current", "snapshot_at",
             "depth": DataFrame, "reserve": DataFrame, "other_injuries": DataFrame}

    depth columns: side, pos_abb, pos_rank, starter, player_name, gsis_id,
    status (bucket), report_status, injury, practice_status, snap_share,
    snap_basis, replacement, replacement_share.
    """
    close = conn is None
    conn = conn or connect(read_only=True)

    if week is None:
        row = conn.execute("SELECT MIN(week) AS w FROM games WHERE season = ? AND result IS NULL",
                           (season,)).fetchone()
        week = row["w"] or 18

    depth = pd.read_sql_query("""
        SELECT side, formation, pos_abb, pos_slot, pos_rank, player_name, gsis_id, snapshot_at
        FROM depth_chart WHERE team = ?
    """, conn, params=[team])

    rep_week = conn.execute("""
        SELECT MAX(week) AS w FROM injury_reports
        WHERE season = ? AND team = ? AND week <= ?
    """, (season, team, week)).fetchone()["w"]
    inj = pd.read_sql_query("""
        SELECT gsis_id, full_name, position, report_status, report_injury, report_injury2,
               practice_status, practice_injury
        FROM injury_reports WHERE season = ? AND team = ? AND week = ?
    """, conn, params=[season, team, rep_week if rep_week is not None else -1]).set_index("gsis_id")

    ros_week = conn.execute("""
        SELECT MAX(week) AS w FROM roster_status WHERE season = ? AND team = ? AND week <= ?
    """, (season, team, week)).fetchone()["w"]
    roster = pd.read_sql_query("""
        SELECT gsis_id, full_name, position, jersey, status, status_code, years_exp
        FROM roster_status WHERE season = ? AND team = ? AND week = ?
    """, conn, params=[season, team, ros_week if ros_week is not None else -1]).set_index("gsis_id")

    shares = _snap_shares(conn, season)
    if close:
        conn.close()

    def share_of(gid):
        if gid is None or gid not in shares.index:
            return None, None
        r = shares.loc[gid]
        if pd.notna(r["share_now"]):
            return float(r["share_now"]), f"{season}, {int(r['games_now'])} g"
        if pd.notna(r["share_prior"]):
            return float(r["share_prior"]), f"{season - 1}, {int(r['games_prior'])} g"
        return None, None

    def injury_text(gid):
        if gid not in inj.index:
            return None
        r = inj.loc[gid]
        parts = [x for x in (r["report_injury"], r["report_injury2"]) if isinstance(x, str)]
        if not parts and isinstance(r["practice_injury"], str):
            parts = [r["practice_injury"]]
        return ", ".join(parts) or None

    rows = []
    for _, d in depth.iterrows():
        gid = d["gsis_id"] if isinstance(d["gsis_id"], str) else None
        ir = inj.loc[gid] if gid in inj.index else None
        ro = roster.loc[gid] if gid in roster.index else None
        ros_status = ro["status"] if ro is not None else None
        report_status = ir["report_status"] if ir is not None and isinstance(ir["report_status"], str) else None
        if ros_status == "RES":
            code = ro["status_code"] if isinstance(ro["status_code"], str) else ""
            report_status = RESERVE_LABELS.get(code, "Reserve list")
        share, basis = share_of(gid)
        rows.append({
            "side": d["side"], "formation": d["formation"], "pos_abb": d["pos_abb"],
            "pos_rank": int(d["pos_rank"]),
            "starter": int(d["pos_rank"]) <= STARTERS_AT.get(d["pos_abb"], 1),
            "player_name": _s(d["player_name"]) or "(unnamed)", "gsis_id": gid,
            "status": _status_bucket(ir["report_status"] if ir is not None else None,
                                     ir["practice_status"] if ir is not None else None,
                                     ros_status),
            "report_status": report_status,
            "injury": injury_text(gid) if gid else None,
            "practice_status": ir["practice_status"] if ir is not None and isinstance(ir["practice_status"], str) else None,
            "snap_share": share, "snap_basis": basis,
        })
    dc = pd.DataFrame(rows)

    # Next man up for anyone not fully available: the best-ranked healthy
    # player at the same position who isn't already a starter.
    if not dc.empty:
        dc["replacement"] = None
        dc["replacement_share"] = None
        for i, r in dc.iterrows():
            if not r["starter"] or r["status"] not in ("out", "doubtful", "questionable"):
                continue
            pool = dc[(dc["pos_abb"] == r["pos_abb"]) & (dc["side"] == r["side"])
                      & (~dc["starter"]) & (dc["status"].isin(["active", "limited"]))
                      & (dc["gsis_id"] != r["gsis_id"])].sort_values("pos_rank")
            if len(pool):
                dc.at[i, "replacement"] = pool.iloc[0]["player_name"]
                dc.at[i, "replacement_share"] = pool.iloc[0]["snap_share"]
        order = {s: {p: k for k, p in enumerate(v)} for s, v in POSITION_ORDER.items()}
        dc["_o"] = [order.get(sd, {}).get(p, 99) for sd, p in zip(dc["side"], dc["pos_abb"])]
        dc = dc.sort_values(["side", "_o", "pos_rank"]).drop(columns="_o").reset_index(drop=True)

    on_chart = set(dc["gsis_id"].dropna()) if not dc.empty else set()

    res = roster[roster["status"] == "RES"].copy()
    res = res[~res.index.isin(on_chart)]
    reserve = pd.DataFrame([{
        "player_name": r["full_name"], "position": r["position"],
        "list": RESERVE_LABELS.get(r["status_code"] if isinstance(r["status_code"], str) else "", "Reserve list"),
        "snap_share": share_of(gid)[0], "snap_basis": share_of(gid)[1],
    } for gid, r in res.iterrows()])
    if not reserve.empty:
        reserve = reserve.sort_values("snap_share", ascending=False, na_position="last").reset_index(drop=True)

    other = inj[~inj.index.isin(on_chart) & ~inj.index.isin(res.index)]
    other_injuries = pd.DataFrame([{
        "player_name": r["full_name"], "position": r["position"],
        "status": _status_bucket(r["report_status"], r["practice_status"], None),
        "report_status": r["report_status"] if isinstance(r["report_status"], str) else None,
        "injury": injury_text(gid), "practice_status": r["practice_status"] if isinstance(r["practice_status"], str) else None,
        "snap_share": share_of(gid)[0], "snap_basis": share_of(gid)[1],
    } for gid, r in other.iterrows()])
    if not other_injuries.empty:
        other_injuries = other_injuries[other_injuries["status"] != "active"]
        other_injuries = other_injuries.sort_values("snap_share", ascending=False, na_position="last").reset_index(drop=True)

    return {
        "week": week,
        "report_week": rep_week,
        "report_is_current": rep_week == week,
        "snapshot_at": depth["snapshot_at"].max() if len(depth) else None,
        "depth": dc, "reserve": reserve, "other_injuries": other_injuries,
    }


# ===========================================================================
# Model spread and total
# ===========================================================================
#
# A simple, transparent power rating -- no black box:
#
#   rating = half points-based + half EPA-based net rating, in points per game
#            points-based = points scored/g - points allowed/g
#            EPA-based    = (off EPA/play - def EPA/play) x EPA_TO_POINTS
#   model home margin = SHRINK x (home rating - away rating) + HOME_FIELD
#
# EPA_TO_POINTS, SHRINK and HOME_FIELD are fitted from data (see _fit), not
# picked by hand: the constants come from two seasons back, and the backtest
# runs on last season, so the model is never graded on games it was fit to.
#
# The total works the same way: each team's expected points is the average of
# its scoring and its opponent's points allowed, pulled toward the league
# average by TOTAL_SHRINK.

_FIT_CACHE: dict = {}


def _ratings(m: pd.DataFrame, epa_to_points: float) -> pd.Series:
    pts = m["off_ppg"] - m["def_ppg"]
    epa = (m["off_epa"] - m["def_epa"]) * epa_to_points
    return 0.5 * pts + 0.5 * epa


def _games_with_ratings(conn, season: int) -> pd.DataFrame:
    """Every completed regular-season game in a season, with both teams'
    as-of-kickoff metrics (weeks 2+; week 1 has nothing from that season)."""
    games = pd.read_sql_query("""
        SELECT game_id, week, home_team, away_team, location, result, total,
               spread_line, total_line, home_ats, ou_result
        FROM v_games_ats WHERE season = ? AND game_type = 'REG' AND result IS NOT NULL
    """, conn, params=[season])
    frames = []
    for wk in sorted(games["week"].unique()):
        m = team_metrics(season, int(wk), conn=conn)
        if m.empty:
            continue
        g = games[games["week"] == wk].copy()
        for side in ("home", "away"):
            cols = m[["off_ppg", "def_ppg", "off_epa", "def_epa", "games_now"]]
            cols = cols.add_prefix(f"{side}_")
            g = g.merge(cols, left_on=f"{side}_team", right_index=True, how="left")
        frames.append(g)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _fit(conn, fit_season: int) -> dict:
    """Fit the model's constants on one season's games."""
    key = (str(_db_path()), fit_season)
    if key in _FIT_CACHE:
        return _FIT_CACHE[key]

    # EPA -> points: team-season point differential per game vs net EPA/play.
    sr = _season_rates(conn, fit_season, None).apply(pd.to_numeric, errors="coerce")
    x = (sr["off_epa"] - sr["def_epa"]).to_numpy()
    y = (sr["off_ppg"] - sr["def_ppg"]).to_numpy()
    epa_to_points = float((x * y).sum() / (x * x).sum())

    # Home field: average home margin at non-neutral sites, recent seasons.
    hf = conn.execute("""
        SELECT AVG(result) AS h FROM games
        WHERE location = 'Home' AND game_type = 'REG' AND result IS NOT NULL
          AND season BETWEEN ? AND ?
    """, (fit_season - 4, fit_season)).fetchone()["h"]

    # Shrink: how much of a rating gap shows up in results. Least squares on
    # games from week 7 on, when the ratings rest on real samples.
    g = _games_with_ratings(conn, fit_season)
    g = g[g["week"] >= 7].dropna(subset=["home_off_ppg", "away_off_ppg"])
    hr = (0.5 * (g["home_off_ppg"] - g["home_def_ppg"])
          + 0.5 * (g["home_off_epa"] - g["home_def_epa"]) * epa_to_points)
    ar = (0.5 * (g["away_off_ppg"] - g["away_def_ppg"])
          + 0.5 * (g["away_off_epa"] - g["away_def_epa"]) * epa_to_points)
    home_adv = (g["location"] != "Neutral").astype(float) * hf
    diff = (hr - ar).to_numpy()
    target = (g["result"] - home_adv).to_numpy()
    shrink = float((diff * target).sum() / (diff * diff).sum())

    # Totals: actual total vs raw expected total, both around the league mean.
    raw_total = ((g["home_off_ppg"] + g["away_def_ppg"]) / 2
                 + (g["away_off_ppg"] + g["home_def_ppg"]) / 2)
    league = float(g["total"].mean())
    dx = (raw_total - league).to_numpy()
    dy = (g["total"] - league).to_numpy()
    total_shrink = float((dx * dy).sum() / (dx * dx).sum())

    fit = {"fit_season": fit_season, "epa_to_points": round(epa_to_points, 1),
           "home_field": round(float(hf), 2), "shrink": round(shrink, 3),
           "total_shrink": round(total_shrink, 3), "games": int(len(g))}
    _FIT_CACHE[key] = fit
    return fit


def _db_path():
    from config import DB_PATH
    return DB_PATH


def model_lines(season: int = CURRENT_SEASON, week: Optional[int] = None, conn=None) -> pd.DataFrame:
    """
    Model spread and total for every game in a week, next to the market.

    Columns: game_id, home_team, away_team, model_margin (home), market_margin
    (= spread_line), spread_edge (model - market; + favors home), model_total,
    market_total, total_edge (+ favors over), model_side, lean.
    """
    close = conn is None
    conn = conn or connect(read_only=True)
    fit = _fit(conn, season - 2) if season - 2 >= min(_pbp_seasons(conn)) else None
    if fit is None:
        if close:
            conn.close()
        return pd.DataFrame()
    slate = week_slate(season, week, conn=conn)
    wk = int(slate.iloc[0]["week"]) if len(slate) else week
    m = team_metrics(season, wk, conn=conn)
    league_total = conn.execute("""
        SELECT AVG(total) AS t FROM games WHERE season BETWEEN ? AND ? AND game_type = 'REG'
          AND total IS NOT NULL
    """, (season - 2, season)).fetchone()["t"]
    if close:
        conn.close()
    if m.empty:
        return pd.DataFrame()
    return _price(slate, m, fit, league_total)


def _price(slate: pd.DataFrame, m: pd.DataFrame, fit: dict, league_total: float) -> pd.DataFrame:
    r = _ratings(m, fit["epa_to_points"])
    rows = []
    for _, g in slate.iterrows():
        h, a = g["home_team"], g["away_team"]
        if h not in m.index or a not in m.index:
            continue
        hfa = 0.0 if g["location"] == "Neutral" else fit["home_field"]
        margin = fit["shrink"] * (r[h] - r[a]) + hfa
        raw_total = ((m.at[h, "off_ppg"] + m.at[a, "def_ppg"]) / 2
                     + (m.at[a, "off_ppg"] + m.at[h, "def_ppg"]) / 2)
        total = league_total + fit["total_shrink"] * (raw_total - league_total)
        spread = g["spread_line"] if pd.notna(g["spread_line"]) else None
        tline = g["total_line"] if pd.notna(g["total_line"]) else None
        edge = None if spread is None else margin - spread
        tedge = None if tline is None else total - tline
        rows.append({
            "game_id": g["game_id"], "home_team": h, "away_team": a,
            "home_rating": round(float(r[h]), 1), "away_rating": round(float(r[a]), 1),
            "model_margin": round(float(margin), 1), "market_margin": spread,
            "spread_edge": None if edge is None else round(float(edge), 1),
            "model_side": None if edge is None or abs(edge) < 0.05 else (h if edge > 0 else a),
            "model_total": round(float(total), 1), "market_total": tline,
            "total_edge": None if tedge is None else round(float(tedge), 1),
            "total_lean": None if tedge is None or abs(tedge) < 0.05 else ("Over" if tedge > 0 else "Under"),
        })
    return pd.DataFrame(rows)


def _pbp_seasons(conn) -> list[int]:
    rows = conn.execute("SELECT DISTINCT season FROM team_game_stats").fetchall()
    return [r["season"] for r in rows] or [9999]


def model_backtest(season: int = CURRENT_SEASON - 1, thresholds=(0, 1.5, 3, 5), conn=None) -> dict:
    """
    How the model would have done on a completed season, betting its side
    whenever it disagreed with the closing line by at least the threshold.

    Constants are fit on the season before, so this is out of sample. Closing
    lines are the toughest benchmark there is -- expect ~50%.
    """
    close = conn is None
    conn = conn or connect(read_only=True)
    loaded = _pbp_seasons(conn)
    if season - 1 not in loaded:
        if close:
            conn.close()
        return {}
    fit = _fit(conn, season - 1)
    league_total = conn.execute("""
        SELECT AVG(total) AS t FROM games WHERE season BETWEEN ? AND ? AND game_type = 'REG'
          AND total IS NOT NULL
    """, (season - 3, season - 1)).fetchone()["t"]
    games = pd.read_sql_query("""
        SELECT game_id, season, week, gameday, gametime, away_team, home_team, location,
               spread_line, total_line, result, total, home_ats, ou_result
        FROM v_games_ats WHERE season = ? AND game_type = 'REG' AND result IS NOT NULL
        ORDER BY week
    """, conn, params=[season])
    priced = []
    for wk in sorted(games["week"].unique()):
        m = team_metrics(season, int(wk), conn=conn)
        if m.empty:
            continue
        p = _price(games[games["week"] == wk], m, fit, league_total)
        priced.append(p.merge(games[["game_id", "week", "home_ats", "ou_result"]], on="game_id"))
    if close:
        conn.close()
    df = pd.concat(priced, ignore_index=True)

    def side_result(r):
        if _s(r["model_side"]) is None or _s(r["home_ats"]) is None:
            return None
        if r["home_ats"] == "P":
            return "P"
        home_won = r["home_ats"] == "W"
        return "W" if (r["model_side"] == r["home_team"]) == home_won else "L"

    def total_result(r):
        if _s(r["total_lean"]) is None or _s(r["ou_result"]) is None:
            return None
        if r["ou_result"] == "PUSH":
            return "P"
        return "W" if (r["total_lean"] == "Over") == (r["ou_result"] == "OVER") else "L"

    df["side_res"] = df.apply(side_result, axis=1)
    df["total_res"] = df.apply(total_result, axis=1)
    out = {"season": season, "fit": fit, "spreads": [], "totals": []}
    for t in thresholds:
        for kind, edge_col, res_col in (("spreads", "spread_edge", "side_res"),
                                        ("totals", "total_edge", "total_res")):
            sub = df[df[edge_col].abs() >= t]
            s = _summarize(int((sub[res_col] == "W").sum()), int((sub[res_col] == "L").sum()),
                           int((sub[res_col] == "P").sum()))
            out[kind].append({"threshold": t, **{k: s[k] for k in ("record", "n", "pct", "moe", "beats_coinflip")}})
    # Mean absolute error vs the market's own error, the fairest single check.
    df = df.merge(games[["game_id", "result", "total"]], on="game_id")
    out["mae"] = {
        "model_margin": round(float((df["model_margin"] - df["result"]).abs().mean()), 2),
        "market_margin": round(float((df["market_margin"] - df["result"]).abs().mean()), 2),
        "model_total": round(float((df["model_total"] - df["total"]).abs().mean()), 2),
        "market_total": round(float((df["market_total"] - df["total"]).abs().mean()), 2),
        "games": int(len(df)),
    }
    return out


# ===========================================================================
# Luck / regression flags
# ===========================================================================

PYTHAG_EXP = 2.37


def luck_table(season: int = CURRENT_SEASON, before_week: Optional[int] = None,
               conn=None) -> pd.DataFrame:
    """
    Signs a team's record is running ahead of (or behind) how well it played.
    Markets price records; these numbers price performance.

      pythag_wins   wins expected from points scored and allowed
      luck_wins     actual wins minus pythag_wins (+ = lucky)
      close_record  record in games decided by 8 or fewer
      to_margin_pg  takeaways minus giveaways per game
      fumble_rec    share of all fumbles in their games that they recovered
                    (league average is about 50%; it doesn't persist)
    """
    close = conn is None
    conn = conn or connect(read_only=True)
    r = _season_rates(conn, season, before_week).apply(pd.to_numeric, errors="coerce")
    if close:
        conn.close()
    if r.empty:
        return pd.DataFrame()
    g = r["games"]
    pf, pa = r["pf"], r["pa"]
    pyth = pf ** PYTHAG_EXP / (pf ** PYTHAG_EXP + pa ** PYTHAG_EXP)
    out = pd.DataFrame(index=r.index)
    out["games"] = g.astype(int)
    out["record"] = [f"{int(w)}-{int(l)}" + (f"-{int(t)}" if t else "")
                     for w, l, t in zip(r["wins"], r["losses"], r["ties"])]
    out["pythag_wins"] = (pyth * g).round(1)
    out["luck_wins"] = (r["wins"] + 0.5 * r["ties"] - pyth * g).round(1)
    out["close_record"] = [f"{int(w)}-{int(l)}" for w, l in zip(r["close_wins"], r["close_losses"])]
    out["close_games"] = (r["close_wins"] + r["close_losses"]).astype(int)
    out["to_margin_pg"] = ((r["takeaways"] - r["giveaways"]) / g).round(2)
    fum_total = r["own_fumbles"] + r["opp_fumbles"]
    recovered = (r["own_fumbles"] - r["own_fumbles_lost"]) + r["opp_fumbles_lost"]
    out["fumbles"] = fum_total.astype(int)
    out["fumble_rec"] = (recovered / fum_total.where(fum_total > 0)).round(3)

    flags = []
    for t, x in out.iterrows():
        f = []
        if x["games"] >= 4 and x["luck_wins"] >= 1.5:
            f.append(("lucky", f"{x['luck_wins']:+.1f} wins vs points-based expectation"))
        if x["games"] >= 4 and x["luck_wins"] <= -1.5:
            f.append(("unlucky", f"{x['luck_wins']:+.1f} wins vs points-based expectation"))
        cw, cl = map(int, x["close_record"].split("-"))
        if cw + cl >= 4 and cw / (cw + cl) >= 0.75:
            f.append(("lucky", f"{x['close_record']} in one-score games"))
        if cw + cl >= 4 and cw / (cw + cl) <= 0.25:
            f.append(("unlucky", f"{x['close_record']} in one-score games"))
        if x["games"] >= 4 and x["to_margin_pg"] >= 1.0:
            f.append(("lucky", f"{x['to_margin_pg']:+.2f} turnover margin per game"))
        if x["games"] >= 4 and x["to_margin_pg"] <= -1.0:
            f.append(("unlucky", f"{x['to_margin_pg']:+.2f} turnover margin per game"))
        if x["fumbles"] >= 12 and pd.notna(x["fumble_rec"]) and x["fumble_rec"] >= 0.60:
            f.append(("lucky", f"recovered {x['fumble_rec']:.0%} of fumbles"))
        if x["fumbles"] >= 12 and pd.notna(x["fumble_rec"]) and x["fumble_rec"] <= 0.40:
            f.append(("unlucky", f"recovered {x['fumble_rec']:.0%} of fumbles"))
        flags.append(f)
    out["flags"] = flags
    out.index.name = "team"
    return out

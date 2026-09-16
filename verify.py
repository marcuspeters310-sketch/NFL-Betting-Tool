"""
Verification for the ATS logic and the team metrics.

The point of this file is that the sign conventions in betting data are easy to
get backwards and the mistake is invisible -- flip the spread and you still get
plausible-looking percentages near 50%, just wrong ones. So rather than trusting
that the code reads correctly, this checks it three ways:

  1. Internal consistency -- do the stored fields agree with each other?
  2. Hand-checkable games -- do specific games come out right?
  3. League-wide sanity -- do the aggregate rates land where the market forces
     them to land?

Run it any time you change the views:   python verify.py
"""
from __future__ import annotations

from db import connect
import queries

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((PASS if ok else FAIL, name, detail))


conn = connect(read_only=True)

# ---------------------------------------------------------------------------
# 1. Internal consistency
# ---------------------------------------------------------------------------
r = conn.execute("""
    SELECT COUNT(*) AS bad FROM games
    WHERE result IS NOT NULL AND result != home_score - away_score
""").fetchone()
check("result equals home_score - away_score", r["bad"] == 0, f"{r['bad']} mismatched rows")

r = conn.execute("""
    SELECT COUNT(*) AS bad FROM games
    WHERE total IS NOT NULL AND total != home_score + away_score
""").fetchone()
check("total equals home_score + away_score", r["bad"] == 0, f"{r['bad']} mismatched rows")

# A push must happen when, and only when, the margin lands exactly on the spread.
r = conn.execute("""
    SELECT COUNT(*) AS bad FROM v_games_ats
    WHERE home_ats IS NOT NULL
      AND ((home_ats = 'P') != (result = spread_line))
""").fetchone()
check("ATS pushes occur exactly when margin equals spread", r["bad"] == 0, f"{r['bad']} bad rows")

# Every game must be a mirror: if home won ATS, away lost it.
r = conn.execute("""
    SELECT COUNT(*) AS bad FROM v_games_ats
    WHERE home_ats IS NOT NULL AND (
        (home_ats = 'W' AND away_ats != 'L') OR
        (home_ats = 'L' AND away_ats != 'W') OR
        (home_ats = 'P' AND away_ats != 'P'))
""").fetchone()
check("home and away ATS results mirror each other", r["bad"] == 0, f"{r['bad']} bad rows")

# The long-format view must contain exactly two rows per game.
r = conn.execute("""
    SELECT (SELECT COUNT(*) FROM v_team_games) AS t, (SELECT COUNT(*) FROM games) AS g
""").fetchone()
check("v_team_games has 2 rows per game", r["t"] == r["g"] * 2, f"{r['t']} vs {r['g']}x2")

# ---------------------------------------------------------------------------
# 2. Hand-checkable games
# ---------------------------------------------------------------------------
# Each of these is verifiable against the box score. The reasoning is spelled
# out so you can confirm the expectation without trusting the code.
cases = [
    # game_id, expected home_ats, expected ou, why
    ("2025_01_DAL_PHI", "L", None,
     "PHI favored by 8.5 at home, won 24-20 (margin 4). 4 < 8.5, so the home favorite failed to cover."),
    ("2025_01_KC_LAC",  "W", None,
     "LAC was a 3-point home underdog (spread_line -3). LAC won by 6, so the home dog covered easily."),
    ("2025_01_TB_ATL",  "L", None,
     "ATL was a 1.5-point home favorite. ATL lost by 3, so the home team did not cover."),
]

for gid, want_ats, want_ou, why in cases:
    row = conn.execute("""
        SELECT game_id, away_team, home_team, away_score, home_score,
               spread_line, total_line, result, total, home_ats, ou_result
        FROM v_games_ats WHERE game_id = ?
    """, (gid,)).fetchone()

    if row is None:
        check(f"{gid} present", False, "game not found")
        continue

    detail = (f"{row['away_team']} {row['away_score']} @ {row['home_team']} {row['home_score']} | "
              f"spread {row['spread_line']:+.1f} total {row['total_line']} -> "
              f"home_ats={row['home_ats']} ou={row['ou_result']}  ({why})")
    check(f"{gid} home ATS = {want_ats}", row["home_ats"] == want_ats, detail)

# ---------------------------------------------------------------------------
# 3. League-wide sanity
# ---------------------------------------------------------------------------
# Sportsbooks set lines to split the money, so over a very large sample both
# ATS and over/under should sit close to 50%. Anything outside 47-53% across
# 7,000+ games means a sign error, not an edge.
r = conn.execute("""
    SELECT SUM(home_ats = 'W') AS w, SUM(home_ats = 'L') AS l, SUM(home_ats = 'P') AS p
    FROM v_games_ats WHERE home_ats IS NOT NULL
""").fetchone()
home_pct = r["w"] / (r["w"] + r["l"]) * 100
check("home ATS rate is 47-53% across all games", 47 <= home_pct <= 53,
      f"home teams {r['w']}-{r['l']}-{r['p']} ATS = {home_pct:.1f}%")

r = conn.execute("""
    SELECT SUM(ou_result = 'OVER') AS o, SUM(ou_result = 'UNDER') AS u, SUM(ou_result = 'PUSH') AS p
    FROM v_games_ats WHERE ou_result IS NOT NULL
""").fetchone()
over_pct = r["o"] / (r["o"] + r["u"]) * 100
check("over rate is 47-53% across all games", 47 <= over_pct <= 53,
      f"{r['o']} overs / {r['u']} unders / {r['p']} pushes = {over_pct:.1f}% over")

# Favorites win outright far more than they cover -- a classic reality check.
# If "favorites cover" came out near 65% you would have the spread backwards.
r = conn.execute("""
    SELECT
      AVG(CASE WHEN result > 0 THEN 1.0 ELSE 0.0 END) * 100 AS home_win_pct
    FROM v_games_ats WHERE result IS NOT NULL AND result != 0
""").fetchone()
check("home teams win outright 53-60% of the time", 53 <= r["home_win_pct"] <= 60,
      f"home straight-up win rate = {r['home_win_pct']:.1f}%")

# Key numbers: 3 and 7 are the most common NFL margins. If this ordering is
# wrong, something is deeply off with the score data.
rows = conn.execute("""
    SELECT ABS(result) AS m, COUNT(*) AS n FROM games
    WHERE result IS NOT NULL AND result != 0
    GROUP BY m ORDER BY n DESC LIMIT 3
""").fetchall()
top = [r["m"] for r in rows]
check("3 and 7 are among the three most common margins", 3 in top and 7 in top,
      f"most common margins: {[(r['m'], r['n']) for r in rows]}")

# ---------------------------------------------------------------------------
# 4. Team metrics (play-by-play)
# ---------------------------------------------------------------------------
from config import CURRENT_SEASON, BLEND_FULL_AT_GAMES

r = conn.execute("""
    SELECT COUNT(*) AS n,
           SUM(CASE WHEN t.c != 2 THEN 1 ELSE 0 END) AS bad,
           SUM(CASE WHEN g.game_id IS NULL THEN 1 ELSE 0 END) AS orphans
    FROM (SELECT game_id, COUNT(*) AS c FROM team_game_stats GROUP BY game_id) t
    LEFT JOIN games g ON g.game_id = t.game_id
""").fetchone()
check("team_game_stats has 2 rows per game, all matching the games table",
      r["n"] > 0 and r["bad"] == 0 and r["orphans"] == 0,
      f"{r['n']} games, {r['bad']} without exactly 2 rows, {r['orphans']} unknown game ids")

r = conn.execute("""
    SELECT COUNT(*) AS bad FROM team_game_stats
    WHERE pass_plays + rush_plays != plays
       OR rz_tds > rz_drives OR third_conv > third_att
       OR successes > plays OR giveaways < 0 OR sacks_taken > pass_plays
""").fetchone()
check("counts are internally consistent (parts <= wholes)", r["bad"] == 0, f"{r['bad']} bad rows")

# Every completed game in a loaded season should have play-by-play.
r = conn.execute("""
    SELECT COUNT(*) AS missing FROM games g
    WHERE g.season IN (SELECT DISTINCT season FROM team_game_stats)
      AND g.result IS NOT NULL
      AND g.game_id NOT IN (SELECT game_id FROM team_game_stats)
""").fetchone()
check("every played game in a loaded season has play-by-play", r["missing"] == 0,
      f"{r['missing']} played games missing")

# EPA is built so the league averages out near zero. A season landing far from
# it means the play filter is off (e.g. special teams leaking in).
for row in conn.execute("""
    SELECT season, SUM(epa_sum) / SUM(plays) AS epa,
           1.0 * SUM(rz_tds) / SUM(rz_drives) AS rz,
           1.0 * SUM(third_conv) / SUM(third_att) AS third,
           COUNT(*) / 2 AS games
    FROM team_game_stats WHERE season_type = 'REG' GROUP BY season
"""):
    if row["games"] < 80:   # about five weeks -- before that, one wild week can swing these
        check(f"{row['season']} league averages (skipped until 80 games are in)", True,
              f"only {row['games']} games so far: EPA {row['epa']:+.3f}, "
              f"RZ TD {row['rz']:.1%}, 3rd down {row['third']:.1%}")
        continue
    check(f"{row['season']} league EPA/play within +/-0.05, RZ TD 45-70%, 3rd down 33-47%",
          abs(row["epa"]) <= 0.05 and 0.45 <= row["rz"] <= 0.70 and 0.33 <= row["third"] <= 0.47,
          f"EPA {row['epa']:+.3f}, RZ TD {row['rz']:.1%}, 3rd down {row['third']:.1%}")

# Points in the metrics come from the scoreboard. Hand check: CHI scored 59
# in 2026 Week 1, so a CHI 2026-only points/game after Week 1 must be 59.
chi = queries._season_rates(conn, 2026, 2)
if "CHI" in chi.index:
    check("hand check: CHI 2026 points/game after Week 1 = 59",
          chi.loc["CHI", "off_ppg"] == 59, f"got {chi.loc['CHI', 'off_ppg']}")

m = queries.team_metrics(CURRENT_SEASON, None, conn=conn)
if not m.empty:
    bad = [k for k, *_ in queries.METRICS
           if m[f"{k}_rank"].min() != 1 or m[f"{k}_rank"].max() > len(m)]
    check(f"every metric ranks {len(m)} teams from 1 to at most {len(m)}",
          len(m) == 32 and not bad, f"{len(m)} teams; bad columns: {bad}")

    # Direction: rank 1 must hold the best value -- highest for 'higher is
    # better' stats, lowest otherwise. This is the check that catches a
    # defense being ranked upside down.
    wrong = []
    for k, _l, _f, higher in queries.METRICS:
        best = m.loc[m[f"{k}_rank"] == 1, k].iloc[0]
        target = m[k].max() if higher else m[k].min()
        if best != target:
            wrong.append(k)
    check(f"rank 1 holds the best value for all {len(queries.METRICS)} metrics (defense: lowest allowed; pace: fastest)",
          not wrong, f"wrong direction: {wrong}" if wrong else
          f"e.g. best defense EPA {m['def_epa'].min():+.3f} is rank 1")

    w = m["weight_now"]
    expected = (m["games_now"] / BLEND_FULL_AT_GAMES).clip(upper=1)
    check("blend weights are this-season games / 6, capped at 100%, and the two shares sum to 100%",
          ((w - expected).abs() < 1e-9).all() and w.between(0, 1).all(),
          f"current weights: {sorted(set((w * 100).round().astype(int)))}% this season")

    # Recompute one blended number by hand from the two separate seasons.
    now = queries._season_rates(conn, CURRENT_SEASON, None)
    prior = queries._season_rates(conn, CURRENT_SEASON - 1, None)
    t = "BUF"
    wt = m.loc[t, "weight_now"]
    manual = wt * now.loc[t, "off_epa"] + (1 - wt) * prior.loc[t, "off_epa"]
    check(f"{t} blended offense EPA = {wt:.0%} x {CURRENT_SEASON} + {1 - wt:.0%} x {CURRENT_SEASON - 1}",
          abs(manual - m.loc[t, "off_epa"]) < 1e-9,
          f"{wt:.3f} x {now.loc[t, 'off_epa']:+.3f} + {1 - wt:.3f} x {prior.loc[t, 'off_epa']:+.3f} "
          f"= {manual:+.4f}; tool shows {m.loc[t, 'off_epa']:+.4f}")

    # Defense is the opponent's offense: the league's total EPA allowed must
    # equal the league's total EPA gained, game for game.
    r = conn.execute("""
        SELECT ABS(SUM(o.epa_sum) - SUM(d.epa_sum)) AS diff
        FROM team_game_stats o
        JOIN team_game_stats d ON d.game_id = o.game_id AND d.team = o.opponent
    """).fetchone()
    check("total EPA allowed equals total EPA gained (defense = opponent's offense)",
          r["diff"] < 1e-6, f"difference {r['diff']:.2e}")

# ---------------------------------------------------------------------------
# 5. ATS margins, trends, availability, model, luck, pace
# ---------------------------------------------------------------------------
import pandas as pd

r = conn.execute("""
    SELECT COUNT(*) AS bad FROM v_team_games
    WHERE ats_margin IS NOT NULL AND ABS(ats_margin - (margin - team_spread)) > 1e-9
""").fetchone()
check("team ATS margin = team's margin - team's spread (both sides of every game)",
      r["bad"] == 0, f"{r['bad']} bad rows")

rg = queries.recent_games("PHI", "2025-09-06", 1, conn=conn)
check("hand check: PHI 24-20 vs DAL, favored by 8.5 -> ATS margin -4.5, a loss",
      len(rg) == 1 and rg.iloc[0]["ats_margin"] == -4.5 and rg.iloc[0]["ats"] == "L",
      rg.to_dict("records")[0] if len(rg) else "no game")

sg = queries.summarize_games(queries._team_games(conn, "CHI").head(1))
check("a 1-game record is never flagged as a signal", sg["sig"] is False, f"{sg['record']}")

angles = queries.ats_angles("BUF", "DET", "home", "2026-09-17", conn=conn)
bad = angles[(angles["sig"]) & (angles["n"] < queries.MIN_SIGNAL_N)]
check("trend windows respect the minimum sample for 'signal'", bad.empty,
      f"{len(angles)} windows checked")
last5 = angles.set_index("angle").loc["Last 5 games"]
manual = queries.recent_games("BUF", "2026-09-17", 5, conn=conn)["ats_margin"].mean()
check("Last-5 average cover margin matches the recent-games list",
      abs(last5["avg_margin"] - round(manual, 1)) < 1e-9, f"{last5['avg_margin']} vs {manual:.2f}")

r = conn.execute("""
    SELECT COUNT(DISTINCT team) AS teams,
           COUNT(DISTINCT CASE WHEN pos_abb = 'QB' AND pos_rank = 1 THEN team END) AS qb1
    FROM depth_chart
""").fetchone()
check("depth chart covers 32 teams, each with a starting QB",
      r["teams"] == 32 and r["qb1"] == 32, f"{r['teams']} teams, {r['qb1']} with a QB1")

av = queries.availability("CHI", conn=conn)
res_ids = {x["gsis_id"] for x in conn.execute(
    "SELECT gsis_id FROM roster_status WHERE season = ? AND team = 'CHI' AND status = 'RES' "
    "AND week = (SELECT MAX(week) FROM roster_status WHERE season = ? AND team = 'CHI' AND week <= ?)",
    (CURRENT_SEASON, CURRENT_SEASON, av["week"]))}
dc = av["depth"]
wrong = dc[dc["gsis_id"].isin(res_ids) & (dc["status"] != "out")]
check("every player on a reserve list shows as OUT on the depth chart",
      wrong.empty, f"{len(res_ids)} CHI reserve players, {len(wrong)} not marked out")
repl_bad = dc[dc["replacement"].map(lambda x: queries._s(x) is not None) & ~dc["starter"]]
check("'next man up' is only listed for injured starters", repl_bad.empty,
      f"{dc['replacement'].notna().sum()} replacements listed")

bt = queries.model_backtest(conn=conn)
if bt:
    f = bt["fit"]
    check("model backtest is out of sample (constants fit on an earlier season)",
          f["fit_season"] < bt["season"], f"fit {f['fit_season']}, tested {bt['season']}")
    check("model constants are sane: home field 0-3 pts, shrink 0.3-1.2, EPA->points 30-80",
          0 <= f["home_field"] <= 3 and 0.3 <= f["shrink"] <= 1.2 and 30 <= f["epa_to_points"] <= 80,
          str(f))
    ml = queries.model_lines(conn=conn)
    side_ok = all((row["model_side"] == row["home_team"]) == (row["spread_edge"] > 0)
                  for _, row in ml.iterrows() if queries._s(row["model_side"]))
    check("model side agrees with the sign of its gap (+ gap = home)", side_ok, f"{len(ml)} games")
    check("backtest games = every 2nd+ week regular-season game with a line",
          bt["mae"]["games"] >= 250, f"{bt['mae']['games']} games; model MAE {bt['mae']['model_margin']} "
          f"vs market {bt['mae']['market_margin']}")

lk = queries.luck_table(CURRENT_SEASON - 1, conn=conn)
check("league luck sums to ~0 and fumble recovery averages ~50%",
      abs(lk["luck_wins"].sum()) < 4 and 0.45 <= lk["fumble_rec"].mean() <= 0.55,
      f"sum of luck {lk['luck_wins'].sum():+.1f} wins, mean fumble recovery {lk['fumble_rec'].mean():.1%}")

r = conn.execute("""
    SELECT SUM(neutral_sec_sum) / SUM(neutral_sec_n) AS sec,
           1.0 * SUM(neutral_passes) / SUM(neutral_plays) AS npass,
           1.0 * SUM(explosives) / SUM(plays) AS expl
    FROM team_game_stats WHERE season = ? AND season_type = 'REG'
""", (CURRENT_SEASON - 1,)).fetchone()
check("pace looks like football: 25-40 s between snaps, 45-60% neutral passing, 6-12% explosive",
      25 <= r["sec"] <= 40 and 0.45 <= r["npass"] <= 0.60 and 0.06 <= r["expl"] <= 0.12,
      f"{r['sec']:.1f} s, {r['npass']:.1%} pass, {r['expl']:.1%} explosive")

# ---------------------------------------------------------------------------
# Game page: splits, logs, roster changes, depth grid, weather
# ---------------------------------------------------------------------------
from datetime import datetime, timezone
from sources.weather import pick_hour, kickoff_utc, roof_type

sp = queries.game_splits("BUF", "DET", "home", 4.5, "2026-09-17", conn=conn)
allg = sp[0]
w, l, p_ = (int(x) for x in (allg["record"].split("-") + ["0"])[:3])
check("game splits: 'All games' W+L+P equals games counted",
      w + l + p_ == allg["games"], f"{allg['record']} vs {allg['games']} games")
check("game splits: venue and role slices are never bigger than all games",
      all(r["games"] <= allg["games"] for r in sp if r["label"] != "Head to head"),
      ", ".join(f"{r['label']} {r['games']}" for r in sp))
h2h = queries._team_games(conn, "BUF", "2026-09-17")
h2h = h2h[(h2h["opponent"] == "DET") & (h2h["season"] >= queries.H2H_START)]
check("game splits: head to head counts only games vs that opponent",
      sp[-1]["games"] == len(h2h), f"{sp[-1]['games']} vs {len(h2h)}")

lg = queries.team_log("DET", conn=conn)
check("game log is newest first and covers only this season and last",
      lg["gameday"].is_monotonic_decreasing and set(lg["season"]) <= {queries.CURRENT_SEASON, queries.CURRENT_SEASON - 1},
      f"{len(lg)} games")

check("name matching treats Greg / Gregory and Jr. suffixes as the same player",
      queries.name_key("Greg Rousseau") == queries.name_key("Gregory Rousseau")
      and queries.name_key("Michael Penix Jr.") == queries.name_key("Michael Penix"))
rc = queries.roster_changes("BUF", conn=conn)
check("roster changes: new starters never exceed starting spots, lost starters were 50%+ players",
      len(rc["new_starters"]) <= rc["starters"] and all(x["share"] >= 0.5 for x in rc["lost_starters"]),
      f"{len(rc['new_starters'])} new of {rc['starters']}, {len(rc['lost_starters'])} lost")
cur = {queries.name_key(r["player_name"]) for r in conn.execute(
    "SELECT player_name FROM depth_chart WHERE team = 'BUF' AND side IN ('O','D')")}
gone = [x for x in rc["lost_starters"] if x["where"].startswith("now ") or x["where"] == "not on a roster"]
check("roster changes: players listed as gone aren't on the team's current depth chart",
      not any(queries.name_key(x["name"]) in cur for x in gone), f"{len(gone)} gone")

av = queries.availability("BUF", conn=conn)["depth"]
od = av[av["side"].isin(["O", "D"])]
check("depth grid: every offense/defense row has a slot, and each slot has one starter",
      od["pos_slot"].notna().all() and (od.groupby(["side", "pos_slot"])["pos_rank"].min().notna().all()),
      f"{len(od.groupby(['side', 'pos_slot']))} slots")

ko = kickoff_utc("2026-09-17", "20:15")
check("weather: 8:15 PM Eastern in September is 00:15 UTC the next day",
      ko == datetime(2026, 9, 18, 0, 15, tzinfo=timezone.utc), ko.isoformat())
hourly = {"time": ["2026-09-17T23:00", "2026-09-18T00:00", "2026-09-18T01:00"],
          "temperature_2m": [60, 61, 59], "wind_speed_10m": [10, 16, 12],
          "wind_gusts_10m": [15, 24, 18], "precipitation_probability": [30, 40, 50]}
h = pick_hour(hourly, ko)
check("weather: picks the forecast hour closest to kickoff, and none if the gap is over 90 min",
      h is not None and h["time"] == "2026-09-18T00:00" and h["wind_mph"] == 16
      and pick_hour(hourly, datetime(2026, 9, 18, 6, 0, tzinfo=timezone.utc)) is None, str(h))
check("weather: domes are skipped, retractable roofs and open stadiums are not",
      roof_type("DET", "Home", None, None) == "dome" and roof_type("ATL", "Home", None, None) == "retractable"
      and roof_type("BUF", "Home", None, "outdoors") == "open" and roof_type("BUF", "Home", None, "closed") == "dome")

conn.close()

# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
print("=" * 78)
print("ATS LOGIC + TEAM METRICS VERIFICATION")
print("=" * 78)
for status, name, detail in results:
    print(f"[{status}] {name}")
    if detail:
        print(f"        {detail}")
print("-" * 78)
failed = sum(1 for s, _, _ in results if s == FAIL)
print(f"{len(results) - failed}/{len(results)} checks passed")

# A worked example, so the splits table is visible rather than described.
print("\n" + "=" * 78)
print("SAMPLE OUTPUT -- Chicago Bears ATS splits, last 3 seasons")
print("=" * 78)
df = queries.ats_splits("CHI", n_seasons=3, through_season=2025)
print(df.to_string(index=False))
print("\nNote the 'n' and 'moe' columns. 'meaningful' is True only where the")
print("record is far enough from 50% that the sample size can support it.")

raise SystemExit(1 if failed else 0)

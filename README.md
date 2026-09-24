# 2026 NFL Betting Tool

## Website and automatic refreshes (GitHub)

- **Website:** https://marcuspeters310-sketch.github.io/NFL-Betting-Tool/
- **Scheduled refreshes (Chicago time):** Monday–Friday 3:30 PM; Sunday 8:00 AM,
  11:00 AM, 2:30 PM and 6:00 PM. Defined in `.github/workflows/refresh.yml`,
  which lists each time twice (daylight and standard time) and skips the copy
  that doesn't match the current clock. GitHub may start runs 5–30 minutes late.
- **Manual refresh:** Actions tab → **Refresh NFL Board** → **Run workflow**
  (works from the GitHub mobile app too).
- **Each run:** `backfill.py` → `verify.py` (any `[FAIL]` stops the run, so the
  site keeps its last good version) → `export_board.py` → `build_site.py`
  (adds the page wrapper GitHub Pages needs) → publish.
- **Database:** kept between runs in GitHub's Actions cache; `data/nfl.db` in
  the repository is only the starting copy if the cache is empty.
- **Keep-alive:** each run commits `data/last_refresh.txt`, because GitHub
  pauses schedules in public repositories after 60 days without a commit.
- **Weather:** `sources/weather.py` pulls the kickoff-hour forecast from
  Open-Meteo (free, no key) for games in the next 10 days, stored in
  `weather_forecasts`. Domes are skipped; retractable roofs are fetched.
  Flags: wind 15+ mph or 50%+ chance of rain (`config.py`). A failed fetch
  keeps the last good forecast.

## The website (Sept 16 redesign; week visibility + results Sept 23; opening-day depth chart Sept 23)

- **Which weeks show up:** the current week (the earliest one with an
  unplayed game) is always there. Next week joins it every day except the
  one Tuesday right after this week's Monday Night Football ends -- by
  Wednesday, next week's tiles are visible too, all the way through its own
  Monday night, at which point it becomes "current" and the week after it
  takes the "next week" slot. Logic: `export_board.show_next_week` /
  `determine_weeks`. Every week that's fully played gets a **results**
  entry in the same week switcher (see below).
- **This week / next week tiles:** each tile opens **condensed** -- teams,
  kickoff, the spread, and each team's last-3-games ATS and O/U record --
  so several fit on a phone screen at once. Tap **More ▼** to expand to the
  full tile: the Spread / Over-Under switch, weather, and the offense-vs-
  defense matchup rows (away team on the left, home on the right).
  - Spread: off EPA vs def EPA allowed, points scored vs allowed, hurt
    starters (offense vs defense), ATS record + average margin for 2026 and
    the last 3 / 5 / 10 games.
  - Over/Under: points matchups, a points estimate (the average of the two
    scoring rows vs the total, not a model), pace, hurt starters, O/U record
    + average points vs the total for the same windows.
  - Weather strip with the flag.
- **Results weeks:** once a week is fully played it switches to a light
  results view -- final score plus what actually happened on the field
  (EPA/play, success rate, yards, red zone and third-down rate, giveaways,
  sacks, explosive-play rate for both teams, from `queries.game_box_score`,
  a plain read of that game's `team_game_stats` row -- not the blended,
  ranked season metrics). No spread, no cover, no model: on purpose, so the
  history reads as "what happened" rather than "how the bet went."
- **Hurt last game:** the official injury report only exists Wednesday to
  Friday. Before that, the Injuries tab (game page) and each team's
  availability panel (Teams page) show who the play-by-play flagged as
  injured in that team's most recently completed game -- "Returned to that
  game" or "Did not return" -- parsed straight out of the same play-by-play
  download already used for team stats (`sources.pbp.parse_injuries`,
  `game_injury_events` table, `queries.hurt_last_game`). It's the NFL's own
  gamebook charting, not a diagnosis: it knows what happened on the field
  that day, nothing about a Monday MRI or a setback at practice. Suppressed
  automatically once that week's real report is out.
- **Depth chart and injuries is frozen to Week 1, not today:** the depth
  chart shown (Teams page and the game page's Roster tab) is the roster as
  it stood right before Week 1 kicked off (the first ESPN depth-chart
  snapshot on/after that season's roster cutdown -- `sources.rosters.
  _opening_day_anchor`, stored in the new `depth_chart_opening` table),
  never today's already-reshuffled one. Every player's *current* status is
  overlaid on top of that fixed structure: hurt, on a reserve list, or
  tagged **GONE** if they've left the roster entirely (released, retired,
  traded -- something the injury report alone won't show). The point is to
  see, at a glance, how much a team's opening lineup has actually been
  depleted over the season, rather than the board quietly "moving on" to
  whoever's playing that slot now. (The separate "key changes vs last
  season" panel below still compares against the *latest* depth chart on
  purpose -- that one's about season-over-season turnover, not in-season
  attrition, and would be wrong if it used the frozen Week 1 chart too.)
- **Game page** (`#game=<id>&tab=...`, opened with "Details ›"):
  - Game logs: this season and last, playoffs included, with ATS and O/U
    results and margins (`queries.team_log`).
  - Roster: key changes vs last season (QB, head coach, new starters, 50%+
    snap starters who left, from `queries.roster_changes`, against the
    latest depth chart), the "Hurt last game" note above when it applies,
    then an ESPN-style depth chart frozen to Week 1 (Starter / 2nd / 3rd /
    4th) with current-status injury tags; tap a tagged player for details.
  - ATS and O/U: all games (last 3 seasons), this venue, this role
    (favorite / underdog), venue + role, head to head since 2015
    (`queries.game_splits`), plus a cover-margin / points-vs-total bar chart
    covering every game since the start of 2025 (`CHART_SINCE_SEASON` in
    `config.py`), most recent game at the top.
- The model table, "most lopsided matchups" and "hurt starters" lists were
  removed from the homepage in the redesign; the Teams view is unchanged
  (its own "last 10 games" trend chart is still a fixed 10-game window).
- Roster matching between data sources uses loose names (Greg = Gregory,
  suffixes dropped, unique first-initial + last-name fallback). It can still
  miss a player now and then.

## Local-only files

`app.py`, `show.py` and `Run NFL Board.bat` are not in
this repository. The references to them below apply to the Windows copy.

Phase 1: the data foundation. Every NFL game since 1999 with its closing
spread, closing total and final score, loaded into a local SQLite database,
plus the query layer that turns that into against-the-spread records.

Phase 3 (Sept 16): team metrics. EPA, success rate, points, yards and
situational stats for every team, blended across last season and this one,
ranked 1-32, and shown as offense-vs-defense matchups on every game card.

It runs as a local web app in your browser, plus a command-line version for
quick checks.

Sept 16 update: depth charts with injury status, ATS/over-under trend windows
with cover margins and a last-10-games chart, a model spread and total with an
honest backtest, luck/regression flags, and a pace/totals profile.

---

## Setup (Windows)

Open PowerShell in this folder and run:

```
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Then build the database:

```
python backfill.py
```

The first run takes about a minute and downloads roughly 22 MB (2 MB of games,
20 MB of 2025 play-by-play). After that it takes a few seconds, because last
season is only downloaded once. You should see `7,548 games, seasons 1999-2026`
followed by the play-by-play seasons it loaded.

A prebuilt `data\nfl.db` is already included, so you can skip straight to the
commands below if you'd rather look before you run anything.

> If you see a zero-byte `data\nfl.db-journal` file, delete it. It's a leftover
> from building the database over the network share and has no effect either way.

---

## Open the tool

```
streamlit run app.py
```

It opens at `http://localhost:8501` in your browser. Or just double-click
**`Run NFL Board.bat`** — same thing, no typing. It also runs `backfill.py`
first, so every launch pulls in the latest scores and play-by-play. If you're
offline, it skips the refresh and opens with what you already have. Close the
black console window to stop it.

Two views, switched from the sidebar:

- **This week** — every game in kickoff order, with the spread, total, roof and
  rest days. Click a game to expand it and see:
  - **Matchup tables**: each team's offense against the other's defense, ten
    stats each, with league ranks. The Edge column flags gaps of 12+ places.
  - Both teams' ATS and over/under records, plus their full splits side by side.
- **Team explorer** — one team's full metric ranks (offense and defense), then
  all seventeen ATS splits, with the season slider controlling how far back
  the splits look.

### What each game card shows (Sept 16 additions)

Every game has four tabs:

| Tab | What's in it |
|---|---|
| **Matchup** | Offense-vs-defense tables, now including explosive play rate, plus each team's luck check |
| **Injuries** | Depth charts for offense and defense with status tags, and a card for every player who isn't fully available |
| **ATS & trends** | Cover record in eight windows, with average and median cover margin, plus a last-10-games chart |
| **Totals** | Model total vs. the market, pace and style ranks, and over/under windows with average points vs. the total |

Above the tabs, each game shows the **model line** and **model total** next to
the market.

**Injury tags.** OUT (or IR / PUP / NFI for reserve lists), D = doubtful,
Q = questionable, LP = limited or no practice but no game status. Each injured
player shows:
- the injury and their practice participation
- their **snap share**, i.e. how big a role they have (this season if they've
  played, otherwise last season)
- for starters, the **next man up** and that player's snap share

The injury report for the upcoming week only exists once teams file it
(Wednesday to Friday). Until then the board shows last week's report and says so.

**Cover margins.** "Avg +2.4" means the team beat the spread by 2.4 points per
game over that window. The median is the middle game, so one blowout can't
move it much. Last 3/5/10 include playoffs and span seasons; the "since 2023"
rows cover the last three seasons plus this one.

**Model line.** A deliberately simple power rating:

```
rating       = ½ × (points scored − allowed per game) + ½ × (net EPA/play × 47)
model margin = 0.76 × (home rating − away rating) + 1.67 home field
```

The three constants are fit on 2024 games. The model is then tested on every
2025 game, using only what was known before each kickoff. **Results: 128-141-1
(47.6%) against closing lines.** Its average miss on the final margin was 10.3
points, against the closing line's 9.7. It does not beat the market on its own,
and the board says so. Use a big gap as a reason to dig in: an injury the
number doesn't know about, a luck-driven record, a QB change.

**Luck check.** Compares a team's record with how it actually played:
- Wins expected from points scored and allowed (Pythagorean, exponent 2.37)
- Record in one-score games (8 points or fewer)
- Turnover margin per game
- The share of all fumbles in its games that it recovered (league average 50%)

"Regression risk" flags a team whose record is running ahead of its play;
"better than the record" flags the reverse. Flags need 4+ games.

**Pace.** Pace stats describe style, not quality, so their ranks aren't colored:
- Seconds between snaps (in neutral situations: win probability 20-80%,
  outside the last two minutes of each half)
- Plays per game
- Pass rate on 1st and 2nd down in neutral situations

Two fast, pass-heavy teams mean more possessions and more points.

### Reading the team metrics

**Rank 1 is always the best**, on both sides of the ball. The best defense is
the one that allows the *least*, and it's ranked #1. So "DET offense #3 vs BUF
defense #28" always means a strong unit facing a weak one, whatever the stat.

**The blend.** One week of 2026 is one game per team, which is noise. So each
number is a weighted mix:

```
blended = w × 2026 + (1 − w) × 2025        w = 2026 games played ÷ 6, max 100%
```

| 2026 games played | 2026 share | 2025 share |
|---|---|---|
| 1 | 17% | 83% |
| 3 | 50% | 50% |
| 6+ | 100% | 0% |

Each team gets its own weight, so a team coming off a bye leans slightly more
on last season. Every table shows the mix it used underneath. To make 2026 take
over faster or slower, change `BLEND_FULL_AT_GAMES` in `config.py`.

**As of kickoff.** A game card for Week 5 uses only Weeks 1-4, which is what
you'd have known when betting it. Looking back at an old week shows the numbers
as they stood then, not with hindsight.

**The stats.** Only standard plays count: passes (including sacks and
scrambles) and designed runs. Kneel-downs, spikes, special teams, two-point
tries and plays wiped out by penalties are excluded.

| Stat | What it means |
|---|---|
| EPA / play | Expected points added: how much each play changed the points the offense could expect to score on that drive. +0.10 is elite, −0.10 is bad. |
| Success rate | The share of plays with positive EPA. It's steadier than EPA because one 80-yard bomb can't inflate it. |
| Points / Yards per game | The box-score numbers. Points come from the final score, so they include defensive and special-teams scores. |
| Red zone TD % | Drives that ran a play at the opponent's 20 or closer and ended in a touchdown. |
| 3rd down % | Third-down plays that got a first down or touchdown. |
| Giveaways / Takeaways | Interceptions plus lost fumbles on offensive plays, per game. |
| Sacks | Sacks taken by the offense, and made by the defense, per game. |

Team metrics currently cover 2025 and 2026. Older seasons show the note "No
play-by-play loaded". The loader could go back further, but the board doesn't
need it.

The spreads and totals for upcoming games come from nflverse, which publishes
lines alongside the schedule. That's why the board is useful before any odds
API exists. Live multi-book prices, best-price shopping and line movement
arrive in Phase 2.

---

## The website

The board is also a private web page on claude.ai (named **NFL Betting Board**
in your artifacts), so you can open it on your phone and share it with friends
from the page's share menu. It has the same two views, This week and Teams,
plus last week's results with the cover and over/under for each game.

It's a **snapshot**, so it doesn't update by itself. To refresh it, ask Claude
to "refresh the NFL board". The refresh is three steps:

```
python backfill.py        # latest scores, lines, play-by-play
python export_board.py    # rebuilds board_data.json and web\NFL Board.html
```

then Claude republishes `web\NFL Board.html` to the same link
(https://claude.ai/artifact/XPTUF5gVMnYzc54us8XGqX).

`web\board_template.html` is the page design. `export_board.py` bakes the data
into it, so `web\NFL Board.html` also opens fine straight from your folder.

---

## Command line

```
python verify.py              # 74 checks — run this first
python show.py slate          # this week's games in kickoff order
python show.py slate 2025 12  # any past week, with results and ATS outcomes
python show.py team CHI       # Bears ATS splits, last 3 seasons
python show.py team KC 5      # any team, any number of seasons
python show.py ou NE 11       # Patriots over/under in November
python show.py ranks          # EPA ranks for all 32 teams, with the blend used
python show.py matchup DET BUF   # both offense-vs-defense tables for a game
python show.py injuries BUF   # depth chart with injury tags, who's hurt, next man up
python show.py trends BUF DET # trend windows with cover margins, last 10 games
python show.py model          # model vs market for the week + the 2025 backtest
python show.py luck 2025      # luck / regression flags for every team
python show.py status         # when each data job last ran
```

That last-but-one command is your Screen 3 example question, already answerable
from the database:

```
NE over/under, 2021-2025, month 11
  7 overs, 11 unders, 0 pushes
  38.9% over  (n=18, margin of error +/-22.5 pts)
  Distinguishable from 50/50? NO
```

Eleven unders in eighteen November games looks like a system. With eighteen
games the margin of error is ±22.5 points, so the true rate is somewhere
between 16% and 61%. That's the whole reason every function in `queries.py`
returns `n` alongside the percentage.

---

## What each file does

| File | Role |
|---|---|
| `config.py` | Paths, URLs, season constants, `CHART_SINCE_SEASON` for the ATS chart. One place to change anything. |
| `db.py` | Connection, table definitions (including `game_injury_events`, `depth_chart_opening`), and the derived SQL views. The interesting part. |
| `sources/nflverse.py` | Downloads the nflverse games file and upserts it. Safe to re-run any time. |
| `sources/pbp.py` | Downloads play-by-play and adds it up to one row per team per game (`team_game_stats`), including pace, neutral pass rate, explosives and fumbles. Also regexes "was injured during the play" / "has returned to the game" out of the same download into `game_injury_events` (`parse_injuries`). Loads 2024-2026 (2024 only for the model backtest). |
| `sources/rosters.py` | Depth charts -- both the latest daily snapshot (`depth_chart`) and the frozen Week 1 one (`depth_chart_opening`, anchored to that season's roster cutdown via `_opening_day_anchor`, both from the same download) -- injury reports, weekly roster status (IR, PUP, NFI), snap counts. |
| `backfill.py` | Builds the database. Run again after any Sunday to pull in results. Run with `--full` once after upgrading to this feature, so `game_injury_events` backfills for already-cached seasons. |
| `queries.py` | Every number the screens display comes from here: `team_metrics()`, `matchup_table()`, `ats_angles()`, `recent_games()` (last-N, or `since_season=` for the full ATS chart), `game_box_score()` (a played game's real stats, for the results view), `availability()` (depth chart frozen to `depth_chart_opening`, current status overlaid, `"gone"` status for anyone who's left the roster) / `hurt_last_game()`, `roster_changes()` (year-over-year turnover, deliberately still against `depth_chart`'s latest snapshot), `model_lines()`, `model_backtest()`, `luck_table()`. |
| `verify.py` | 74 checks: ATS logic, team metrics, cover margins, injury overlay, in-game injury parsing, week-visibility rules, results-view stats, model (out-of-sample, sane constants), luck, pace. Re-run after any change. |
| `app.py` | **Screen 1.** The board itself. `streamlit run app.py` |
| `Run NFL Board.bat` | Double-click launcher for Windows. |
| `export_board.py` | Builds the website snapshot: `board_data.json` + `web\NFL Board.html`. `determine_weeks()` / `show_next_week()` decide which weeks are upcoming vs. results. |
| `web\board_template.html` | The website's design. The data gets inserted at `__BOARD_DATA__`. |
| `show.py` | Command-line version of the same data, handy for spot checks. |
| `data/nfl.db` | The database. About 2.6 MB. |

---

## Three things worth understanding before Phase 2

**1. The spread sign convention.** `spread_line` is always from the *home*
team's point of view, and positive means the home team is favored. `result` is
the home team's margin. Because both are on the same scale, covering is just
`result - spread_line`: positive means the home team covered, zero is a push,
negative means the away team covered. Getting this backwards produces
plausible-looking numbers that are all wrong, which is why `verify.py` checks it
three separate ways.

**2. `v_team_games` is the view you'll actually query.** The raw data has one
row per game with a home team and an away team. Nearly every question you want
to ask is about one team ("how are the Bears doing on the road"). That view
flips each game into two rows — one per team — with the spread, the margin and
the ATS result already stated from that team's perspective. After that, every
team query is a plain `WHERE` clause instead of a pile of `CASE` statements.

**3. Nothing derived is stored.** The tables hold only what nflverse publishes.
Every ATS and over/under calculation lives in a view. When nflverse issues a
stat correction — they run through Wednesday most weeks — you re-run
`backfill.py` and every derived number updates itself. There's no second copy of
the truth to drift.

---

## Already built for Phase 2

Two things exist ahead of when they're needed:

- **`line_snapshots`** is created and empty. When odds ingestion arrives, it
  appends rather than overwrites. Historical odds cost 10× normal on The Odds
  API, so every snapshot taken from week one is data you'd otherwise buy.
- **`ingest_runs`** logs every loader run with a status and row count. That's
  what will drive the "last refreshed" indicator, and how you find out a
  scheduled job has been quietly failing.

---

## Notes

- `NFL_DB_PATH` overrides the database location if you ever need it somewhere
  other than `data\nfl.db`. Required if the project folder sits on a network
  drive, since SQLite needs real file locking.
- `team_game_stats` stores counts, not rates (plays, total EPA, third-down
  attempts and conversions...). Rates are calculated when you ask for them, so
  combining seasons adds up the counts and then divides. Averaging two averages
  would give the wrong answer.
- New columns are added to an existing database automatically (`MIGRATIONS` in
  `db.py`), and play-by-play seasons missing them reload on the next backfill.
- Blank values can come back from pandas as `None` or `NaN` depending on the
  version. Use `queries._s()` for text and never test `if value:` directly.
  `export_board.py` refuses to write NaN, because it would break the web page.
- Run `python backfill.py --full` to force last season's play-by-play to
  download again, for example after a late nflverse stat correction.
- The 2026 season is already in the database with opening lines for Week 1.
- **"Hurt last game" is a heuristic, not the injury report.** It only exists
  because the NFL's own play-by-play charting happens to write "was injured
  during the play" / "has returned to the game" into the text of the play --
  there's no separate free real-time injury feed. It can miss a player if the
  charting ever uses different wording, and "did not return" is a flag to dig
  in, not a diagnosis. The official Wednesday-Friday report is still the
  source of truth and replaces this note automatically once it's filed.
- `game_injury_events` only backfills for seasons `pbp.py` actually
  re-downloads. Existing (cached) completed seasons won't have it until you
  run `python backfill.py --full` once.
- **`depth_chart_opening` fills itself in retroactively.** ESPN's daily depth
  chart snapshots for a season cover the whole year, offseason included, so
  the very first `python backfill.py` run after upgrading to this feature
  computes the Week 1 snapshot correctly even mid-season -- no `--full` or
  backfill needed for this one. If a season's schedule (and therefore its
  Week 1 kickoff date) hasn't loaded yet, that season's opening depth chart
  is simply skipped until it does.
- The depth chart shown on the board is deliberately the frozen Week 1 one
  (`depth_chart_opening`), not the live daily one (`depth_chart`) -- see "The
  website" section above. `queries.roster_changes()` (the separate "key
  changes vs last season" panel) still reads the live one on purpose.

---

*Data: [nflverse](https://github.com/nflverse/nfldata). Free, no API key.*

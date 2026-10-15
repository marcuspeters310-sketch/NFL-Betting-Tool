# NFL Betting Board — notes for Claude Code

Marcus's personal NFL betting-research tool: Python + SQLite, free nflverse data,
published to GitHub Pages by GitHub Actions. `README.md` is the full reference;
read it before changing anything. Marcus reviews the code, so explain changes in
plain language with concrete numbers. He will not pay for any service.

## Local workflow (Windows)

```powershell
$env:NFL_DB_PATH = (Resolve-Path .local\nfl.db).Path   # work db; never touch data\nfl.db
.\.venv\Scripts\python.exe backfill.py       # fresh nflverse data (~15 s)
.\.venv\Scripts\python.exe verify.py         # every line must be [PASS]; it never exits nonzero, so grep for [FAIL]
.\.venv\Scripts\python.exe export_board.py   # board_data.json + web\NFL Board.html
.\.venv\Scripts\python.exe build_site.py     # _site\index.html
.\.venv\Scripts\python.exe -m http.server 8765 --bind 127.0.0.1 --directory _site   # preview
```

- `.venv` is Python 3.14 with `requirements.txt` (pandas 3.0.5). GitHub Actions uses 3.13.
- `.local\` holds the working database copy and is git-ignored. `data\nfl.db` is
  only a seed; the live database lives in the GitHub Actions cache.
- Check the site at 400px wide too: no console errors, no horizontal scroll, open
  games and each tab.
- Ask Marcus before every push. Pushing to `main` changes the live site on the
  next workflow run.
- The `Refresh NFL Board` workflow commits `data/last_refresh.txt` to `main`
  several times a week, so pull before branching or pushing.

## Rules that must not break

- **Spreads:** `spread_line` is from the home team's view, positive = home favored.
  `result` = home margin; cover = result - spread_line. Team view:
  ats_margin = margin - team_spread (+ = covered by that much).
- **Honest samples:** every percentage shows n and a 95% margin of error. Call it
  a signal only when n >= 10 and the gap from 50% exceeds the margin of error.
- **Storage:** store counts, not rates. Defense = the opponent's offense row.
- **Ranks:** 1 = best for offense and defense. Pace ranks are neutral and never
  colored good/bad. Edge = rank gap of 12+.
- **Blend:** w x this season + (1 - w) x last season, w = games played / 6 (max 1).
  A game card uses only games before that game.
- **Model:** present it as a reason to dig in, never as a pick. Constants are fit
  on season - 2 and backtested out of sample on last season.
- **pandas 3:** blanks arrive as NaN (truthy). Use `queries._s()` for text; never
  write `x or default` or `x is None` on DataFrame values.
- **Colors:** covered/over = #2F6DB5 (dark #5B93D6), missed/under = #C2601A
  (dark #D0742F). Not red/green (colorblind safety).
- **Website:** template has no doctype (build_site.py adds it); Google Fonts only
  (Saira Condensed, IBM Plex Sans, IBM Plex Mono); must work at 400px wide; don't
  reuse the `.top` class.
- **Workflow cron:** GitHub cron is UTC with no daylight saving, so each Chicago
  time is listed twice (CDT and CST) and the "Check the clock" step picks one.
  Every cron string must stay unique.
- **Secrets** (API keys) go in GitHub repo Settings → Secrets, added by Marcus.
  Never hardcode them or ask for them in chat.

## Making a change

1. Put SQL and calculations in `queries.py`; display them via `export_board.py`
   and `web/board_template.html`.
2. Add `verify.py` checks for new logic (signs, rank direction, sample sizes).
3. Run the full local workflow above and preview the site.
4. Update `README.md`.
5. Show Marcus the result, then commit and push with his OK.

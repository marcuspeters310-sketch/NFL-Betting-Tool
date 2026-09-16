"""
Build the shareable web version of the board.

    python export_board.py

Reads the database (run backfill.py first for fresh data) and writes:

  board_data.json        everything the page shows, as one JSON file
  web/NFL Board.html     web/board_template.html with that data baked in

The HTML file is fully self-contained: open it in any browser, or have Claude
publish it as a private claude.ai page you can open on your phone and share.
No Python runs behind it, so it's a snapshot as of the moment you exported.
"""
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

import queries
from config import CURRENT_SEASON, PROJECT_ROOT, WIND_FLAG_MPH, RAIN_FLAG_PCT

THROUGH = CURRENT_SEASON - 1   # ATS splits use completed seasons only
N_SEASONS = 3
TEMPLATE = PROJECT_ROOT / "web" / "board_template.html"
OUTPUT = PROJECT_ROOT / "web" / "NFL Board.html"


def num(x):
    """JSON-safe number: None for missing/NaN, plain float/int otherwise."""
    if x is None:
        return None
    try:
        if pd.isna(x):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(x, float) and math.isnan(x):
        return None
    return int(x) if float(x).is_integer() else float(x)


def text(x):
    return None if x is None or (not isinstance(x, str) and pd.isna(x)) else str(x)


def rec(d: dict) -> dict:
    """Trim a record dict down to what the page displays."""
    return {"record": d["record"], "n": d["n"], "pct": d["pct"],
            "moe": d["moe"], "sig": d["beats_coinflip"]}


def metrics_payload(m: pd.DataFrame, season: int) -> dict:
    """{team: {"note": ..., "v": {key: [formatted value, rank]}}}"""
    out = {}
    for team in m.index:
        r = m.loc[team]
        vals = {}
        for key, _label, fmt, _hb in queries.METRICS:
            v, rk = r[key], r[f"{key}_rank"]
            vals[key] = [None if pd.isna(v) else fmt.format(v),
                         None if pd.isna(rk) else int(rk)]
        out[team] = {"note": queries.blend_note(m, team, season), "v": vals}
    return out


def angles_payload(df: pd.DataFrame) -> list:
    keys = ["angle", "this_game", "games", "record", "n", "pct", "moe", "sig",
            "avg_margin", "median_margin", "ou_record", "ou_n", "over_pct", "ou_moe",
            "ou_sig", "avg_total_vs_line"]
    return [{k: (num(r[k]) if isinstance(r[k], (int, float)) and not isinstance(r[k], bool) else
                 (bool(r[k]) if isinstance(r[k], bool) else r[k])) for k in keys}
            for r in df.to_dict("records")]


def recent_payload(df: pd.DataFrame) -> list:
    return [{
        "d": r["gameday"], "opp": r["opponent"], "home": r["side"] == "home",
        "pf": num(r["points_for"]), "pa": num(r["points_against"]),
        "line": num(r["team_spread"]), "m": num(r["ats_margin"]), "ats": r["ats"],
        "ou": text(r["ou_result"]), "post": r["game_type"] != "REG",
        "tot": num(r["total"]), "tl": num(r["total_line"]),
    } for r in df.to_dict("records")]


def availability_payload(av: dict) -> dict:
    def share(x):
        return None if x is None or pd.isna(x) else round(float(x), 3)
    depth = []
    for r in av["depth"].to_dict("records"):
        if r["side"] == "ST":
            continue
        row = {"s": r["side"], "p": r["pos_abb"], "r": int(r["pos_rank"]),
               "sl": num(r.get("pos_slot")), "f": text(r.get("formation")),
               "st": bool(r["starter"]), "n": text(r["player_name"]) or "(unnamed)", "x": r["status"]}
        if r["status"] != "active":
            row.update({"rs": text(r["report_status"]), "inj": text(r["injury"]),
                        "pr": text(r["practice_status"]), "sh": share(r["snap_share"]),
                        "sb": text(r["snap_basis"]), "rp": text(r["replacement"]),
                        "rsh": share(r["replacement_share"])})
        depth.append(row)
    def plain(df, cols):
        return [] if df.empty else [
            {c: (share(r[c]) if c == "snap_share" else text(r[c])) for c in cols}
            for r in df.to_dict("records")]
    return {
        "week": av["week"], "report_week": av["report_week"],
        "current": bool(av["report_is_current"]),
        "snapshot": (av["snapshot_at"] or "")[:10],
        "depth": depth,
        "reserve": plain(av["reserve"], ["player_name", "position", "list", "snap_share", "snap_basis"]),
        "other": plain(av["other_injuries"], ["player_name", "position", "status", "report_status",
                                              "injury", "practice_status", "snap_share", "snap_basis"]),
    }


def splits_payload(rows: list) -> list:
    return [{"label": r["label"], "sub": r["sub"],
             "ats": {"rec": r["record"], "n": r["n"], "moe": num(r["moe"]), "sig": bool(r["sig"]),
                     "m": num(r["avg_margin"])},
             "ou": {"rec": r["ou_record"], "n": r["ou_n"], "moe": num(r["ou_moe"]), "sig": bool(r["ou_sig"]),
                    "m": num(r["avg_total_vs_line"])}} for r in rows]


def log_payload(df: pd.DataFrame) -> list:
    return [{
        "s": int(r["season"]), "wk": int(r["week"]), "gt": r["game_type"], "d": r["gameday"],
        "opp": r["opponent"], "home": r["side"] == "home",
        "pf": num(r["points_for"]), "pa": num(r["points_against"]),
        "line": num(r["team_spread"]), "am": num(r["ats_margin"]), "ats": text(r["ats"]),
        "tl": num(r["total_line"]), "tot": num(r["total"]), "ou": text(r["ou_result"]),
    } for r in df.to_dict("records")]


def weather_payload(w: dict) -> dict:
    return {"roof": w["roof"], "temp": num(w["temp"]), "wind": num(w["wind"]),
            "gust": num(w["gust"]), "precip": num(w["precip"]), "flag": w["flag"],
            "src": w["source"], "at": w.get("fetched")}


def luck_payload(l: pd.DataFrame, team: str):
    if l.empty or team not in l.index:
        return None
    r = l.loc[team]
    return {"games": int(r["games"]), "record": r["record"], "pythag": num(r["pythag_wins"]),
            "luck": num(r["luck_wins"]), "close": r["close_record"],
            "to": num(r["to_margin_pg"]), "fum": num(r["fumble_rec"]), "fumbles": int(r["fumbles"]),
            "flags": [[k, t] for k, t in r["flags"]]}


def game_payload(g) -> dict:
    played = pd.notna(g["result"])
    return {
        "id": g["game_id"], "date": g["gameday"], "weekday": g["weekday"],
        "time": g["gametime"], "away": g["away_team"], "home": g["home_team"],
        "spread": num(g["spread_line"]), "total": num(g["total_line"]),
        "roof": text(g["roof"]), "stadium": text(g["stadium"]), "neutral": g["location"] == "Neutral",
        "div": int(num(g["div_game"]) or 0), "prime": int(num(g["is_primetime"]) or 0),
        "away_rest": num(g["away_rest"]), "home_rest": num(g["home_rest"]),
        "final": {
            "away": num(g["away_score"]), "home": num(g["home_score"]),
            "home_ats": text(g["home_ats"]), "ou": text(g["ou_result"]),
        } if played else None,
    }


def main() -> None:
    conn = queries.connect(read_only=True)

    current = int(queries.week_slate(CURRENT_SEASON, None, conn=conn).iloc[0]["week"])
    # The week you're betting, plus last week's results for a look back.
    week_list = [w for w in (current, current - 1) if w >= 1]

    weeks = []
    for w in week_list:
        slate = queries.week_slate(CURRENT_SEASON, w, conn=conn)
        m = queries.team_metrics(CURRENT_SEASON, w, conn=conn)   # as of that kickoff
        model = queries.model_lines(CURRENT_SEASON, w, conn=conn)
        model = model.set_index("game_id") if not model.empty else model
        games = []
        for _, g in slate.iterrows():
            gp = game_payload(g)
            if len(model) and g["game_id"] in model.index:
                mr = model.loc[g["game_id"]]
                gp["model"] = {k: num(mr[k]) for k in (
                    "model_margin", "market_margin", "spread_edge", "model_total",
                    "market_total", "total_edge", "home_rating", "away_rating")}
                gp["model"].update({"side": text(mr["model_side"]), "lean": text(mr["total_lean"])})
            gp["wx"] = weather_payload(queries.game_weather(g.to_dict(), conn=conn))
            spread = num(g["spread_line"])
            gp["splits"] = {
                side: splits_payload(queries.game_splits(
                    g[f"{side}_team"], g[("home" if side == "away" else "away") + "_team"], side,
                    None if spread is None else (spread if side == "home" else -spread),
                    g["gameday"], conn=conn))
                for side in ("away", "home")
            }
            gp["trends"] = {
                side: {
                    "angles": angles_payload(queries.ats_angles(
                        g[f"{side}_team"], g[("home" if side == "away" else "away") + "_team"],
                        side, g["gameday"], conn=conn)),
                    "recent": recent_payload(queries.recent_games(g[f"{side}_team"], g["gameday"], 10, conn=conn)),
                } for side in ("away", "home")
            }
            games.append(gp)
        weeks.append({
            "week": w,
            "games": games,
            "metrics": metrics_payload(m, CURRENT_SEASON) if not m.empty else {},
            "luck": {t: luck_payload(queries.luck_table(CURRENT_SEASON, w, conn=conn), t)
                     for t in m.index} if not m.empty else {},
            "avail": {t: availability_payload(queries.availability(t, CURRENT_SEASON, w, conn=conn))
                      for t in set(slate["home_team"]) | set(slate["away_team"])},
        })

    # Game-page extras for every team on an exported slate: game logs for this
    # season and last, and what changed on the roster since last season.
    slate_teams = sorted({t for wk in weeks for g in wk["games"] for t in (g["away"], g["home"])})
    teamx = {t: {"log": log_payload(queries.team_log(t, conn=conn)),
                 "changes": queries.roster_changes(t, conn=conn)} for t in slate_teams}

    luck_now = queries.luck_table(CURRENT_SEASON, None, conn=conn)
    luck_prior = queries.luck_table(CURRENT_SEASON - 1, None, conn=conn)
    teams = {}
    all_teams = [r["team"] for r in conn.execute(
        "SELECT DISTINCT team FROM team_game_stats WHERE season = ? ORDER BY team",
        (CURRENT_SEASON,))]
    for t in all_teams:
        splits = queries.ats_splits(t, N_SEASONS, THROUGH, conn=conn)
        teams[t] = {
            "ats":      rec(queries.ats_record(t, N_SEASONS, THROUGH, None,   conn=conn)),
            "ats_home": rec(queries.ats_record(t, N_SEASONS, THROUGH, "home", conn=conn)),
            "ats_away": rec(queries.ats_record(t, N_SEASONS, THROUGH, "away", conn=conn)),
            "ou":       rec(queries.ou_record(t, N_SEASONS, THROUGH, None,    conn=conn)),
            "splits": [
                {"k": r["split"], "r": r["record"], "n": int(r["n"]),
                 "p": num(r["pct"]), "m": num(r["moe"]), "s": bool(r["meaningful"]),
                 "am": num(r["avg_margin"]), "md": num(r["median_margin"])}
                for _, r in splits.iterrows()
            ],
            "angles": angles_payload(queries.ats_angles(t, conn=conn)),
            "recent": recent_payload(queries.recent_games(t, None, 10, conn=conn)),
            "luck_now": luck_payload(luck_now, t),
            "luck_prior": luck_payload(luck_prior, t),
        }

    latest = queries.team_metrics(CURRENT_SEASON, None, conn=conn)
    backtest = queries.model_backtest(conn=conn)
    last_game = conn.execute(
        "SELECT MAX(gameday) AS d FROM games WHERE season = ? AND result IS NOT NULL",
        (CURRENT_SEASON,)).fetchone()["d"]
    conn.close()

    payload = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "season": CURRENT_SEASON, "current_week": current,
        "results_through": last_game,
        "n_seasons": N_SEASONS, "through": THROUGH,
        "metric_defs": {
            "offense": [[k, label, hb] for k, label, _f, hb in queries.OFFENSE],
            "defense": [[k, label, hb] for k, label, _f, hb in queries.DEFENSE],
        },
        "weeks": weeks,
        "teams": teams,
        "teamx": teamx,
        "latest_metrics": metrics_payload(latest, CURRENT_SEASON) if not latest.empty else {},
        "pace_defs": [[k, label] for k, label, _f, _hb in queries.PACE],
        "backtest": backtest,
        "weather_flags": {"wind": WIND_FLAG_MPH, "rain": RAIN_FLAG_PCT},
    }

    # NaN is not valid JSON and would stop the web page from loading, so clean
    # it out everywhere and make json.dumps refuse anything that slipped by.
    def clean(x):
        if isinstance(x, dict):
            return {k: clean(v) for k, v in x.items()}
        if isinstance(x, (list, tuple)):
            return [clean(v) for v in x]
        if isinstance(x, float) and math.isnan(x):
            return None
        if hasattr(x, "item") and not isinstance(x, (str, bytes)):   # numpy scalars
            return clean(x.item())
        return x
    blob = json.dumps(clean(payload), separators=(",", ":"), allow_nan=False)
    (PROJECT_ROOT / "board_data.json").write_text(blob, encoding="utf-8")

    if TEMPLATE.exists():
        # "</" can't appear raw inside a <script> block.
        html = TEMPLATE.read_text(encoding="utf-8").replace(
            "__BOARD_DATA__", blob.replace("</", "<\\/"))
        OUTPUT.write_text(html, encoding="utf-8")
        where = f" and {OUTPUT.relative_to(PROJECT_ROOT)}"
    else:
        where = ""

    print(f"Wrote board_data.json{where} — {CURRENT_SEASON} weeks {week_list}, "
          f"{len(teams)} teams, {len(blob) // 1024} KB")


if __name__ == "__main__":
    main()

"""On-ice groups and their seconds together at every strength, for the game simulator.

At even strength and shorthanded, forwards and defence change separately, so each team's skaters
are split into a forward group and a defence group (e.g. a line and a pair). On the power play the
whole unit is kept. Groups that were together for under MIN_S seconds in a game are mostly
line-change overlap and are dropped.

Writes units.csv to the season's output folder: game_id, date, team, state, group (F / D / PP),
player_ids, seconds.
Run from the repo root:  python pipeline/units.py [season]
"""
import csv
import sys
from collections import Counter

from config import RAW_DIR, SEASON, out_dir
from parse import load, strength_state
from pp_toi import boundaries, on_ice_by_second, segments

MIN_S = 10


def game_units(pbp, shifts, stats):
    """Counter keyed by (teamAbbrev, state, group, sorted player tuple) -> seconds."""
    goalies = {r["playerId"] for r in pbp["rosterSpots"] if r["positionCode"] == "G"}
    is_d = {r["playerId"]: r["positionCode"] == "D" for r in pbp["rosterSpots"]}
    teams = ((pbp["homeTeam"], True), (pbp["awayTeam"], False))
    ice = on_ice_by_second(shifts, goalies)
    out = Counter()
    for start, end, code in segments(pbp, boundaries(pbp, shifts, goalies), ice, stats):
        for team, is_home in teams:
            state = strength_state(code, is_home)[0]
            if state not in ("EV", "PP", "SH"):
                continue
            for s in range(start + 1, end + 1):
                on = ice[team["id"]][s]
                if state == "PP":
                    out[(team["abbrev"], "pp", "PP", tuple(sorted(on)))] += 1
                    continue
                st = state.lower()
                out[(team["abbrev"], st, "F", tuple(sorted(p for p in on if not is_d.get(p))))] += 1
                out[(team["abbrev"], st, "D", tuple(sorted(p for p in on if is_d.get(p))))] += 1
    return out


def main(season=SEASON):
    out = out_dir(season)
    with open(out / "games.csv") as f:
        games = list(csv.DictReader(f))

    stats, rows = Counter(), []
    for g in games:
        pbp_path, shift_path = RAW_DIR / f"{g['game_id']}_pbp.json", RAW_DIR / f"{g['game_id']}_shifts.json"
        if not (pbp_path.exists() and shift_path.exists()):
            stats["games_skipped"] += 1
            continue
        for (team, state, group, ids), secs in sorted(game_units(load(pbp_path), load(shift_path), stats).items()):
            if secs < MIN_S or not ids:
                stats["dropped_s"] += secs
                continue
            rows.append({"game_id": g["game_id"], "date": g["date"], "team": team, "state": state,
                         "group": group, "player_ids": ";".join(map(str, ids)), "seconds": secs})
            stats["kept_s"] += secs
        stats["games"] += 1

    with open(out / "units.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["game_id", "date", "team", "state", "group", "player_ids", "seconds"])
        w.writeheader()
        w.writerows(rows)
    kept = stats["kept_s"] / max(stats["kept_s"] + stats["dropped_s"], 1)
    print(f"Games: {stats['games']}  skipped: {stats['games_skipped']}  unit rows: {len(rows)}  "
          f"({kept:.1%} of group-seconds kept)")


if __name__ == "__main__":
    main(*sys.argv[1:])

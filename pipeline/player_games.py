"""One row per skater per game at every strength: the base table for the betting model.

Scoring and on-ice goals for/against are split by the player's team's state when the goal went in
(ev / pp / sh, and "other" for empty-net, extra-attacker and penalty-shot goals; see
parse.strength_state). TOI is split the same way, second by second, using the strength segments
from pp_toi.py. Shootout goals are left out, so team_goals is the score before any shootout.

Also writes one row per team per game (team_games.csv): goals and seconds by state, the
regulation score, and whether the game went to overtime or a shootout.

Writes player_games.csv and team_games.csv to the season's output folder.
Run from the repo root:  python pipeline/player_games.py [season]
"""
import csv
import sys
from collections import Counter

from config import RAW_DIR, SEASON, SHIFT_TYPE_CODE, out_dir
from parse import game_goals, load, skater_shifts, strength_state, time_to_seconds
from pp_toi import boundaries, on_ice_by_second, segments

STATES = ("ev", "pp", "sh", "other")
COUNTS = (
    ["g", "a1", "a2", "pts"]
    + [f"{s}_{k}" for s in STATES for k in ("g", "a")]
    + [f"gf_{s}" for s in STATES] + [f"ga_{s}" for s in STATES]
    + ["toi"] + [f"toi_{s}" for s in STATES]
    + ["sog", "attempts"]
)
FIELDS = ["game_id", "date", "team", "opponent", "home", "player_id", "player", "position",
          "team_goals", "opp_goals"] + COUNTS
TEAM_FIELDS = (["game_id", "date", "team", "opponent", "home", "gf", "ga", "reg_gf", "reg_ga", "ot", "so"]
               + [f"gf_{s}" for s in STATES] + [f"s_{s}" for s in STATES])
SHOTS = {"goal", "shot-on-goal", "missed-shot", "blocked-shot"}


def bucket(state):
    return state.lower() if state in ("EV", "PP", "SH") else "other"


def ids(s):
    return [int(x) for x in s.split(";") if x]


def game_rows(pbp, shifts, stats):
    """({playerId: row} for every skater dressed in the game, [home row, away row] for the teams)."""
    home, away = pbp["homeTeam"], pbp["awayTeam"]
    side = {home["id"]: (home, away, True), away["id"]: (away, home, False)}
    goalies = {r["playerId"] for r in pbp["rosterSpots"] if r["positionCode"] == "G"}
    names = {r["playerId"]: f"{r['firstName']['default']} {r['lastName']['default']}" for r in pbp["rosterSpots"]}
    goals = list(game_goals(pbp, names, skater_shifts(shifts, goalies)))
    score = Counter(g["team_id"] for g in goals)
    reg_score = Counter(g["team_id"] for g in goals if g["period"] <= 3)
    periods = {p["periodDescriptor"]["periodType"] for p in pbp["plays"]}
    teams = {}
    for tid, (team, opp, is_home) in side.items():
        teams[tid] = {
            "game_id": pbp["id"], "date": pbp["gameDate"], "team": team["abbrev"], "opponent": opp["abbrev"],
            "home": int(is_home), "gf": score[tid], "ga": score[opp["id"]], "reg_gf": reg_score[tid],
            "reg_ga": reg_score[opp["id"]], "ot": int("OT" in periods), "so": int("SO" in periods),
            **{f"gf_{s}": 0 for s in STATES}, **{f"s_{s}": 0 for s in STATES},
        }

    rows = {}
    for r in pbp["rosterSpots"]:
        if r["positionCode"] == "G":
            continue
        team, opp, is_home = side[r["teamId"]]
        rows[r["playerId"]] = {
            "game_id": pbp["id"], "date": pbp["gameDate"], "team": team["abbrev"], "opponent": opp["abbrev"],
            "home": int(is_home), "player_id": r["playerId"], "player": names[r["playerId"]],
            "position": r["positionCode"], "team_goals": score[team["id"]], "opp_goals": score[opp["id"]],
            **dict.fromkeys(COUNTS, 0),
        }

    def add(pid, key):
        if pid in rows:
            rows[pid][key] += 1
        else:
            stats[f"unknown_player_{key.split('_')[0]}"] += 1

    for g in goals:
        scoring_is_home = g["team_id"] == home["id"]
        st = bucket(g["state"])
        teams[g["team_id"]][f"gf_{st}"] += 1
        opp_st = bucket(strength_state(g["situation_code"], not scoring_is_home)[0])
        for pid, role in ((g["scorer_id"], "g"), (g["a1_id"], "a1"), (g["a2_id"], "a2")):
            if pid in rows:  # goalie assists aren't skater rows
                rows[pid][role] += 1
                rows[pid][f"{st}_{'g' if role == 'g' else 'a'}"] += 1
        for pid in ids(g["on_ice_ids"]):
            add(pid, f"gf_{st}")
        for pid in ids(g["opp_on_ice_ids"]):
            add(pid, f"ga_{opp_st}")

    for r in shifts["data"]:
        if r["typeCode"] == SHIFT_TYPE_CODE and r["playerId"] in rows:
            rows[r["playerId"]]["toi"] += time_to_seconds(r["endTime"]) - time_to_seconds(r["startTime"])
    ice = on_ice_by_second(shifts, goalies)
    for start, end, code in segments(pbp, boundaries(pbp, shifts, goalies), ice, stats):
        for team_id, (_, _, is_home) in side.items():
            key = "toi_" + bucket(strength_state(code, is_home)[0])
            teams[team_id]["s_" + key[4:]] += end - start
            for s in range(start + 1, end + 1):
                for pid in ice[team_id][s]:
                    add(pid, key)

    for p in pbp["plays"]:
        if p["typeDescKey"] not in SHOTS or p["periodDescriptor"]["periodType"] == "SO":
            continue
        d = p.get("details", {})
        shooter = d.get("scoringPlayerId") if p["typeDescKey"] == "goal" else d.get("shootingPlayerId")
        if shooter in rows:
            rows[shooter]["attempts"] += 1
            rows[shooter]["sog"] += p["typeDescKey"] in ("goal", "shot-on-goal")

    for r in rows.values():
        r["pts"] = r["g"] + r["a1"] + r["a2"]
    return rows, [teams[home["id"]], teams[away["id"]]]


def main(season=SEASON):
    out = out_dir(season)
    with open(out / "games.csv") as f:
        games = list(csv.DictReader(f))

    stats, rows, team_rows = Counter(), [], []
    for g in games:
        pbp_path, shift_path = RAW_DIR / f"{g['game_id']}_pbp.json", RAW_DIR / f"{g['game_id']}_shifts.json"
        if not (pbp_path.exists() and shift_path.exists()):
            stats["games_skipped"] += 1
            continue
        game, game_teams = game_rows(load(pbp_path), load(shift_path), stats)
        rows += sorted(game.values(), key=lambda r: (r["team"], r["player_id"]))
        team_rows += game_teams
        stats["games"] += 1

    with open(out / "player_games.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    with open(out / "team_games.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=TEAM_FIELDS)
        w.writeheader()
        w.writerows(team_rows)

    tot = {k: sum(r[k] for r in rows) for k in COUNTS}
    print(f"Games: {stats['games']}  skipped: {stats['games_skipped']}  player-game rows: {len(rows)}")
    print(f"Goals {tot['g']}  assists {tot['a1'] + tot['a2']}  points {tot['pts']}  "
          f"(ev {tot['ev_g']} / pp {tot['pp_g']} / sh {tot['sh_g']} / other {tot['other_g']} goals)")
    print("Skater TOI by state (min): " + "  ".join(
        f"{s} {tot[f'toi_{s}'] / 60:.0f}" for s in STATES) + f"  total from shifts {tot['toi'] / 60:.0f}")
    unknown = {k: v for k, v in stats.items() if k.startswith("unknown")}
    if unknown:
        print(f"On-ice players missing from the roster: {unknown}")


if __name__ == "__main__":
    main(*sys.argv[1:])

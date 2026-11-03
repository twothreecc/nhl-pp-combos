"""Shots on goal faced by each goalie, and who started each game, for the betting model's goalie ratings.

Every shot on goal or goal with a goalie in net (so empty-net goals are left out) becomes one row of
shots.csv with what the expected-goals model needs: distance and angle to the net, shot type,
whether it was a rebound (the same team had an attempt in the previous REBOUND_S seconds), and the
shooting team's state (EV / PP / SH / EA / PS, see parse.strength_state). Shootout attempts are
left out.

goalie_games.csv has one row per goalie who faced a shot: shots and goals against, and whether they
started (they were in net for their team's first shot against).

Writes shots.csv and goalie_games.csv to the season's output folder.
Run from the repo root:  python pipeline/goalies.py [season]
"""
import csv
import math
import sys
from collections import Counter

from config import RAW_DIR, SEASON, out_dir
from parse import load, strength_state, time_to_seconds

NET_X = 89
REBOUND_S = 3
ATTEMPTS = {"goal", "shot-on-goal", "missed-shot", "blocked-shot"}
SHOT_FIELDS = ["game_id", "date", "team", "opponent", "goalie_id", "state", "shot_type", "distance", "angle",
               "rebound", "goal"]
GOALIE_FIELDS = ["game_id", "date", "team", "opponent", "goalie_id", "started", "shots", "goals"]


def geometry(x, y, attacking_left):
    """Distance (ft) and angle (degrees off the centre line; over 90 is from behind the net)."""
    dx = (x + NET_X) if attacking_left else (NET_X - x)
    return math.hypot(dx, y), math.degrees(math.atan2(abs(y), dx))


def game_shots(pbp):
    home, away = pbp["homeTeam"], pbp["awayTeam"]
    last_attempt = {}
    for p in pbp["plays"]:
        kind = p["typeDescKey"]
        if kind not in ATTEMPTS or p["periodDescriptor"]["periodType"] == "SO":
            continue
        d = p["details"]
        is_home = d.get("eventOwnerTeamId") == home["id"]
        team, opp = (home, away) if is_home else (away, home)
        # a blocked shot's owner is the blocking team
        if kind == "blocked-shot":
            team, opp, is_home = opp, team, not is_home
        period, t = p["periodDescriptor"]["number"], time_to_seconds(p["timeInPeriod"])
        prev = last_attempt.get(team["id"])
        rebound = prev is not None and prev[0] == period and t - prev[1] <= REBOUND_S
        last_attempt[team["id"]] = (period, t)
        code = p.get("situationCode")
        goalie = d.get("goalieInNetId")
        if kind not in ("goal", "shot-on-goal") or not goalie or not code or len(code) != 4 or "xCoord" not in d:
            continue
        state = strength_state(code, is_home)[0]
        if state == "EN":
            continue
        # the defending side is the end the home team defends, so the home team attacks the other end
        home_defends_right = p.get("homeTeamDefendingSide") == "right"
        dist, angle = geometry(d["xCoord"], d.get("yCoord", 0), home_defends_right == is_home)
        yield {"game_id": pbp["id"], "date": pbp["gameDate"], "team": team["abbrev"], "opponent": opp["abbrev"],
               "goalie_id": goalie, "state": state, "shot_type": d.get("shotType", ""),
               "distance": round(dist, 1), "angle": round(angle, 1), "rebound": int(rebound),
               "goal": int(kind == "goal")}


def main(season=SEASON):
    out = out_dir(season)
    with open(out / "games.csv") as f:
        games = list(csv.DictReader(f))

    shots, goalie_rows, stats = [], [], Counter()
    for g in games:
        pbp_path = RAW_DIR / f"{g['game_id']}_pbp.json"
        if not pbp_path.exists():
            stats["games_skipped"] += 1
            continue
        rows = list(game_shots(load(pbp_path)))
        shots += rows
        faced, starter = {}, {}
        for r in rows:
            key = (r["opponent"], r["goalie_id"])
            faced.setdefault(key, {"team": r["opponent"], "opponent": r["team"], "shots": 0, "goals": 0})
            faced[key]["shots"] += 1
            faced[key]["goals"] += r["goal"]
            starter.setdefault(r["opponent"], r["goalie_id"])
        for (team, gid), f in faced.items():
            goalie_rows.append({"game_id": g["game_id"], "date": g["date"], "goalie_id": gid,
                                "started": int(starter[team] == gid), **f})
        stats["games"] += 1

    for name, rows, fields in (("shots.csv", shots, SHOT_FIELDS), ("goalie_games.csv", goalie_rows, GOALIE_FIELDS)):
        with open(out / name, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)
    print(f"Games: {stats['games']}  skipped: {stats['games_skipped']}  shots: {len(shots)}  "
          f"goalie games: {len(goalie_rows)}")


if __name__ == "__main__":
    main(*sys.argv[1:])

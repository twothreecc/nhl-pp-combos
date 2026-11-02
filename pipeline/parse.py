"""Phase 2: extract power-play goals and player info from cached play-by-play.

Phase 3 adds the scoring team's on-ice skaters at goal time, from cached shifts.
Writes data/pp_goals.csv, data/players.json and data/qa_mismatches.csv.
Run from the repo root:  python pipeline/parse.py
"""
import csv
import json

from config import DATA_DIR, INCLUDE_EXTRA_ATTACKER, RAW_DIR, SHIFT_TYPE_CODE

GOAL_FIELDS = [
    "game_id", "date", "team", "team_id", "opponent", "period", "period_type", "time",
    "strength", "situation_code", "scorer_id", "a1_id", "a2_id", "scorer", "a1", "a2",
    "on_ice_ids",
]


def load(path):
    with open(path) as f:
        return json.load(f)


def parse_situation(code):
    """'1541' -> (away_goalie, away_skaters, home_skaters, home_goalie)."""
    return tuple(int(c) for c in code)


def pp_strength(code, scoring_is_home):
    """Return e.g. '5v4' if the goal was scored on the power play, else None."""
    away_g, away_sk, home_sk, home_g = parse_situation(code)
    own_g, own_sk, opp_sk, opp_g = (
        (home_g, home_sk, away_sk, away_g) if scoring_is_home else (away_g, away_sk, home_sk, home_g)
    )
    if opp_g != 1:  # empty-net goal against
        return None
    if own_g != 1 and not INCLUDE_EXTRA_ATTACKER:
        return None
    if own_sk <= opp_sk:
        return None
    return f"{own_sk}v{opp_sk}"


def time_to_seconds(t):
    m, s = t.split(":")
    return int(m) * 60 + int(s)


def skater_shifts(shifts, goalie_ids):
    """{(teamId, period): [(playerId, start_s, end_s), ...]} for real skater shifts."""
    out = {}
    for r in shifts["data"]:
        if r["typeCode"] != SHIFT_TYPE_CODE or r["playerId"] in goalie_ids:
            continue
        out.setdefault((r["teamId"], r["period"]), []).append(
            (r["playerId"], time_to_seconds(r["startTime"]), time_to_seconds(r["endTime"])))
    return out


def on_ice(shifts_by_team_period, team_id, period, t):
    """Skaters on the ice at second t: left on the goal counts, jumped on at t doesn't."""
    return sorted({pid for pid, start, end in shifts_by_team_period.get((team_id, period), []) if start < t <= end})


def game_pp_goals(pbp, names):
    """Yield one dict per PP goal in a game."""
    home, away = pbp["homeTeam"], pbp["awayTeam"]
    for p in pbp["plays"]:
        if p["typeDescKey"] != "goal" or p["periodDescriptor"]["periodType"] == "SO":
            continue
        d = p["details"]
        code = p.get("situationCode")
        if not code or len(code) != 4:
            continue
        scoring_is_home = d["eventOwnerTeamId"] == home["id"]
        strength = pp_strength(code, scoring_is_home)
        if strength is None:
            continue
        team, opp = (home, away) if scoring_is_home else (away, home)
        ids = [d.get("scoringPlayerId"), d.get("assist1PlayerId"), d.get("assist2PlayerId")]
        yield {
            "game_id": pbp["id"],
            "date": pbp["gameDate"],
            "team": team["abbrev"],
            "team_id": team["id"],
            "opponent": opp["abbrev"],
            "period": p["periodDescriptor"]["number"],
            "period_type": p["periodDescriptor"]["periodType"],
            "time": p["timeInPeriod"],
            "strength": strength,
            "situation_code": code,
            "scorer_id": ids[0],
            "a1_id": ids[1] or "",
            "a2_id": ids[2] or "",
            "scorer": names.get(ids[0], ""),
            "a1": names.get(ids[1], ""),
            "a2": names.get(ids[2], ""),
            "on_ice_ids": "",  # filled in phase 3
        }


def main():
    with open(DATA_DIR / "games.csv") as f:
        games = list(csv.DictReader(f))

    players, goals, missing, missing_shifts, mismatches = {}, [], [], [], []
    for g in games:
        path = RAW_DIR / f"{g['game_id']}_pbp.json"
        if not path.exists():
            missing.append(g["game_id"])
            continue
        pbp = load(path)
        abbrev = {pbp["homeTeam"]["id"]: pbp["homeTeam"]["abbrev"], pbp["awayTeam"]["id"]: pbp["awayTeam"]["abbrev"]}
        names = {}
        for r in pbp["rosterSpots"]:
            pid = r["playerId"]
            name = f"{r['firstName']['default']} {r['lastName']['default']}"
            names[pid] = name
            p = players.setdefault(pid, {"name": name, "position": r["positionCode"], "teams": [], "headshot": ""})
            team = abbrev[r["teamId"]]
            if team not in p["teams"]:
                p["teams"].append(team)
            # games are processed in date order, so the last headshot is the current team's
            p["headshot"] = r.get("headshot", p["headshot"])
        game_goals = list(game_pp_goals(pbp, names))
        shift_path = RAW_DIR / f"{g['game_id']}_shifts.json"
        if game_goals and shift_path.exists():
            goalies = {r["playerId"] for r in pbp["rosterSpots"] if r["positionCode"] == "G"}
            by_tp = skater_shifts(load(shift_path), goalies)
            for r in game_goals:
                ids = on_ice(by_tp, r["team_id"], r["period"], time_to_seconds(r["time"]))
                r["on_ice_ids"] = ";".join(map(str, ids))
                expected = int(r["strength"].split("v")[0])
                sheet = {x for x in (r["scorer_id"], r["a1_id"], r["a2_id"]) if x}
                # goalie assists are expected off-ice; only flag skaters
                off_ice = sheet - set(ids) - goalies
                if len(ids) != expected or off_ice:
                    mismatches.append({
                        "game_id": r["game_id"], "team": r["team"], "period": r["period"], "time": r["time"],
                        "strength": r["strength"], "expected": expected, "found": len(ids),
                        "on_ice": "; ".join(names.get(i, str(i)) for i in ids),
                        "scoresheet_not_on_ice": "; ".join(names.get(i, str(i)) for i in off_ice),
                        "shift_source": load(shift_path).get("source", "api"),
                    })
        elif game_goals:
            missing_shifts.append(g["game_id"])
        goals.extend(game_goals)

    goals.sort(key=lambda r: (r["date"], r["game_id"], r["period"], time_to_seconds(r["time"])))
    with open(DATA_DIR / "pp_goals.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=GOAL_FIELDS)
        w.writeheader()
        w.writerows(goals)
    with open(DATA_DIR / "players.json", "w") as f:
        json.dump({str(k): v for k, v in sorted(players.items())}, f, indent=1)

    with open(DATA_DIR / "qa_mismatches.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "game_id", "team", "period", "time", "strength", "expected", "found",
            "on_ice", "scoresheet_not_on_ice", "shift_source"])
        w.writeheader()
        w.writerows(mismatches)

    by_strength = {}
    for r in goals:
        by_strength[r["strength"]] = by_strength.get(r["strength"], 0) + 1
    print(f"Games parsed: {len(games) - len(missing)}  PP goals: {len(goals)}  by strength: {by_strength}")
    print(f"Players: {len(players)}")
    print(f"On-ice QA mismatches: {len(mismatches)} of {len(goals)} (see data/qa_mismatches.csv)")
    if missing_shifts:
        print(f"Games with PP goals but no cached shifts: {missing_shifts}")
    if missing:
        print(f"Games missing play-by-play: {missing}")


if __name__ == "__main__":
    main()

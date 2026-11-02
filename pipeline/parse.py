"""Extract goals at every strength, with both teams' on-ice skaters, plus player info.

pp_goals.csv keeps the power-play subset the PP dashboard is built on.
Writes goals.csv, pp_goals.csv, players.json and qa_mismatches.csv to the season's output folder.
Run from the repo root:  python pipeline/parse.py [season]
"""
import csv
import json
import sys

from config import INCLUDE_EXTRA_ATTACKER, RAW_DIR, SEASON, SHIFT_TYPE_CODE, out_dir

PP_GOAL_FIELDS = [
    "game_id", "date", "team", "team_id", "opponent", "period", "period_type", "time",
    "strength", "situation_code", "scorer_id", "a1_id", "a2_id", "scorer", "a1", "a2",
    "on_ice_ids",
]
GOAL_FIELDS = PP_GOAL_FIELDS[:8] + ["state", "pp"] + PP_GOAL_FIELDS[8:] + ["opp_on_ice_ids"]


def load(path):
    with open(path) as f:
        return json.load(f)


def parse_situation(code):
    """'1541' -> (away_goalie, away_skaters, home_skaters, home_goalie)."""
    return tuple(int(c) for c in code)


def team_view(code, is_home):
    """(own_goalie, own_skaters, opp_skaters, opp_goalie) from one team's side."""
    away_g, away_sk, home_sk, home_g = parse_situation(code)
    return (home_g, home_sk, away_sk, away_g) if is_home else (away_g, away_sk, home_sk, home_g)


def pp_strength(code, scoring_is_home):
    """Return e.g. '5v4' if the goal was scored on the power play, else None."""
    own_g, own_sk, opp_sk, opp_g = team_view(code, scoring_is_home)
    if opp_g != 1:  # empty-net goal against
        return None
    if own_g != 1 and not INCLUDE_EXTRA_ATTACKER:
        return None
    if own_sk <= opp_sk:
        return None
    return f"{own_sk}v{opp_sk}"


def strength_state(code, is_home):
    """One team's state and skaters, e.g. ('PP', '5v4').

    EV / PP / SH need both goalies in net; EN means the opponent's net is empty, EA means this
    team's goalie is pulled, PS is a penalty shot (one shooter against a goalie).
    """
    own_g, own_sk, opp_sk, opp_g = team_view(code, is_home)
    label = f"{own_sk}v{opp_sk}"
    if own_sk + opp_sk == 1:
        return "PS", label
    if opp_g != 1:
        return "EN", label
    if own_g != 1:
        return "EA", label
    return ("PP" if own_sk > opp_sk else "SH" if own_sk < opp_sk else "EV"), label


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


def game_goals(pbp, names, by_tp=None):
    """Yield one dict per non-shootout goal; on-ice sets are filled when shifts (by_tp) are given."""
    home, away = pbp["homeTeam"], pbp["awayTeam"]
    for p in pbp["plays"]:
        if p["typeDescKey"] != "goal" or p["periodDescriptor"]["periodType"] == "SO":
            continue
        d = p["details"]
        code = p.get("situationCode")
        if not code or len(code) != 4:
            continue
        scoring_is_home = d["eventOwnerTeamId"] == home["id"]
        state, strength = strength_state(code, scoring_is_home)
        team, opp = (home, away) if scoring_is_home else (away, home)
        period, t = p["periodDescriptor"]["number"], time_to_seconds(p["timeInPeriod"])
        ids = [d.get("scoringPlayerId"), d.get("assist1PlayerId"), d.get("assist2PlayerId")]
        on_team = on_ice(by_tp, team["id"], period, t) if by_tp is not None else []
        on_opp = on_ice(by_tp, opp["id"], period, t) if by_tp is not None else []
        yield {
            "game_id": pbp["id"],
            "date": pbp["gameDate"],
            "team": team["abbrev"],
            "team_id": team["id"],
            "opponent": opp["abbrev"],
            "period": period,
            "period_type": p["periodDescriptor"]["periodType"],
            "time": p["timeInPeriod"],
            "state": state,
            "pp": int(pp_strength(code, scoring_is_home) is not None),
            "strength": strength,
            "situation_code": code,
            "scorer_id": ids[0],
            "a1_id": ids[1] or "",
            "a2_id": ids[2] or "",
            "scorer": names.get(ids[0], ""),
            "a1": names.get(ids[1], ""),
            "a2": names.get(ids[2], ""),
            "on_ice_ids": ";".join(map(str, on_team)),
            "opp_on_ice_ids": ";".join(map(str, on_opp)),
        }


def write_csv(path, rows, fields):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main(season=SEASON):
    out = out_dir(season)
    with open(out / "games.csv") as f:
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
        shift_path = RAW_DIR / f"{g['game_id']}_shifts.json"
        goalies = {r["playerId"] for r in pbp["rosterSpots"] if r["positionCode"] == "G"}
        shifts = load(shift_path) if shift_path.exists() else None
        game_goals_ = list(game_goals(pbp, names, skater_shifts(shifts, goalies) if shifts else None))
        if shifts:
            for r in game_goals_:
                if r["state"] == "PS":  # the shift chart shows the whole unit, not the shooter alone
                    continue
                ids = [int(x) for x in r["on_ice_ids"].split(";") if x]
                expected = int(r["strength"].split("v")[0])
                sheet = {x for x in (r["scorer_id"], r["a1_id"], r["a2_id"]) if x}
                # goalie assists are expected off-ice; only flag skaters
                off_ice = sheet - set(ids) - goalies
                if len(ids) != expected or off_ice:
                    mismatches.append({
                        "game_id": r["game_id"], "team": r["team"], "period": r["period"], "time": r["time"],
                        "state": r["state"], "strength": r["strength"], "expected": expected, "found": len(ids),
                        "on_ice": "; ".join(names.get(i, str(i)) for i in ids),
                        "scoresheet_not_on_ice": "; ".join(names.get(i, str(i)) for i in off_ice),
                        "shift_source": shifts.get("source", "api"),
                    })
        elif game_goals_:
            missing_shifts.append(g["game_id"])
        goals.extend(game_goals_)

    goals.sort(key=lambda r: (r["date"], r["game_id"], r["period"], time_to_seconds(r["time"])))
    pp_goals = [r for r in goals if r["pp"]]
    write_csv(out / "goals.csv", goals, GOAL_FIELDS)
    write_csv(out / "pp_goals.csv", pp_goals, PP_GOAL_FIELDS)
    with open(out / "players.json", "w") as f:
        json.dump({str(k): v for k, v in sorted(players.items())}, f, indent=1)
    write_csv(out / "qa_mismatches.csv", mismatches, [
        "game_id", "team", "period", "time", "state", "strength", "expected", "found",
        "on_ice", "scoresheet_not_on_ice", "shift_source"])

    by_state, by_strength = {}, {}
    for r in goals:
        by_state[r["state"]] = by_state.get(r["state"], 0) + 1
    for r in pp_goals:
        by_strength[r["strength"]] = by_strength.get(r["strength"], 0) + 1
    pp_mismatches = sum(m["state"] == "PP" for m in mismatches)
    print(f"Games parsed: {len(games) - len(missing)}  goals: {len(goals)}  by state: {by_state}")
    print(f"PP goals: {len(pp_goals)}  by strength: {by_strength}")
    print(f"Players: {len(players)}")
    print(f"On-ice QA mismatches: {len(mismatches)} of {len(goals)} goals, {pp_mismatches} on the PP "
          f"(see qa_mismatches.csv)")
    if missing_shifts:
        print(f"Games with goals but no cached shifts: {missing_shifts}")
    if missing:
        print(f"Games missing play-by-play: {missing}")


if __name__ == "__main__":
    main(*sys.argv[1:])

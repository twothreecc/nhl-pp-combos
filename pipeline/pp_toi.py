"""Phase 4: power-play intervals per game and PP seconds for every on-ice skater set.

PP state comes from the situationCode on each play. A penalty event carries the code from
before the call and the faceoff after it carries the new state, so PP starts land exactly on
event timestamps. A PP that ends on penalty expiry ends between events; there the transition
is placed at the penalty's expiry time (or a goalie pull/return from the shift chart). If no such
boundary lies in the gap (e.g. stacked penalties), it is placed where both teams' on-ice skater
counts first match the new state, falling back to the next event's timestamp.

Time runs on an absolute game clock (seconds since opening faceoff) so PPs that carry over an
intermission stay continuous. Second s covers the interval (s-1, s]; a skater is on the ice for
it when start < s <= end, matching the on-ice-at-goal rule.

Writes pp_toi_units.csv to the season's output folder: one row per (game, team, strength,
skater set) with PP seconds.
Run from the repo root:  python pipeline/pp_toi.py [season]
"""
import csv
import json
import sys
from collections import Counter, defaultdict

from config import PERIOD_SECONDS, RAW_DIR, SEASON, SHIFT_TYPE_CODE, out_dir
from parse import pp_strength, time_to_seconds

PP_PENALTY_TYPES = {"MIN", "BEN", "MAJ"}


def load(path):
    with open(path) as f:
        return json.load(f)


def abs_time(period, mmss):
    return (period - 1) * PERIOD_SECONDS + time_to_seconds(mmss)


def boundaries(pbp, shifts, goalie_ids):
    """Times where the strength state may change between events: penalty expiries, goalie pulls/returns."""
    out = set()
    for p in pbp["plays"]:
        d = p.get("details", {})
        if p["typeDescKey"] == "penalty" and d.get("typeCode") in PP_PENALTY_TYPES and d.get("duration"):
            out.add(abs_time(p["periodDescriptor"]["number"], p["timeInPeriod"]) + d["duration"] * 60)
    for r in shifts["data"]:
        if r["typeCode"] == SHIFT_TYPE_CODE and r["playerId"] in goalie_ids:
            out.add(abs_time(r["period"], r["startTime"]))
            out.add(abs_time(r["period"], r["endTime"]))
    return sorted(out)


def shift_transition(ice, pbp, t0, t1, new_code, hold=3):
    """First second in (t0, t1) after which both teams' skater counts match new_code for `hold` seconds."""
    _, away_sk, home_sk, _ = (int(c) for c in new_code)
    home, away = pbp["homeTeam"]["id"], pbp["awayTeam"]["id"]
    run = 0
    for s in range(t0 + 1, t1 + 1):
        if len(ice[home][s]) == home_sk and len(ice[away][s]) == away_sk:
            run += 1
            if run >= hold or s == t1:
                return s - run
        else:
            run = 0
    return None


def segments(pbp, cuts, ice, stats):
    """Yield (start, end, situationCode): the state holds over seconds start+1..end."""
    plays = [p for p in pbp["plays"] if p.get("situationCode") and len(p["situationCode"]) == 4
             and p["periodDescriptor"]["periodType"] != "SO"]
    events = [(abs_time(p["periodDescriptor"]["number"], p["timeInPeriod"]), p["situationCode"]) for p in plays]
    for (t0, c0), (t1, c1) in zip(events, events[1:]):
        if t1 <= t0:
            continue
        if c0 == c1:
            yield t0, t1, c0
            continue
        inside = [c for c in cuts if t0 < c < t1]
        cut = inside[0] if inside else shift_transition(ice, pbp, t0, t1, c1)
        if inside:
            stats["boundary"] += 1
        elif cut is not None:
            stats["shift_located"] += 1
        if cut is not None:
            yield t0, cut, c0
            yield cut, t1, c1
        else:
            # no known boundary: hold the old state until the next event
            stats["event_fallback"] += 1
            yield t0, t1, c0


def on_ice_by_second(shifts, goalie_ids):
    """{teamId: {second: set(playerIds)}} for skaters."""
    out = defaultdict(lambda: defaultdict(set))
    for r in shifts["data"]:
        if r["typeCode"] != SHIFT_TYPE_CODE or r["playerId"] in goalie_ids:
            continue
        start, end = abs_time(r["period"], r["startTime"]), abs_time(r["period"], r["endTime"])
        for s in range(start + 1, end + 1):
            out[r["teamId"]][s].add(r["playerId"])
    return out


def game_unit_seconds(pbp, shifts, stats):
    """Counter keyed by (teamAbbrev, strength, sorted skater tuple) -> PP seconds."""
    goalies = {r["playerId"] for r in pbp["rosterSpots"] if r["positionCode"] == "G"}
    home, away = pbp["homeTeam"], pbp["awayTeam"]
    ice = on_ice_by_second(shifts, goalies)
    units = Counter()
    for start, end, code in segments(pbp, boundaries(pbp, shifts, goalies), ice, stats):
        for team, is_home in ((home, True), (away, False)):
            strength = pp_strength(code, is_home)
            if strength is None:
                continue
            for s in range(start + 1, end + 1):
                units[(team["abbrev"], strength, tuple(sorted(ice[team["id"]][s])))] += 1
    return units


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
        for (team, strength, ids), secs in game_unit_seconds(load(pbp_path), load(shift_path), stats).items():
            rows.append({"game_id": g["game_id"], "date": g["date"], "team": team, "strength": strength,
                         "player_ids": ";".join(map(str, ids)), "seconds": secs})
        stats["games"] += 1

    with open(out / "pp_toi_units.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["game_id", "date", "team", "strength", "player_ids", "seconds"])
        w.writeheader()
        w.writerows(rows)

    total = sum(r["seconds"] for r in rows)
    print(f"Games: {stats['games']}  skipped: {stats['games_skipped']}  unit rows: {len(rows)}")
    print(f"Total team PP time: {total / 60:.0f} min  ({total / 60 / max(stats['games'], 1):.1f} min per game, both teams)")
    print(f"State changes between events: placed at a boundary {stats['boundary']}, "
          f"located from shifts {stats['shift_located']}, held to next event {stats['event_fallback']}")


if __name__ == "__main__":
    main(*sys.argv[1:])

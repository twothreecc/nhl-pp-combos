"""Phase 1: collect finished regular-season games and cache their play-by-play and shifts.

Raw responses go to data/raw/{gameId}_{pbp|shifts}.json. A finished game is never re-fetched.
The game list goes to games.csv in the season's output folder (see config.out_dir).
Run from the repo root:  python pipeline/fetch.py [season]
"""
import csv
import json
import sys
from collections import Counter

import nhl_api
from config import FINISHED_STATES, RAW_DIR, REGULAR_SEASON, SEASON, SHIFT_TYPE_CODE, out_dir, teams
from html_shifts import shifts_from_html


def load(path):
    with open(path) as f:
        return json.load(f)


def save(path, obj):
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(obj, f)
    tmp.replace(path)


def collect_games(season):
    """Return {gameId: row} for finished regular-season games, deduped across all teams."""
    games, states = {}, Counter()
    for abbrev in teams(season):
        sched = nhl_api.team_schedule(abbrev, season)
        for g in sched["games"]:
            if g["gameType"] != REGULAR_SEASON:
                continue
            states[g["gameState"]] += 1
            if g["gameState"] in FINISHED_STATES:
                games[g["id"]] = {
                    "game_id": g["id"],
                    "date": g["gameDate"],
                    "away": g["awayTeam"]["abbrev"],
                    "home": g["homeTeam"]["abbrev"],
                    "state": g["gameState"],
                }
    # each game shows up on two teams' schedules
    print(f"Schedule gameState counts (per-team rows): {dict(states)}")
    return games


def cache_pbp(game_id):
    path = RAW_DIR / f"{game_id}_pbp.json"
    if path.exists() and load(path).get("gameState") in FINISHED_STATES:
        return "cached"
    pbp = nhl_api.play_by_play(game_id)
    if pbp.get("gameState") not in FINISHED_STATES:
        return "not_final"
    save(path, pbp)
    return "fetched"


def cache_shifts(game_id):
    path = RAW_DIR / f"{game_id}_shifts.json"
    if path.exists():
        return "cached"
    shifts = nhl_api.shift_chart(game_id)
    if any(r["typeCode"] == SHIFT_TYPE_CODE for r in shifts.get("data", [])):
        save(path, shifts)
        return "fetched"
    # the API has gaps; fall back to the HTML TOI reports
    pbp_path = RAW_DIR / f"{game_id}_pbp.json"
    if not pbp_path.exists():
        return "empty"
    try:
        shifts = shifts_from_html(game_id, load(pbp_path))
    except Exception as e:  # report not posted yet, or unparseable; retried next run
        print(f"  {game_id}: HTML shift fallback failed: {e}")
        return "empty"
    if not shifts["data"]:
        return "empty"
    save(path, shifts)
    return "fetched_html"


def main(season=SEASON):
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    out = out_dir(season)
    out.mkdir(parents=True, exist_ok=True)
    games = collect_games(season)
    ids = sorted(games)
    print(f"Finished regular-season games: {len(ids)}")

    pbp_status, shift_status = Counter(), Counter()
    for i, gid in enumerate(ids, 1):
        pbp_status[cache_pbp(gid)] += 1
        shift_status[cache_shifts(gid)] += 1
        if i % 25 == 0 or i == len(ids):
            print(f"  {i}/{len(ids)}  pbp={dict(pbp_status)}  shifts={dict(shift_status)}")

    with open(out / "games.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["game_id", "date", "away", "home", "state"])
        w.writeheader()
        w.writerows(games[g] for g in ids)

    summarize(ids)


def summarize(ids):
    """Print counts plus the open checks from the spec, over everything cached."""
    goals = 0
    sit_codes, period_types, shift_codes = Counter(), Counter(), Counter()
    two_assist = 0
    missing_shifts = []
    for gid in ids:
        pbp_path = RAW_DIR / f"{gid}_pbp.json"
        if pbp_path.exists():
            for p in load(pbp_path)["plays"]:
                if p["typeDescKey"] != "goal":
                    continue
                goals += 1
                sit_codes[p.get("situationCode")] += 1
                period_types[p["periodDescriptor"]["periodType"]] += 1
                if "assist2PlayerId" in p["details"]:
                    two_assist += 1
        shift_path = RAW_DIR / f"{gid}_shifts.json"
        if shift_path.exists():
            for r in load(shift_path)["data"]:
                shift_codes[(r["typeCode"], r.get("eventDescription"))] += 1
        else:
            missing_shifts.append(gid)

    print("\n=== Summary ===")
    print(f"Games: {len(ids)}   goals: {goals}   goals with assist2PlayerId: {two_assist}")
    print(f"Goal periodTypes: {dict(period_types)}")
    print(f"Goal situationCodes: {dict(sit_codes.most_common())}")
    print(f"Shift (typeCode, eventDescription): {dict(shift_codes.most_common())}")
    if missing_shifts:
        print(f"Games without cached shifts ({len(missing_shifts)}): {missing_shifts}")


if __name__ == "__main__":
    main(*sys.argv[1:])

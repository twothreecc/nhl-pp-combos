"""Cross-check data/pp_goals.csv against the NHL gamecenter scoring summary (landing endpoint).

The landing summary labels every goal with a strength ('pp', 'ev', 'sh') and lists its
scorer and assists, so it's an independent check on both the PP rule and the scoresheet.
Landing responses are cached to data/raw/{gameId}_landing.json.
Run from the repo root:  python pipeline/check_scoresheet.py
"""
import csv
import json

import nhl_api
from config import DATA_DIR, RAW_DIR


def landing(game_id):
    path = RAW_DIR / f"{game_id}_landing.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    data = nhl_api.get_json(f"{nhl_api.WEB}/gamecenter/{game_id}/landing")
    with open(path, "w") as f:
        json.dump(data, f)
    return data


def key(game_id, period, time):
    return (int(game_id), int(period), time)


def main():
    with open(DATA_DIR / "pp_goals.csv") as f:
        ours = {key(r["game_id"], r["period"], r["time"]): r for r in csv.DictReader(f)}
    with open(DATA_DIR / "games.csv") as f:
        game_ids = [int(r["game_id"]) for r in csv.DictReader(f)]

    theirs = {}
    for gid in game_ids:
        for period in landing(gid)["summary"]["scoring"]:
            if period["periodDescriptor"]["periodType"] == "SO":
                continue
            for g in period["goals"]:
                if g["strength"] == "pp":
                    assists = [a["playerId"] for a in g.get("assists", [])]
                    theirs[key(gid, period["periodDescriptor"]["number"], g["timeInPeriod"])] = (
                        g["playerId"], assists, g.get("situationCode"))

    only_ours = sorted(set(ours) - set(theirs))
    only_theirs = sorted(set(theirs) - set(ours))
    sheet_diff = []
    for k in set(ours) & set(theirs):
        r = ours[k]
        mine = (int(r["scorer_id"]), [int(x) for x in (r["a1_id"], r["a2_id"]) if x])
        if mine != theirs[k][:2]:
            sheet_diff.append((k, mine, theirs[k][:2]))

    print(f"Our PP goals: {len(ours)}  NHL 'pp' goals: {len(theirs)}  matched: {len(set(ours) & set(theirs))}")
    print(f"Only ours ({len(only_ours)}):")
    for k in only_ours:
        print(f"  {k} {ours[k]['team']} {ours[k]['situation_code']} {ours[k]['strength']} {ours[k]['scorer']}")
    print(f"Only NHL ({len(only_theirs)}):")
    for k in only_theirs:
        print(f"  {k} situationCode={theirs[k][2]}")
    print(f"Scoresheet mismatches on matched goals: {len(sheet_diff)}")
    for d in sheet_diff:
        print(f"  {d}")


if __name__ == "__main__":
    main()

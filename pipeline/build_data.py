"""Phase 5: bundle the per-goal and per-game PP TOI rows into site/data.json for the dashboard.

The dashboard aggregates client-side so its team / strength / date / position filters apply to
both goals and TOI. Rows are stored as arrays to keep the file small; see the `fields` keys.
Run from the repo root:  python pipeline/build_data.py
"""
import csv
import json
from datetime import datetime, timezone

from config import DATA_DIR, ROOT, SEASON


def ids(s):
    return [int(x) for x in s.split(";")] if s else []


def main():
    with open(DATA_DIR / "games.csv") as f:
        games = list(csv.DictReader(f))
    with open(DATA_DIR / "pp_goals.csv") as f:
        goals = list(csv.DictReader(f))
    with open(DATA_DIR / "pp_toi_units.csv") as f:
        toi = list(csv.DictReader(f))
    with open(DATA_DIR / "players.json") as f:
        players = json.load(f)

    used = set()
    goal_rows = []
    for g in goals:
        on_ice = ids(g["on_ice_ids"])
        sheet = [int(x) if x else None for x in (g["scorer_id"], g["a1_id"], g["a2_id"])]
        used.update(on_ice, (x for x in sheet if x))
        goal_rows.append([int(g["game_id"]), g["date"], g["team"], g["opponent"], int(g["period"]),
                          g["time"], g["strength"], *sheet, on_ice])
    toi_rows = []
    for r in toi:
        on_ice = ids(r["player_ids"])
        used.update(on_ice)
        toi_rows.append([r["date"], r["team"], r["strength"], on_ice, int(r["seconds"])])

    out = {
        "meta": {
            "season": SEASON,
            "games": len(games),
            "through": max(g["date"] for g in games) if games else None,
            "generated": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        },
        "fields": {
            "players": ["name", "position", "headshot"],
            "goals": ["gameId", "date", "team", "opponent", "period", "time", "strength",
                      "scorer", "a1", "a2", "onIce"],
            "toi": ["date", "team", "strength", "onIce", "seconds"],
        },
        "players": {pid: [p["name"], p["position"], p["headshot"]]
                    for pid, p in players.items() if int(pid) in used},
        "goals": goal_rows,
        "toi": toi_rows,
    }
    site = ROOT / "site"
    site.mkdir(exist_ok=True)
    with open(site / "data.json", "w") as f:
        json.dump(out, f, separators=(",", ":"))
    print(f"site/data.json: {len(goal_rows)} goals, {len(toi_rows)} TOI rows, {len(out['players'])} players, "
          f"{(site / 'data.json').stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    main()

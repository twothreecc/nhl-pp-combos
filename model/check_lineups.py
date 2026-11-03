"""Compare saved lineup projections (data/lineups/{date}/{TEAM}.json) with who actually played.

For every saved projection whose game is in the data: skaters projected but scratched, skaters who
played but weren't projected, and whether the starting goalie was right, by goalie status
(Confirmed / Likely / ...). Writes data/lineups/check.csv, one row per team-game.

    python model/check_lineups.py
"""
import json

import pandas as pd

from data import Db
from lineups import SNAPSHOTS


def main():
    db = Db()
    pg, gg = db.player_games, db.goalie_games
    rows = []
    for path in sorted(SNAPSHOTS.glob("*/*.json")):
        snap = json.loads(path.read_text())
        day, team = pd.Timestamp(path.parent.name), snap["team"]
        played = pg[(pg.team == team) & (pg.date == day)]
        if played.empty:
            continue
        actual, projected = set(played.player_id), {pid for pid, _ in snap["skaters"]}
        starter = gg[(gg.team == team) & (gg.date == day) & (gg.started == 1)].goalie_id
        rows.append({"date": day.date(), "team": team, "source": snap["source"], "fetched": snap.get("fetched", ""),
                     "goalie_status": snap["goalie_status"], "goalie_right": int(len(starter) > 0 and starter.iloc[0] == snap["goalie"]),
                     "scratched": len(projected - actual), "missed": len(actual - projected),
                     "scratched_names": ", ".join(db.name(p) for p in projected - actual),
                     "missed_names": ", ".join(db.name(p) for p in actual - projected)})
    if not rows:
        print("No saved projections for games that have been played yet.")
        return
    df = pd.DataFrame(rows)
    df.to_csv(SNAPSHOTS / "check.csv", index=False)
    print(f"{len(df)} team-games checked")
    print(f"  skaters right: {1 - df.missed.sum() / (18 * len(df)):.1%}   "
          f"team-games with every skater right: {(df.missed == 0).mean():.1%}")
    print("  starting goalie right, by status:")
    print(df.groupby("goalie_status").goalie_right.agg(games="size", right="mean").round(3).to_string())
    misses = df[df.missed > 0]
    if len(misses):
        print("\nMisses:")
        print(misses[["date", "team", "scratched_names", "missed_names"]].to_string(index=False))


if __name__ == "__main__":
    main()

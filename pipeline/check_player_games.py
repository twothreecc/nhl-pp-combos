"""Cross-check a season's player_games.csv against the NHL stats API's season totals per skater.

Expected gaps: the NHL counts PP goals by penalty situation, so a 6v4 goal with the goalie pulled
is PP for them and "other" for us; and goals a goalie assists on lose that assist here.
Run from the repo root:  python pipeline/check_player_games.py [season]
"""
import sys

import pandas as pd

import nhl_api
from config import REGULAR_SEASON, SEASON, out_dir

PAIRS = [("goals", "g"), ("assists", "a"), ("points", "pts"), ("ppGoals", "pp_g"), ("ppPoints", "pp_pts"),
         ("shots", "sog"), ("gamesPlayed", "gp"), ("nhl_toi_min", "toi_min")]


def main(season=SEASON):
    nhl = pd.DataFrame(nhl_api.get_json(
        f"{nhl_api.STATS}/skater/summary?limit=-1"
        f"&cayenneExp=seasonId={season}%20and%20gameTypeId={REGULAR_SEASON}")["data"])
    nhl["nhl_toi_min"] = nhl.timeOnIcePerGame * nhl.gamesPlayed / 60
    nhl = nhl.set_index("playerId")

    pg = pd.read_csv(out_dir(season) / "player_games.csv")
    pg["a"] = pg.a1 + pg.a2
    pg["pp_pts"] = pg.pp_g + pg.pp_a
    pg["gp"] = (pg.toi > 0).astype(int)
    pg["toi_min"] = pg.toi / 60
    ours = pg.groupby("player_id")[[o for _, o in PAIRS]].sum()

    df = nhl[[n for n, _ in PAIRS] + ["skaterFullName"]].join(ours, how="outer").fillna(0)
    print(f"Season {season}: {len(nhl)} NHL skaters, {len(ours)} of ours")
    print(f"{'stat':<12}{'NHL':>9}{'ours':>9}{'exact':>8}")
    for n, o in PAIRS:
        tol = 1 if n == "nhl_toi_min" else 0
        exact = ((df[n] - df[o]).abs() <= tol).mean()
        print(f"{n:<12}{df[n].sum():>9.0f}{df[o].sum():>9.0f}{exact:>8.1%}")
    df["pts_diff"] = df.pts - df.points
    worst = df[df.pts_diff != 0].sort_values("points", ascending=False).head(10)
    if len(worst):
        print("\nPoint differences (top scorers first):")
        print(worst[["skaterFullName", "points", "pts", "ppPoints", "pp_pts"]].to_string())


if __name__ == "__main__":
    main(*sys.argv[1:])

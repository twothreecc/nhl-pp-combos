"""Cross-check data/player_pp_toi.csv against the NHL stats API's season ppTimeOnIce per skater.

The NHL counts any manpower advantage from a penalty as PP time, including when the PP team pulls
its goalie (6v4), which our goalie rule excludes, so expect ours to run slightly lower.
Run from the repo root:  python pipeline/check_toi.py
"""
import pandas as pd

import nhl_api
from config import DATA_DIR, REGULAR_SEASON, SEASON


def main():
    rows = nhl_api.get_json(
        f"{nhl_api.STATS}/skater/timeonice?limit=-1"
        f"&cayenneExp=seasonId={SEASON}%20and%20gameTypeId={REGULAR_SEASON}")["data"]
    nhl = pd.DataFrame(rows)[["playerId", "skaterFullName", "teamAbbrevs", "ppTimeOnIce"]]
    nhl["nhl_min"] = nhl.ppTimeOnIce / 60

    ours = pd.read_csv(DATA_DIR / "player_pp_toi.csv").groupby("player_id").toi_min.sum().rename("our_min")
    df = nhl.merge(ours, left_on="playerId", right_index=True, how="outer").fillna({"nhl_min": 0, "our_min": 0})
    df = df[(df.nhl_min > 0) | (df.our_min > 0)].copy()
    df["diff_min"] = (df.our_min - df.nhl_min).round(1)
    df["abs"] = df.diff_min.abs()

    print(f"Players compared: {len(df)}")
    print(f"Total PP minutes  ours: {df.our_min.sum():.0f}  NHL: {df.nhl_min.sum():.0f}")
    print(f"Median abs diff: {df['abs'].median():.2f} min   within 1 min: {(df['abs'] <= 1).mean():.0%}   "
          f"within 2 min: {(df['abs'] <= 2).mean():.0%}")
    top = df.sort_values("nhl_min", ascending=False).head(15)
    print("\nTop 15 by NHL PP TOI:")
    print(top[["skaterFullName", "teamAbbrevs", "nhl_min", "our_min", "diff_min"]].round(1).to_string(index=False))
    print("\nLargest differences:")
    print(df.sort_values("abs", ascending=False).head(10)[
        ["skaterFullName", "teamAbbrevs", "nhl_min", "our_min", "diff_min"]].round(1).to_string(index=False))


if __name__ == "__main__":
    main()

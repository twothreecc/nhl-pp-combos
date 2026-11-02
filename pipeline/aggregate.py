"""Scoresheet and on-ice combinations from data/pp_goals.csv, always keyed by team.

Writes data/scoresheet_pairs.csv, data/setup_connections.csv, data/scoresheet_trios.csv,
data/onice_pairs.csv, data/onice_units.csv.
Run from the repo root:  python pipeline/aggregate.py
"""
import json
from itertools import combinations

import pandas as pd

from config import DATA_DIR


# combos with no goals together are kept only once they've shared at least this much PP time
MIN_KEEP_S = 60


def with_rates(df):
    df = df.copy()
    df["goals"] = df.goals.astype(int)
    df["toi_min"] = (df.toi_s / 60).round(1)
    df["goals_per_60"] = (df.goals * 3600 / df.toi_s.where(df.toi_s > 0)).round(2)
    return df.sort_values(["goals", "toi_min"], ascending=False)


def main():
    goals = pd.read_csv(DATA_DIR / "pp_goals.csv", dtype={"a1_id": "Int64", "a2_id": "Int64"})
    with open(DATA_DIR / "players.json") as f:
        names = {int(k): v["name"] for k, v in json.load(f).items()}

    pairs, setups, trios = [], [], []
    for g in goals.itertuples():
        on_sheet = [int(x) for x in (g.scorer_id, g.a1_id, g.a2_id) if pd.notna(x)]
        for a, b in combinations(sorted(on_sheet), 2):
            pairs.append((g.team, a, b))
        for role, passer in (("a1", g.a1_id), ("a2", g.a2_id)):
            if pd.notna(passer):
                setups.append((g.team, int(passer), int(g.scorer_id), role))
        if len(on_sheet) == 3:
            trios.append((g.team, *sorted(on_sheet)))

    pairs = (
        pd.DataFrame(pairs, columns=["team", "p1_id", "p2_id"])
        .value_counts().rename("goals").reset_index()
    )
    pairs["p1"] = pairs.p1_id.map(names)
    pairs["p2"] = pairs.p2_id.map(names)
    pairs = pairs[["team", "p1_id", "p1", "p2_id", "p2", "goals"]].sort_values(["goals", "team"], ascending=[False, True])

    setups = pd.DataFrame(setups, columns=["team", "passer_id", "scorer_id", "role"])
    setups = (
        setups.pivot_table(index=["team", "passer_id", "scorer_id"], columns="role", aggfunc="size", fill_value=0)
        .reindex(columns=["a1", "a2"], fill_value=0)
        .reset_index()
    )
    setups.columns.name = None
    setups["total"] = setups.a1 + setups.a2
    setups["passer"] = setups.passer_id.map(names)
    setups["scorer"] = setups.scorer_id.map(names)
    setups = setups[["team", "passer_id", "passer", "scorer_id", "scorer", "a1", "a2", "total"]]
    setups = setups.sort_values(["total", "a1"], ascending=False)

    trios = (
        pd.DataFrame(trios, columns=["team", "p1_id", "p2_id", "p3_id"])
        .value_counts().rename("goals").reset_index()
    )
    for i in (1, 2, 3):
        trios[f"p{i}"] = trios[f"p{i}_id"].map(names)
    trios = trios[["team", "p1_id", "p1", "p2_id", "p2", "p3_id", "p3", "goals"]].sort_values("goals", ascending=False)

    # on-ice combinations: goals together plus PP time together (from pipeline/pp_toi.py)
    onice_pairs, units, player_goals = [], [], []
    for g in goals.dropna(subset=["on_ice_ids"]).itertuples():
        ids = sorted(int(x) for x in str(g.on_ice_ids).split(";"))
        units.append((g.team, ";".join(map(str, ids))))
        player_goals += [(g.team, i) for i in ids]
        for a, b in combinations(ids, 2):
            onice_pairs.append((g.team, a, b))

    toi = pd.read_csv(DATA_DIR / "pp_toi_units.csv", dtype={"player_ids": str}).dropna(subset=["player_ids"])
    unit_toi = toi.groupby(["team", "player_ids"]).seconds.sum()
    pair_toi, player_toi = {}, {}
    for (team, ids), secs in unit_toi.items():
        ids = [int(x) for x in ids.split(";")]
        for p in ids:
            player_toi[(team, p)] = player_toi.get((team, p), 0) + secs
        for a, b in combinations(ids, 2):
            pair_toi[(team, a, b)] = pair_toi.get((team, a, b), 0) + secs

    onice_pairs = (
        pd.DataFrame(onice_pairs, columns=["team", "p1_id", "p2_id"])
        .value_counts().rename("goals").reset_index()
    )
    onice_pairs = onice_pairs.merge(
        pd.Series(pair_toi, name="toi_s").rename_axis(["team", "p1_id", "p2_id"]).reset_index(),
        how="outer").fillna({"goals": 0, "toi_s": 0})
    onice_pairs = with_rates(onice_pairs[(onice_pairs.goals > 0) | (onice_pairs.toi_s >= MIN_KEEP_S)])
    onice_pairs["p1"] = onice_pairs.p1_id.map(names)
    onice_pairs["p2"] = onice_pairs.p2_id.map(names)
    onice_pairs = onice_pairs[["team", "p1_id", "p1", "p2_id", "p2", "goals", "toi_min", "goals_per_60"]]

    units = pd.DataFrame(units, columns=["team", "player_ids"]).value_counts().rename("goals").reset_index()
    units = units.merge(unit_toi.rename("toi_s").reset_index(), how="outer").fillna({"goals": 0, "toi_s": 0})
    units = with_rates(units[(units.goals > 0) | (units.toi_s >= MIN_KEEP_S)])
    units["skaters"] = units.player_ids.str.count(";") + 1
    units["players"] = units.player_ids.map(lambda s: "; ".join(names[int(i)] for i in s.split(";")))
    units = units[["team", "skaters", "player_ids", "players", "goals", "toi_min", "goals_per_60"]]

    players = pd.DataFrame(player_goals, columns=["team", "player_id"]).value_counts().rename("onice_goals").reset_index()
    players = players.merge(
        pd.Series(player_toi, name="toi_s").rename_axis(["team", "player_id"]).reset_index(),
        how="outer").fillna({"onice_goals": 0, "toi_s": 0})
    players["player"] = players.player_id.map(names)
    players["toi_min"] = (players.toi_s / 60).round(1)
    players = players[["team", "player_id", "player", "onice_goals", "toi_min"]].sort_values("toi_min", ascending=False)

    onice_pairs.to_csv(DATA_DIR / "onice_pairs.csv", index=False)
    units.to_csv(DATA_DIR / "onice_units.csv", index=False)
    players.to_csv(DATA_DIR / "player_pp_toi.csv", index=False)
    print(f"On-ice pairs: {len(onice_pairs)}  on-ice units: {len(units)}  players with PP TOI: {len(players)}")

    pairs.to_csv(DATA_DIR / "scoresheet_pairs.csv", index=False)
    setups.to_csv(DATA_DIR / "setup_connections.csv", index=False)
    trios.to_csv(DATA_DIR / "scoresheet_trios.csv", index=False)
    print(f"Scoresheet pairs: {len(pairs)}  setup connections: {len(setups)}  trios: {len(trios)}")


if __name__ == "__main__":
    main()

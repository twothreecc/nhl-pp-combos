"""Load the pipeline's per-season tables (data/history/* plus the current season) into one place."""
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
from config import HISTORY_SEASONS, SEASON, out_dir  # noqa: E402

STATES = ("ev", "pp", "sh", "other")
# Arizona's roster moved to Utah in 2024-25; treat them as one team so Utah has history
FRANCHISE = {"ARI": "UTA"}


def bucket(state):
    return state.lower() if state in ("EV", "PP", "SH") else "other"


def _concat(name, **kw):
    frames = []
    for season in HISTORY_SEASONS + [SEASON]:
        path = out_dir(season) / name
        if path.exists():
            frames.append(pd.read_csv(path, **kw).assign(season=season))
    df = pd.concat(frames, ignore_index=True)
    for col in ("team", "opponent"):
        if col in df:
            df[col] = df[col].replace(FRANCHISE)
    if "date" in df:
        df["date"] = pd.to_datetime(df.date)
    return df


class Db:
    """Every season's team_games, player_games, units and goals, with a few derived columns."""

    def __init__(self):
        self.players = {}
        for season in HISTORY_SEASONS + [SEASON]:
            path = out_dir(season) / "players.json"
            if path.exists():
                with open(path) as f:
                    self.players.update({int(k): v for k, v in json.load(f).items()})

        tg = _concat("team_games.csv")
        opp = tg[["game_id", "team", "gf_ev", "gf_pp", "gf_sh"]].rename(
            columns={"team": "opponent", "gf_ev": "opp_gf_ev", "gf_pp": "opp_gf_pp", "gf_sh": "opp_gf_sh"})
        self.team_games = tg.merge(opp, on=["game_id", "opponent"]).sort_values(["date", "game_id"])

        self.player_games = _concat("player_games.csv")
        self.player_games["pos"] = (self.player_games.position == "D").map({True: "D", False: "F"})

        units = _concat("units.csv", dtype={"player_ids": str})
        units["members"] = units.player_ids.map(lambda s: tuple(int(x) for x in s.split(";")))
        self.units = {team: df for team, df in units.groupby("team")}

        goals = _concat("goals.csv", dtype={"a1_id": "Int64", "a2_id": "Int64"})
        skater = lambda x: pd.notna(x) and self.players.get(int(x), {}).get("position") != "G"  # noqa: E731
        goals["bucket"] = goals.state.map(bucket)
        goals["a1s"] = goals.a1_id.map(skater)
        goals["a2s"] = goals.a2_id.map(skater)
        goals["other"] = goals.bucket == "other"
        self.goals = goals

        # one row per game, home side first: regulation goals split into "other" (EN / EA / PS) and the rest
        reg = goals[goals.period <= 3]
        sides = reg.groupby(["game_id", "team", "other"]).size().unstack(fill_value=0).reindex(
            columns=[False, True], fill_value=0)
        sides.columns = ["base", "other"]
        home = self.team_games[self.team_games.home == 1][["game_id", "date", "team", "opponent"]]
        script = home.merge(sides.add_prefix("h_"), left_on=["game_id", "team"], right_index=True, how="left")
        script = script.merge(sides.add_prefix("a_"), left_on=["game_id", "opponent"], right_index=True, how="left")
        self.scripts = script.fillna(0)

    def name(self, pid):
        return self.players.get(pid, {}).get("name", str(pid))

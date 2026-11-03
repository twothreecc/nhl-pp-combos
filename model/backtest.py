"""Out-of-sample check: price every game of a season using only data from before that game.

Lineups are the skaters who actually dressed (projected lineups come later), so this tests the
rates and the correlation structure rather than lineup guessing. Reports:
  - single props (point, goal): log loss and calibration against a simple points-per-game baseline;
  - teammate pairs both getting a point: model joint probability against the same marginals
    treated as independent, and predicted vs observed lift by bucket;
  - team goals per game.
Writes the per-player and per-pair predictions to the scratch folder given by --out.

    python model/backtest.py [--season 20252026] [--sims 4000] [--every 1] [--out DIR]
"""
import argparse
import time
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd

from data import Db
from rates import Ratings, decay
from sim import build_spec, simulate

EPS = 1e-6


def logloss(p, y):
    p = np.clip(p, EPS, 1 - EPS)
    return -np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))


def baseline_ppg(db, as_of, pids_pos):
    """Recency-weighted points and goals per game, shrunk to the position mean: a naive Poisson prop price."""
    pg = db.player_games[db.player_games.date < as_of]
    w = decay(pg.date, as_of)
    df = pd.DataFrame({"player_id": pg.player_id, "pos": pg.pos, "w": w, "pts": pg.pts * w, "g": pg.g * w})
    sums = df.groupby("player_id")[["w", "pts", "g"]].sum()
    pos_mean = df.groupby("pos")[["pts", "g"]].sum().div(df.groupby("pos").w.sum(), axis=0)
    out = {}
    for pid, pos in pids_pos:
        s = sums.loc[pid] if pid in sums.index else pd.Series({"w": 0, "pts": 0, "g": 0})
        lam_p = (s.pts + 5 * pos_mean.loc[pos, "pts"]) / (s.w + 5)
        lam_g = (s.g + 5 * pos_mean.loc[pos, "g"]) / (s.w + 5)
        out[pid] = (1 - np.exp(-lam_p), 1 - np.exp(-lam_g))
    return out


def calibration(p, y, bins):
    df = pd.DataFrame({"p": p, "y": y})
    df["bin"] = pd.cut(df.p, bins)
    return df.groupby("bin", observed=True).agg(n=("y", "size"), predicted=("p", "mean"), observed=("y", "mean"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", default="20252026")
    ap.add_argument("--sims", type=int, default=4000)
    ap.add_argument("--every", type=int, default=1, help="price every k-th game, for a quicker run")
    ap.add_argument("--out", default=".")
    args = ap.parse_args()

    db = Db()
    players, pairs, teams = run(db, args.season, args.sims, args.every)
    out = Path(args.out)
    players.to_csv(out / f"backtest_players_{args.season}.csv", index=False)
    pairs.to_csv(out / f"backtest_pairs_{args.season}.csv", index=False)
    teams.to_csv(out / f"backtest_teams_{args.season}.csv", index=False)
    report(players, pairs, teams, db, args.season)


def run(db, season, sims, every=1, quiet=False):
    tg = db.team_games
    games = tg[(tg.season == season) & (tg.home == 1)].sort_values(["date", "game_id"]).iloc[::every]
    pg = db.player_games.set_index("game_id")
    players, pairs, teams = [], [], []
    ratings, t0 = None, time.time()
    for k, g in enumerate(games.itertuples(), 1):
        if ratings is None or ratings.as_of != g.date:
            ratings = Ratings(db, g.date)
        dressed = pg.loc[[g.game_id]]
        lineups = {side: list(zip(d.player_id, d.pos)) for side, d in
                   (("home", dressed[dressed.team == g.team]), ("away", dressed[dressed.team == g.opponent]))}
        spec = build_spec(ratings, g.team, g.opponent, lineups)
        res = simulate(spec, sims, seed=g.game_id % 100000)
        base = baseline_ppg(db, g.date, lineups["home"] + lineups["away"])
        actual = dressed.set_index("player_id")

        pts = res.points >= 1
        p_pt, p_g = pts.mean(0), (res.goals >= 1).mean(0)
        for i, pid in enumerate(spec.players):
            if pid < 0:
                continue
            players.append({"game_id": g.game_id, "player_id": pid, "p_point": p_pt[i], "p_goal": p_g[i],
                            "x_pts": res.points[:, i].mean(), "x_g": res.goals[:, i].mean(),
                            "pts": actual.loc[pid, "pts"], "g": actual.loc[pid, "g"], "pos": actual.loc[pid, "pos"],
                            "base_point": base[pid][0], "base_goal": base[pid][1],
                            "point": int(actual.loc[pid, "pts"] >= 1), "goal": int(actual.loc[pid, "g"] >= 1)})
        real = [i for i, pid in enumerate(spec.players) if pid > 0]
        for i, j in combinations(real, 2):
            if spec.team_of[i] != spec.team_of[j]:
                continue
            a, b = spec.players[i], spec.players[j]
            pairs.append({"game_id": g.game_id, "p1": a, "p2": b, "joint": (pts[:, i] & pts[:, j]).mean(),
                          "indep": p_pt[i] * p_pt[j],
                          "both": int(actual.loc[a, "pts"] >= 1 and actual.loc[b, "pts"] >= 1)})
        teams.append({"game_id": g.game_id, "xg_home": res.home_goals.mean(), "xg_away": res.away_goals.mean(),
                      "home_goals": g.gf, "away_goals": g.ga})
        if not quiet and (k % 100 == 0 or k == len(games)):
            print(f"  {k}/{len(games)} games  ({time.time() - t0:.0f}s)", flush=True)

    return pd.DataFrame(players), pd.DataFrame(pairs), pd.DataFrame(teams)


def report(players, pairs, teams, db, season):
    print(f"\n=== Backtest {season}: {teams.game_id.nunique()} games, {len(players)} player-games, "
          f"{len(pairs)} teammate pairs ===")
    print("\nSingle props (log loss, lower is better):")
    for stat in ("point", "goal"):
        y = players[stat]
        print(f"  {stat:<6} model {logloss(players[f'p_{stat}'], y):.4f}   ppg baseline "
              f"{logloss(players[f'base_{stat}'], y):.4f}   base rate {y.mean():.3f}  "
              f"mean predicted {players[f'p_{stat}'].mean():.3f}")
    print("\nPoint calibration:")
    print(calibration(players.p_point, players.point, [0, .1, .2, .3, .4, .5, .6, .7, 1]).round(3).to_string())

    print("\nTeammate pairs, both get a point (log loss):")
    print(f"  model joint {logloss(pairs.joint, pairs.both):.5f}   "
          f"same marginals as if independent {logloss(pairs.indep, pairs.both):.5f}")
    pairs = pairs.assign(lift=pairs.joint / pairs.indep.clip(lower=EPS))
    pairs["bucket"] = pd.cut(pairs.lift, [0, 1.0, 1.1, 1.2, 1.35, 1.5, 1.75, 10])
    lift = pairs.groupby("bucket", observed=True).agg(
        n=("both", "size"), indep=("indep", "sum"), joint=("joint", "sum"), both=("both", "sum"))
    lift["predicted lift"] = lift.joint / lift.indep
    lift["observed lift"] = lift.both / lift.indep
    print("\nLift by bucket (observed lift = actual both-point games / independent expectation):")
    print(lift[["n", "predicted lift", "observed lift"]].round(3).to_string())

    hg = teams.home_goals + teams.away_goals
    print(f"\nGoals per game: predicted {(teams.xg_home + teams.xg_away).mean():.2f}  actual {hg.mean():.2f}   "
          f"home {teams.xg_home.mean():.2f} vs {teams.home_goals.mean():.2f}, "
          f"away {teams.xg_away.mean():.2f} vs {teams.away_goals.mean():.2f}")


if __name__ == "__main__":
    main()

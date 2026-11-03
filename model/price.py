"""Price player props and same-game parlays for one game.

    python model/price.py AWAY HOME [--date YYYY-MM-DD] [--leg "McDavid point" ...] [--book +450]
        [--home-xg 3.4 --away-xg 2.9]

Legs: "<player> goal|assist|point", "<player> 2+ points" (also goals / assists), "<TEAM> win",
"over 6.5" / "under 6.5" (game total, where a shootout adds one goal, as books settle it).
Player names match on any part of the name, e.g. "McDavid" or "Connor McD".

Lineups are each team's skaters from its most recent game until projected lineups are added.
--home-xg / --away-xg scale the simulation to the market's implied team totals (no shootout goal).
Without --leg, prints every skater's prop prices and the most correlated teammate pairs.

The backtest shows the simulation overstates how much teammates' points move together (observed
lift above independence is about 80% of predicted, fitted on 2024-25 and holding on 2025-26), so
parlays are also shown with that much of the correlation kept, and expected value uses that figure.
"""
import argparse
import re
import sys
from datetime import date
from itertools import combinations

import numpy as np
import pandas as pd

from data import Db
from rates import Ratings
from sim import build_spec, match_market, simulate

CORR_KEEP = 0.8  # share of the simulated lift over independence that held up out of sample


def last_lineup(db, team, before):
    pg = db.player_games
    games = pg[(pg.team == team) & (pg.date < before)]
    if games.empty:
        sys.exit(f"No games for {team} before {before.date()}")
    last = games[games.game_id == games.game_id.max()]
    return list(zip(last.player_id, last.pos))


def american(p):
    if p <= 0:
        return "-"
    if p >= 1:
        return "-inf"
    return f"+{100 * (1 - p) / p:.0f}" if p < 0.5 else f"{-100 * p / (1 - p):.0f}"


def to_prob(odds):
    o = float(odds)
    return 100 / (o + 100) if o > 0 else -o / (-o + 100)


LEG = re.compile(r"^(?P<who>.+?)\s+(?:(?P<n>\d+)\+\s*)?(?P<stat>goals?|assists?|points?)$", re.I)


def parse_leg(text, res, db):
    """Return (label, boolean mask over sims)."""
    spec = res.spec
    t = text.strip()
    m = re.match(r"^(over|under)\s+([\d.]+)$", t, re.I)
    if m:
        total = res.home_goals + res.away_goals + res.shootout
        line = float(m.group(2))
        return f"{m.group(1).title()} {line}", (total > line) if m.group(1).lower() == "over" else (total < line)
    m = re.match(r"^([A-Za-z]{3})\s+(win|ml)$", t, re.I)
    if m:
        team = m.group(1).upper()
        if team not in (spec.home, spec.away):
            sys.exit(f"{team} isn't in this game")
        return f"{team} win", res.home_win if team == spec.home else ~res.home_win
    m = LEG.match(t)
    if not m:
        sys.exit(f"Can't read leg: {text!r}")
    who, n, stat = m.group("who").lower(), int(m.group("n") or 1), m.group("stat").lower().rstrip("s")
    hits = [i for i, p in enumerate(spec.players) if p > 0 and who in db.name(p).lower()]
    if len(hits) != 1:
        names = ", ".join(db.name(spec.players[i]) for i in hits) or "nobody in either lineup"
        sys.exit(f"{text!r} matches {names}")
    i = hits[0]
    arr = {"goal": res.goals, "assist": res.assists, "point": res.points}[stat][:, i]
    label = f"{db.name(spec.players[i])} {'' if n == 1 else f'{n}+ '}{stat}{'s' if n > 1 else ''}"
    return label, arr >= n


def prop_table(res, db):
    rows = []
    for i, pid in enumerate(res.spec.players):
        if pid < 0:
            continue
        rows.append({"team": [res.spec.home, res.spec.away][res.spec.team_of[i]], "player": db.name(pid),
                     "point": (res.points[:, i] >= 1).mean(), "goal": (res.goals[:, i] >= 1).mean(),
                     "assist": (res.assists[:, i] >= 1).mean(), "2+ pts": (res.points[:, i] >= 2).mean()})
    return pd.DataFrame(rows).sort_values(["team", "point"], ascending=[True, False])


def top_pairs(res, db, k=12, min_p=0.3):
    pts = res.points >= 1
    p = pts.mean(0)
    rows = []
    for i, j in combinations(np.flatnonzero((p >= min_p) & (np.array(res.spec.players) > 0)), 2):
        if res.spec.team_of[i] != res.spec.team_of[j]:
            continue
        joint = (pts[:, i] & pts[:, j]).mean()
        rows.append({"pair": f"{db.name(res.spec.players[i])} + {db.name(res.spec.players[j])}",
                     "joint": joint, "if independent": p[i] * p[j], "lift": joint / (p[i] * p[j]),
                     "fair": american(joint)})
    return pd.DataFrame(rows).sort_values("lift", ascending=False).head(k)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("away")
    ap.add_argument("home")
    ap.add_argument("--date", default=str(date.today()))
    ap.add_argument("--leg", action="append", default=[])
    ap.add_argument("--book", help="the book's American odds for the parlay, to show expected value")
    ap.add_argument("--home-xg", type=float)
    ap.add_argument("--away-xg", type=float)
    ap.add_argument("--sims", type=int, default=40000)
    args = ap.parse_args(argv)

    db = Db()
    as_of = pd.Timestamp(args.date)
    home, away = args.home.upper(), args.away.upper()
    ratings = Ratings(db, as_of)
    spec = build_spec(ratings, home, away, {"home": last_lineup(db, home, as_of), "away": last_lineup(db, away, as_of)})
    if args.home_xg and args.away_xg:
        spec = match_market(spec, args.home_xg, args.away_xg)
    res = simulate(spec, args.sims)

    print(f"{away} @ {home}, {as_of.date()}   expected goals {away} {res.away_goals.mean():.2f}  "
          f"{home} {res.home_goals.mean():.2f}   {home} win {res.home_win.mean():.1%}")
    for note in spec.notes:
        print(f"  lineup: {note}")

    if not args.leg:
        fmt = {c: "{:.1%}".format for c in ("point", "goal", "assist", "2+ pts")}
        print("\n" + prop_table(res, db).to_string(index=False, formatters=fmt))
        print("\nMost correlated teammate point pairs:")
        print(top_pairs(res, db).to_string(index=False, formatters={
            "joint": "{:.1%}".format, "if independent": "{:.1%}".format, "lift": "{:.2f}".format}))
        return

    legs = [parse_leg(t, res, db) for t in args.leg]
    print()
    for label, mask in legs:
        print(f"  {label:<32} {mask.mean():6.1%}  fair {american(mask.mean())}")
    joint = np.logical_and.reduce([m for _, m in legs]).mean()
    indep = np.prod([m.mean() for _, m in legs])
    calibrated = indep * (1 + CORR_KEEP * (joint / indep - 1))
    print(f"\n  Parlay: {joint:.2%}  fair {american(joint)}   "
          f"(if independent {indep:.2%}, {american(indep)}; lift {joint / indep:.2f})")
    print(f"  Calibrated (keeps {CORR_KEEP:.0%} of the lift): {calibrated:.2%}  fair {american(calibrated)}")
    if args.book:
        ev = calibrated / to_prob(args.book) - 1
        print(f"  Book {args.book}: implied {to_prob(args.book):.2%}, "
              f"expected value {ev:+.1%} per unit staked (calibrated)")


if __name__ == "__main__":
    main()

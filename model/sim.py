"""Goal-by-goal Monte Carlo of one game, giving the joint distribution of every skater's goals and assists.

For each simulated game:
1. Each team's regulation EV, PP and SH goals are Poisson draws around Ratings.expected_goals.
2. Empty-net / extra-attacker goals are drawn from what actually happened in past games with the
   same regulation margin, so a team protecting a one-goal lead gets its EN goals here.
3. A game tied after regulation gets an OT goal with the historical probability, else a shootout
   (no goals credited; the winner is a coin flip).
4. For every goal, an on-ice group is drawn from the team's recent groups at that strength
   (weighted by seconds together and how well they score), then a scorer, a primary assist and a
   secondary assist are drawn from the skaters on the ice by their shares.

Correlation between legs comes out of this structure: linemates share goals, a team's players all
move with its goal total, and the leading team gets the empty-net goals.
"""
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from rates import SIM_STATES

PAD = -1
REL_POWER = 2.0   # how hard a group's on-ice scoring rate tilts which group is on for a goal
SIDES = ("home", "away")
# Share of a state's group-seconds that goes to projected lines (the rest stays with the recent
# groups recast onto tonight's lineup). Not fitted yet: there's no archive of projected lines to fit it on.
LINES_WEIGHT = 0.5
# How ice time splits across each team's lines, top first: the full-size groups' split, 2025-26
SLOT_SIZE = {"ev_F": 3, "ev_D": 2, "pp_PP": 5, "sh_F": 2, "sh_D": 2}
# How hard tonight's lineup quality (its skaters' points rates weighted by projected ice time, against
# the team's last 10 games) moves the team's expected goals: 0 ignores it, 1 is proportional
LINEUP_POWER = 1.0
SLOT_SHARE = {"ev_F": [0.330, 0.273, 0.225, 0.171], "ev_D": [0.402, 0.336, 0.261], "pp_PP": [0.724, 0.276],
              "sh_F": [0.62, 0.38], "sh_D": [0.656, 0.344]}


@dataclass
class Sampler:
    """On-ice groups for one component (forwards, defence or a PP unit): member indices + cumulative weights."""
    members: np.ndarray   # groups x width, global player index, PAD-padded
    cum: np.ndarray

    def draw(self, rng, n):
        idx = np.searchsorted(self.cum, rng.random(n) * self.cum[-1], side="right")
        return self.members[np.minimum(idx, len(self.cum) - 1)]


@dataclass
class GameSpec:
    home: str
    away: str
    players: list                 # global index -> player id (placeholders are negative)
    team_of: np.ndarray           # global index -> 0 home / 1 away
    shares: dict                  # (role, state) -> array over global index (+1 trailing zero for PAD)
    samplers: dict                # (side, state) -> [Sampler, ...]
    lam: dict                     # side -> state -> expected regulation goals
    p_a1: dict
    p_a2: dict
    other_dist: dict
    p_ot_goal: float
    notes: list = field(default_factory=list)

    def scaled(self, home_factor, away_factor):
        """Copy with every expected-goal rate multiplied, per side (used to match market totals)."""
        lam = {"home": {k: v * home_factor for k, v in self.lam["home"].items()},
               "away": {k: v * away_factor for k, v in self.lam["away"].items()}}
        return GameSpec(**{**self.__dict__, "lam": lam})


def with_lines(units, lines, lineup, placeholder):
    """Blend projected lines (lineups.Lineup.lines) into the recent on-ice groups, LINES_WEIGHT of each state's seconds.

    A projected line with someone missing (ruled out, or a name that didn't match) gets a
    replacement-level player in the empty spot, so his linemates don't inherit his share.
    """
    out = []
    for key, groups in lines.items():
        state, group = key.split("_")
        fill = placeholder["D"] if group == "D" else placeholder["F"]
        groups = [[p for p in g if p in lineup] for g in groups[:len(SLOT_SHARE[key])]]
        groups = [tuple(sorted(g + [fill] * (SLOT_SIZE[key] - len(g)))) for g in groups if g]
        if not groups:
            continue
        mask = (units.state == state) & (units.group == group)
        total = units.seconds[mask].sum()
        if total <= 0:
            continue
        share = np.array(SLOT_SHARE[key][:len(groups)])
        units = units.assign(seconds=np.where(mask, units.seconds * (1 - LINES_WEIGHT), units.seconds))
        out += [(state, group, g, total * LINES_WEIGHT * w) for g, w in zip(groups, share / share.sum())]
    if not out:
        return units
    added = pd.DataFrame(out, columns=["state", "group", "members", "seconds"])
    return pd.concat([units, added]).groupby(["state", "group", "members"], as_index=False).seconds.sum()


def build_spec(ratings, home, away, lineups, replacement=0.85, goalies=None, lines=None):
    """lineups: {'home': [(player_id, 'F'|'D'), ...], 'away': [...]}: tonight's dressed skaters.
    goalies: {'home': player_id, 'away': player_id}: tonight's starters, if known.
    lines: {'home': Lineup.lines, 'away': ...}: projected lines, if known."""
    players, team_of, rows, notes = [], [], [], []
    quality = {}
    samplers = {}
    index = {}
    for t, side in enumerate(SIDES):
        team = home if side == "home" else away
        lineup = dict(lineups[side])
        for pid, pos in lineup.items():
            index[pid] = len(players)
            players.append(pid)
            team_of.append(t)
            rows.append(ratings.player(pid, pos))
        placeholder = {}
        for pos in ("F", "D"):
            ph = -(2 * t + (1 if pos == "F" else 2))
            placeholder[pos] = ph
            index[ph] = len(players)
            players.append(ph)
            team_of.append(t)
            rows.append(ratings.player(ph, pos, replacement))

        units, swaps = ratings.recent_units(team, lineup, placeholder)
        if lines and lines.get(side):
            units = with_lines(units, lines[side], lineup, placeholder)
        quality[side] = {st: (ratings.lineup_quality(units, st) / ratings.recent_quality(team, st)) ** LINEUP_POWER
                         for st in SIM_STATES}
        lost = [p for p, to in swaps.items() if any(q < 0 for q in to)]
        if lost:
            notes.append(f"{team}: replacement-level fill-in for " + ", ".join(ratings.db.name(p) for p in lost))
        present = {p for m in units.members for p in m}
        missing = [p for p in lineup if p not in present]
        if missing:
            notes.append(f"{team}: no recent ice time for " + ", ".join(ratings.db.name(p) for p in missing))

        rel = {pid: rows[index[pid]] for pid in list(lineup) + list(placeholder.values())}
        for state in SIM_STATES:
            comps = []
            groups = ["PP"] if state == "pp" else ["F", "D"]
            for group in groups:
                u = units[(units.state == state) & (units.group == group)]
                if u.empty:
                    continue
                mem = list(u.members)
                width = max(len(m) for m in mem)
                arr = np.full((len(mem), width), PAD)
                weights = np.empty(len(mem))
                for i, m in enumerate(mem):
                    arr[i, :len(m)] = [index[p] for p in m]
                    weights[i] = u.seconds.iloc[i] * np.mean([rel[p][f"rel_{state}"] for p in m]) ** REL_POWER
                comps.append(Sampler(arr, np.cumsum(weights)))
            samplers[(side, state)] = comps

    # a1 / a2 are shares of all on-ice goals, but a skater can only assist on goals someone else
    # scored (and a2 only when they didn't get a1), so draw weights are conditioned on that;
    # otherwise defencemen, who rarely score, soak up the assists left over by forward scorers
    shares = {}
    for state in SIM_STATES:
        g = np.array([r[f"g_{state}"] for r in rows])
        a1 = np.array([r[f"a1_{state}"] for r in rows])
        a2 = np.array([r[f"a2_{state}"] for r in rows])
        shares[("g", state)] = np.append(g, 0.0)
        shares[("a1", state)] = np.append(a1 / np.clip(1 - g, 0.05, None), 0.0)
        shares[("a2", state)] = np.append(a2 / np.clip(1 - g - a1, 0.05, None), 0.0)
    goalies = goalies or {}
    xg = ratings.expected_goals(home, away, goalies.get("home"), goalies.get("away"))
    # tonight's lineup against the ones behind the team ratings: a missing star lowers the team's goals
    xg = {side: {st: lam * quality[side][st] for st, lam in xg[side].items()} for side in SIDES}
    return GameSpec(home, away, players, np.array(team_of), shares, samplers, xg,
                    ratings.p_a1, ratings.p_a2, ratings.other_dist, ratings.p_ot_goal, notes)


def _pick(rng, weights):
    """Row-wise categorical draw; returns column index, or -1 where a row has no weight."""
    cum = weights.cumsum(1)
    total = cum[:, -1]
    r = rng.random(len(weights)) * total
    idx = np.minimum((cum <= r[:, None]).sum(1), weights.shape[1] - 1)
    return np.where(total > 0, idx, -1)


@dataclass
class Result:
    spec: GameSpec
    goals: np.ndarray        # sims x players
    assists: np.ndarray
    home_goals: np.ndarray   # excludes the shootout
    away_goals: np.ndarray
    home_win: np.ndarray
    shootout: np.ndarray

    @property
    def points(self):
        return self.goals + self.assists

    def col(self, pid):
        return self.spec.players.index(pid)


def simulate(spec, n=20000, seed=0):
    rng = np.random.default_rng(seed)
    base = {s: {st: rng.poisson(spec.lam[s][st], n) for st in SIM_STATES} for s in SIDES}
    h_base = sum(base["home"].values())
    a_base = sum(base["away"].values())
    margin = h_base - a_base

    # empty-net / extra-attacker goals from past games with the same margin
    lead, trail = np.zeros(n, int), np.zeros(n, int)
    b = np.minimum(np.abs(margin), 4)
    for m, dist in spec.other_dist.items():
        idx = np.flatnonzero(b == m)
        if len(idx):
            draw = dist[rng.integers(len(dist), size=len(idx))]
            lead[idx], trail[idx] = draw[:, 0], draw[:, 1]
    # for a tied game "lead" is home, "trail" is away
    h_other = np.where(margin >= 0, lead, trail)
    a_other = np.where(margin >= 0, trail, lead)

    h_reg, a_reg = h_base + h_other, a_base + a_other
    tied = h_reg == a_reg
    ot_goal = tied & (rng.random(n) < spec.p_ot_goal)
    ev_h, ev_a = spec.lam["home"]["ev"], spec.lam["away"]["ev"]
    ot_home = ot_goal & (rng.random(n) < ev_h / (ev_h + ev_a))
    ot_away = ot_goal & ~ot_home
    shootout = tied & ~ot_goal
    home_goals, away_goals = h_reg + ot_home, a_reg + ot_away
    home_win = (home_goals > away_goals) | (shootout & (rng.random(n) < 0.5))

    # every goal as an event: (sim, side, unit state, assist state)
    events = []
    for t, side in enumerate(SIDES):
        for st in SIM_STATES:
            events.append((base[side][st], t, st, st))
        other = h_other if side == "home" else a_other
        leading = margin > 0 if side == "home" else margin < 0
        # a leading team's late goals are mostly into an empty net (EV-type groups); a trailing
        # team's come with the goalie pulled, from its top (PP-type) group
        events.append((np.where(leading | (margin == 0), other, 0), t, "ev", "en"))
        events.append((np.where(~leading & (margin != 0), other, 0), t, "pp", "ea"))
        events.append(((ot_home if side == "home" else ot_away).astype(int), t, "ev", "ev"))

    P = len(spec.players)
    goals = np.zeros((n, P + 1), np.int16)
    assists = np.zeros((n, P + 1), np.int16)
    for counts, t, ustate, astate in events:
        sims = np.repeat(np.arange(n), counts)
        if not len(sims):
            continue
        comps = spec.samplers.get((SIDES[t], ustate)) or spec.samplers[(SIDES[t], "ev")]
        on = np.concatenate([c.draw(rng, len(sims)) for c in comps], 1)
        on_idx = np.where(on == PAD, P, on)  # PAD -> trailing zero-share column

        w = spec.shares[("g", ustate)][on_idx]
        s_col = _pick(rng, w)
        scorer = np.where(s_col >= 0, on_idx[np.arange(len(sims)), np.maximum(s_col, 0)], P)
        np.add.at(goals, (sims, scorer), 1)

        taken = on_idx == scorer[:, None]
        has1 = rng.random(len(sims)) < spec.p_a1[astate]
        w1 = np.where(taken, 0, spec.shares[("a1", ustate)][on_idx])
        c1 = _pick(rng, w1)
        a1 = np.where(has1 & (c1 >= 0), on_idx[np.arange(len(sims)), np.maximum(c1, 0)], P)
        np.add.at(assists, (sims, a1), 1)

        taken |= on_idx == a1[:, None]
        has2 = has1 & (rng.random(len(sims)) < spec.p_a2[astate])
        w2 = np.where(taken, 0, spec.shares[("a2", ustate)][on_idx])
        c2 = _pick(rng, w2)
        a2 = np.where(has2 & (c2 >= 0), on_idx[np.arange(len(sims)), np.maximum(c2, 0)], P)
        np.add.at(assists, (sims, a2), 1)

    return Result(spec, goals[:, :P], assists[:, :P], home_goals, away_goals, home_win, shootout)


def match_market(spec, home_xg, away_xg, n=20000, iters=4):
    """Scale each side's rates so the simulated mean goals (no shootout) hit the given team totals."""
    fh = fa = 1.0
    for _ in range(iters):
        r = simulate(spec.scaled(fh, fa), n, seed=1)
        fh *= home_xg / r.home_goals.mean()
        fa *= away_xg / r.away_goals.mean()
    return spec.scaled(fh, fa)

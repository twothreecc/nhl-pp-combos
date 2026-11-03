"""Team and player rates as of a date, from every earlier game.

Everything is weighted by recency (half-life HALF_LIFE_DAYS) and shrunk toward league or position
averages, so a player or team with little data sits near the average.

Team (per strength, per second of that strength): EV goals for/against, PP time drawn/conceded,
PP and PK goal rates, shorthanded goal rates. Goalie: goals allowed against expected goals on the
shots they faced (expected goals from a logistic fit on shot distance, angle, type, rebound and
strength), which adjusts the opponent's rates for tonight's starter against the team's usual mix. Player, per strength: share of on-ice team goals they
score (g), get the primary (a1) or secondary (a2) assist on, and how much their on-ice goal rate
runs above or below the league (rel), which tilts which unit is on the ice for a goal.
"""
import numpy as np
import pandas as pd

HALF_LIFE_DAYS = 120
TEAM_PRIOR_GAMES = 15        # games of league-average play mixed into each team rate
SHARE_PRIOR = {"ev": 25, "pp": 15, "sh": 6}   # on-ice goals of position-average shares
REL_PRIOR_S = {"ev": 2 * 3600, "pp": 3600, "sh": 2 * 3600}  # on-ice seconds at the league rate
A1_PRIOR = 10                # assists of league-average a1/a2 split
POINTS_PRIOR_S = {"ev": 10 * 3600, "pp": 3 * 3600, "sh": 3 * 3600}  # seconds at the position's points rate
REPLACEMENT_REL = 0.94      # on-ice scoring rate of bottom-quartile-TOI skaters (league = 1)
REPLACEMENT_POINTS = 0.7     # a call-up's points rate against his position's average (bottom-quartile TOI, ~0.67-0.77 at EV)
UNIT_GAMES = 10              # recent games whose on-ice groups are sampled
UNIT_DECAY = 0.85            # weight per game back
SIM_STATES = ("ev", "pp", "sh")
GOALIE_PRIOR_XG = 60         # expected goals faced of league-average goaltending mixed into each goalie
DIST_BINS = [0, 10, 15, 20, 25, 30, 40, 50, 60, 1000]
ANGLE_BINS = [-1, 20, 40, 60, 90, 181]
SHOT_TYPES = {"wrist": "wrist", "snap": "wrist", "slap": "slap", "backhand": "backhand",
              "wrap-around": "wrap"}   # everything else (tip-in, deflected, bat, poke, ...) is a tip


def decay(dates, as_of):
    return 0.5 ** ((as_of - dates).dt.days.to_numpy() / HALF_LIFE_DAYS)


def shrink(num, den, prior_den, rate):
    """Shrunk rate relative to `rate`: 1.0 means league average."""
    return (num + prior_den * rate) / (den + prior_den) / rate


class Ratings:
    def __init__(self, db, as_of):
        self.db = db
        self.as_of = pd.Timestamp(as_of)
        self._quality = {}
        self._fit_teams()
        self._fit_players()
        self._fit_script()
        self._fit_goalies()

    # ---- teams ----
    def _fit_teams(self):
        tg = self.db.team_games[self.db.team_games.date < self.as_of].copy()
        tg["w"] = decay(tg.date, self.as_of)
        wsum = lambda df, c: (df[c] * df.w).sum()  # noqa: E731
        home, away = tg[tg.home == 1], tg[tg.home == 0]
        L = {
            "ev": wsum(tg, "gf_ev") / wsum(tg, "s_ev"),
            "ev_home": wsum(home, "gf_ev") / wsum(home, "s_ev"),
            "ev_away": wsum(away, "gf_ev") / wsum(away, "s_ev"),
            "pp": wsum(tg, "gf_pp") / wsum(tg, "s_pp"),
            "sh": wsum(tg, "gf_sh") / wsum(tg, "s_sh"),
            "pp_s_home": wsum(home, "s_pp") / home.w.sum(),
            "pp_s_away": wsum(away, "s_pp") / away.w.sum(),
            "pp_s": wsum(tg, "s_pp") / tg.w.sum(),
            "ev_s": wsum(tg, "s_ev") / tg.w.sum(),
            "other_s": wsum(tg, "s_other") / tg.w.sum(),
        }
        self.league = L
        P = TEAM_PRIOR_GAMES
        rows = {}
        for team, df in tg.groupby("team"):
            n = df.w.sum()
            rows[team] = {
                "ev_att": shrink(wsum(df, "gf_ev"), wsum(df, "s_ev"), P * L["ev_s"], L["ev"]),
                "ev_def": shrink(wsum(df, "opp_gf_ev"), wsum(df, "s_ev"), P * L["ev_s"], L["ev"]),
                "pp_draw": (wsum(df, "s_pp") + P * L["pp_s"]) / (n + P) / L["pp_s"],
                "pp_take": (wsum(df, "s_sh") + P * L["pp_s"]) / (n + P) / L["pp_s"],
                "pp_att": shrink(wsum(df, "gf_pp"), wsum(df, "s_pp"), 2 * P * L["pp_s"], L["pp"]),
                "pk_def": shrink(wsum(df, "opp_gf_pp"), wsum(df, "s_sh"), 2 * P * L["pp_s"], L["pp"]),
                "sh_att": shrink(wsum(df, "gf_sh"), wsum(df, "s_sh"), 4 * P * L["pp_s"], L["sh"]),
                "sh_def": shrink(wsum(df, "opp_gf_sh"), wsum(df, "s_pp"), 4 * P * L["pp_s"], L["sh"]),
            }
        self.teams = pd.DataFrame(rows).T
        self.team_history = tg

    def expected_goals(self, home, away, home_goalie=None, away_goalie=None):
        """Expected regulation goals by strength for each side: {'home': {...}, 'away': {...}}.

        With starting goalies, each side's rates are scaled by the opposing starter's goals-allowed
        factor over the factor of the goalies that team has used (which the team ratings already hold).
        """
        L = self.league
        avg = pd.Series(1.0, index=self.teams.columns)
        h = self.teams.loc[home] if home in self.teams.index else avg
        a = self.teams.loc[away] if away in self.teams.index else avg
        pp_h = L["pp_s_home"] * h.pp_draw * a.pp_take
        pp_a = L["pp_s_away"] * a.pp_draw * h.pp_take
        ev_s = 3600 - pp_h - pp_a - L["other_s"]
        fh = self.goalie_adjustment(away, away_goalie)   # home shooters face the away goalie
        fa = self.goalie_adjustment(home, home_goalie)
        return {
            "home": {"ev": L["ev_home"] * h.ev_att * a.ev_def * ev_s * fh,
                     "pp": L["pp"] * h.pp_att * a.pk_def * pp_h * fh,
                     "sh": L["sh"] * h.sh_att * a.sh_def * pp_a * fh},
            "away": {"ev": L["ev_away"] * a.ev_att * h.ev_def * ev_s * fa,
                     "pp": L["pp"] * a.pp_att * h.pk_def * pp_a * fa,
                     "sh": L["sh"] * a.sh_att * h.sh_def * pp_h * fa},
        }

    # ---- goalies ----
    @staticmethod
    def shot_features(shots):
        """One-hot design matrix (with intercept) for the expected-goals logistic fit."""
        cols = [pd.cut(shots.distance, DIST_BINS, labels=False).astype(str).radd("d"),
                pd.cut(shots.angle, ANGLE_BINS, labels=False).astype(str).radd("a"),
                shots.shot_type.map(SHOT_TYPES).fillna("tip"),
                shots.rebound.astype(str).radd("r"),
                shots.state.replace({"PS": "EV"})]
        X = pd.get_dummies(pd.concat(cols, axis=1, keys=["d", "a", "t", "r", "s"]), drop_first=True)
        return X.assign(const=1.0).astype(float)

    def _fit_goalies(self, iters=8):
        shots = self.db.shots[self.db.shots.date < self.as_of]
        X = self.shot_features(shots)
        y = shots.goal.to_numpy(float)
        A = X.to_numpy()
        beta = np.zeros(A.shape[1])
        for _ in range(iters):   # Newton / IRLS, with a little ridge so rare cells stay finite
            p = 1 / (1 + np.exp(-A @ beta))
            H = (A * (p * (1 - p))[:, None]).T @ A + np.eye(len(beta))
            beta += np.linalg.solve(H, A.T @ (y - p) - beta)
        self.xg_columns, self.xg_beta = list(X.columns), beta
        xg = 1 / (1 + np.exp(-A @ beta))

        w = decay(shots.date, self.as_of)
        df = pd.DataFrame({"goalie_id": shots.goalie_id.to_numpy(), "team": shots.opponent.to_numpy(),
                           "g": y * w, "xg": xg * w})
        by_goalie = df.groupby("goalie_id")[["g", "xg"]].sum()
        K = GOALIE_PRIOR_XG
        self.goalies = (by_goalie.g + K) / (by_goalie.xg + K)   # < 1 is better than average
        df["factor"] = df.goalie_id.map(self.goalies)
        # the goalie quality already inside each team's defensive ratings, weighted like those ratings
        self.team_goalie = (df.factor * df.xg).groupby(df.team).sum() / df.groupby("team").xg.sum()

    def goalie_factor(self, goalie):
        return self.goalies.get(goalie, 1.0) if goalie else 1.0

    def goalie_adjustment(self, team, goalie):
        """Multiplier on the shooting side's rates for `team` starting `goalie` (1.0 if unknown)."""
        if not goalie:
            return 1.0
        return self.goalie_factor(goalie) / self.team_goalie.get(team, 1.0)

    # ---- players ----
    def _fit_players(self):
        pg = self.db.player_games[self.db.player_games.date < self.as_of].copy()
        pg["w"] = decay(pg.date, self.as_of)
        cols = (["a1", "a2", "toi"] + [f"{s}_{k}" for s in SIM_STATES for k in ("g", "a")]
                + [f"gf_{s}" for s in SIM_STATES] + [f"toi_{s}" for s in SIM_STATES])
        weighted = pg[cols].mul(pg.w, axis=0)
        weighted["player_id"], weighted["pos"] = pg.player_id, pg.pos
        sums = weighted.groupby("player_id")[cols].sum()
        pos = pg.groupby("player_id").pos.last()
        games = pg.groupby("player_id").w.sum()

        prior, out = {}, pd.DataFrame(index=sums.index)
        out["pos"] = pos
        out["toi_pg"] = sums.toi / games
        for p in ("F", "D"):
            s = sums[pos == p]
            prior[p] = {"a1frac": s.a1.sum() / (s.a1.sum() + s.a2.sum())}
            for st in SIM_STATES:
                prior[p][f"g_{st}"] = s[f"{st}_g"].sum() / s[f"gf_{st}"].sum()
                prior[p][f"a_{st}"] = s[f"{st}_a"].sum() / s[f"gf_{st}"].sum()
                prior[p][f"p60_{st}"] = (s[f"{st}_g"].sum() + s[f"{st}_a"].sum()) / s[f"toi_{st}"].sum()
        self.position_prior = prior

        pf = pos.map(lambda p: prior[p]["a1frac"])
        a1frac = (sums.a1 + A1_PRIOR * pf) / (sums.a1 + sums.a2 + A1_PRIOR)
        for st in SIM_STATES:
            k = SHARE_PRIOR[st]
            gp = pos.map(lambda p: prior[p][f"g_{st}"])
            ap = pos.map(lambda p: prior[p][f"a_{st}"])
            a = (sums[f"{st}_a"] + k * ap) / (sums[f"gf_{st}"] + k)
            out[f"g_{st}"] = (sums[f"{st}_g"] + k * gp) / (sums[f"gf_{st}"] + k)
            out[f"a1_{st}"] = a * a1frac
            out[f"a2_{st}"] = a * (1 - a1frac)
            pp_rate = pos.map(lambda p: prior[p][f"p60_{st}"])
            K = POINTS_PRIOR_S[st]
            out[f"p60_{st}"] = (sums[f"{st}_g"] + sums[f"{st}_a"] + K * pp_rate) / (sums[f"toi_{st}"] + K)
            rate = sums[f"gf_{st}"].sum() / sums[f"toi_{st}"].sum()
            out[f"rel_{st}"] = shrink(sums[f"gf_{st}"], sums[f"toi_{st}"], REL_PRIOR_S[st], rate)
        self.players = out

    def player(self, pid, pos, replacement=1.0):
        """Rates for a player, falling back to the position prior (scaled by `replacement`) and a
        fringe player's on-ice rate: someone we have no data on is almost always a call-up."""
        if pid in self.players.index:
            return self.players.loc[pid]
        pr = self.position_prior[pos]
        row = {"pos": pos, "toi_pg": 0.0}
        for st in SIM_STATES:
            row[f"g_{st}"] = pr[f"g_{st}"] * replacement
            row[f"a1_{st}"] = pr[f"a_{st}"] * pr["a1frac"] * replacement
            row[f"a2_{st}"] = pr[f"a_{st}"] * (1 - pr["a1frac"]) * replacement
            row[f"rel_{st}"] = REPLACEMENT_REL
        return pd.Series(row)

    def _recent_unit_games(self, team):
        """(the team's unit rows from its last UNIT_GAMES games, {game_id: recency weight})."""
        games = self.team_history[self.team_history.team == team].sort_values("date").game_id.tail(UNIT_GAMES)
        weight = {g: UNIT_DECAY ** i for i, g in enumerate(reversed(games.tolist()))}
        u = self.db.units.get(team)
        return (None, weight) if u is None else (u[u.game_id.isin(weight)], weight)

    def lineup_quality(self, units, state):
        """How much scoring the groups at one strength carry: every skater's own points rate (shrunk to
        his position), averaged over the seconds he's in those groups. Replacement placeholders and
        players we have no data on get REPLACEMENT_POINTS of their position's rate."""
        u = units[units.state == state]
        secs = {}
        for m, sec in zip(u.members, u.seconds):
            for p in m:
                secs[p] = secs.get(p, 0.0) + sec
        if not secs or sum(secs.values()) <= 0:
            return 1.0
        rate = self.players[f"p60_{state}"]

        def points(p):
            if p in rate.index:
                return rate[p]
            pos = ("F" if -p % 2 else "D") if p < 0 else "F"   # placeholders: odd ids are forwards
            return REPLACEMENT_POINTS * self.position_prior[pos][f"p60_{state}"]
        return sum(sec * points(p) for p, sec in secs.items()) / sum(secs.values())

    def recent_quality(self, team, state):
        """lineup_quality of the groups the team actually used in its last UNIT_GAMES games."""
        key = (team, state)
        if key not in self._quality:
            u, weight = self._recent_unit_games(team)
            if u is None or u.empty:
                self._quality[key] = 1.0
            else:
                u = u.assign(seconds=u.seconds * u.game_id.map(weight))
                self._quality[key] = self.lineup_quality(u, state)
        return self._quality[key]

    def recent_units(self, team, lineup, placeholder):
        """On-ice groups from the team's last UNIT_GAMES games, recast onto tonight's lineup.

        In each past game, skaters not dressed tonight hand their slots to tonight's skaters who
        didn't play that game, same position, biggest role first (so a regular back from injury
        takes the top vacated spot). Slots nobody can fill go to the replacement `placeholder`.
        Returns (groups with recency-weighted seconds, {absent player: {filled by: games}}).
        """
        u, weight = self._recent_unit_games(team)
        if u is None:
            return pd.DataFrame(columns=["state", "group", "members", "seconds"]), {}
        role = lambda p: self.players.toi_pg.get(p, 0.0)  # noqa: E731
        pos_of = lambda p: self.players.pos.get(p, "F")  # noqa: E731
        swaps, rows = {}, []
        for gid, ug in u.groupby("game_id"):
            secs = {}
            for m, sec in zip(ug.members, ug.seconds):
                for p in m:
                    secs[p] = secs.get(p, 0) + sec
            swap = {}
            for pos in ("F", "D"):
                gone = sorted((p for p in secs if p not in lineup and pos_of(p) == pos), key=lambda p: -secs[p])
                free = sorted((p for p, q in lineup.items() if q == pos and p not in secs), key=lambda p: -role(p))
                for i, p in enumerate(gone):
                    swap[p] = free[i] if i < len(free) else placeholder[pos]
                    swaps.setdefault(p, {}).setdefault(swap[p], 0)
                    swaps[p][swap[p]] += 1
            for st, grp, m, sec in zip(ug.state, ug.group, ug.members, ug.seconds):
                rows.append((st, grp, tuple(sorted(swap.get(p, p) for p in m)), sec * weight[gid]))
        out = pd.DataFrame(rows, columns=["state", "group", "members", "seconds"])
        return out.groupby(["state", "group", "members"], as_index=False).seconds.sum(), swaps

    # ---- game script: empty-net / extra-attacker goals, overtime ----
    def _fit_script(self):
        g = self.db.goals[self.db.goals.date < self.as_of]
        self.p_a1, self.p_a2 = {}, {}
        groups = {**{st: g.bucket == st for st in SIM_STATES}, "en": g.state == "EN", "ea": g.state.isin(["EA", "PS"])}
        for st, mask in groups.items():
            s = g[mask]
            self.p_a1[st] = s.a1s.mean()
            self.p_a2[st] = s.a2s.sum() / max(s.a1s.sum(), 1)

        sc = self.db.scripts[self.db.scripts.date < self.as_of]
        margin = sc.h_base - sc.a_base
        lead_other = np.where(margin > 0, sc.h_other, sc.a_other)
        trail_other = np.where(margin > 0, sc.a_other, sc.h_other)
        b = np.minimum(margin.abs(), 4).to_numpy()
        self.other_dist = {}
        for m in range(1, 5):
            self.other_dist[m] = np.stack([lead_other[b == m], trail_other[b == m]], 1).astype(int)
        tied = np.stack([sc.h_other[b == 0], sc.a_other[b == 0]], 1).astype(int)
        self.other_dist[0] = np.concatenate([tied, tied[:, ::-1]])

        tg = self.team_history[self.team_history.home == 1]
        tied = tg[tg.reg_gf == tg.reg_ga]
        self.p_ot_goal = ((tied.ot == 1) & (tied.so == 0)).mean()

# NHL Power-Play Combinations

Which players combine most on 2026–27 NHL power-play goals: scoresheet pairs, trios and setups, plus on-ice pairs and units with PP time together and goals/60. Built to the brief in [nhl-pp-combos-spec.md](nhl-pp-combos-spec.md).

A GitHub Action refreshes the data every morning and redeploys the dashboard to GitHub Pages.

## Run locally

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python pipeline/run.py
python3 -m http.server 8765 --directory site
```

## Pipeline

| Step | File | Output |
|---|---|---|
| Fetch + cache | `pipeline/fetch.py` | `data/raw/{gameId}_{pbp,shifts}.json`, `data/games.csv` |
| Goals, on-ice sets | `pipeline/parse.py` | `data/goals.csv` (all strengths), `data/pp_goals.csv`, `data/players.json`, `data/qa_mismatches.csv` |
| PP time on ice | `pipeline/pp_toi.py` | `data/pp_toi_units.csv` |
| Player games | `pipeline/player_games.py` | `data/player_games.csv` (one row per skater per game: goals, assists, on-ice goals for/against and TOI by strength, plus shots) and `data/team_games.csv` |
| On-ice groups | `pipeline/units.py` | `data/units.csv`: seconds together per game for forward groups and defence pairs (EV, SH) and whole PP units |
| Goalie shots | `pipeline/goalies.py` | `data/shots.csv` (every shot on goal with a goalie in net: distance, angle, type, rebound, strength) and `data/goalie_games.csv` (shots and goals against per goalie per game, and who started) |
| Combinations | `pipeline/aggregate.py` | `data/scoresheet_*.csv`, `data/setup_connections.csv`, `data/onice_*.csv`, `data/player_pp_toi.csv` |
| Dashboard data | `pipeline/build_data.py` | `site/data.json` |

Earlier seasons (`HISTORY_SEASONS` in `pipeline/config.py`) are built once into `data/history/{season}/` with `python pipeline/backfill.py`; the nightly run only updates the current season.

Checks: `pipeline/check_player_games.py [season]` compares season totals per skater with the NHL stats API; `pipeline/check_scoresheet.py` compares PP goals with the NHL scoring summary; `pipeline/check_toi.py` compares player PP TOI with the NHL stats API.

## Same-game parlay model (`model/`)

A goal-by-goal Monte Carlo of a game that gives the joint distribution of every skater's goals, assists and points, so correlated legs are priced together rather than multiplied.

```bash
.venv/bin/python model/price.py CGY EDM                        # every skater's props + most correlated pairs
.venv/bin/python model/price.py CGY EDM --leg "McDavid point" --leg "Draisaitl point" --leg "EDM win" \
    --home-xg 3.5 --away-xg 2.8 --book +250                    # one parlay, scaled to market team totals
.venv/bin/python model/backtest.py --season 20252026           # out-of-sample check
.venv/bin/python model/check_lineups.py                        # saved lineup projections against who played
```

Lineups come from [Daily Faceoff](https://www.dailyfaceoff.com): each team's line-combinations page (lines, pairs, PP and PK units, injuries) and the starting-goalies page (starter and whether it's Confirmed, Likely or Unconfirmed). If Daily Faceoff can't be read, or with `--lineups last`, the pricer uses each team's last game and its most frequent recent starter. For late news, edit `model/overrides.txt` (it applies only on the date in its first line):

```
date: 2026-11-02
EDM out: Draisaitl
EDM in: Hyman F
EDM goalie: Jarry
```

Every projection is saved to `data/lineups/{date}/{TEAM}.json`; `check_lineups.py` compares them with who actually dressed and started, to measure how good the projections are over time.

| File | What it does |
|---|---|
| `model/data.py` | Loads every season's `team_games`, `player_games`, `units`, `goals`, `shots` and `goalie_games` |
| `model/rates.py` | Team rates by strength (EV, PP time drawn/conceded, PP/PK, SH), player shares of on-ice goals, and goalie ratings (goals allowed against expected goals from shot distance, angle, type, rebound and strength), recency-weighted and shrunk to league / position averages |
| `model/lineups.py` | Tonight's lineups, lines and starting goalies from Daily Faceoff, our own data and `overrides.txt` |
| `model/sim.py` | Simulates regulation EV/PP/SH goals, empty-net and extra-attacker goals by score margin, overtime and shootouts; each goal draws an on-ice group from the team's recent groups, then a scorer and assists |
| `model/price.py` | Prices player, team-win and game-total legs and their parlay |
| `model/backtest.py` | Prices each game of a season with only earlier data and scores the results |
| `model/check_lineups.py` | Scores saved lineup projections against the real lineups |

**Backtest, 2025–26** (every game, tuned on 2024–25 only):

Lineups and starting goalies in the backtest are the real ones, so it tests the ratings and correlation, not lineup guessing.

- **Single props:** both beat a recency-weighted points-per-game baseline on log loss.
  - Point props: 0.5953 against 0.5971 for the baseline.
  - Goal props: 0.3934 against 0.3962.
- **Team goals:** 6.12 a game predicted against 6.16 actual.
- **Starting goalie:** adjusting for the starter improves every measure slightly over leaving goalies out: team-goal Poisson log likelihood −1.9249 against −1.9263, point props 0.5954 against 0.5956 (before the lineup-strength adjustment), pairs 0.37152 against 0.37169. Starters are usually better than their backups, so the adjustment pulls predicted goals about 0.03 a game below actual.
- **Lineup strength:** team expected goals are scaled by tonight's lineup against the team's last 10 games. The measure is each skater's own points rate by strength, weighted by projected ice time, with a replacement-level player for anyone missing. So a team missing a star scores less, and his linemates lose the goals he would have helped create rather than inheriting his share. Fitted on 2024–25 (proportional scaling was best), it improves 2025–26 slightly: team-goal log likelihood −1.9245 against −1.9249, pairs 0.37143 against 0.37153. The average gain is small because most nights' lineups match recent games; it matters on nights with big absences.
- **Teammate "both get a point":** 0.3715 with the model's joint probabilities against 0.3732 with the same marginals treated as independent. The model ranks correlation well but overstates it: realised lift over independence is about 80% of predicted. `price.py` therefore also reports a calibrated parlay probability that keeps 80% of the lift, and quotes expected value on that figure.

**Known limits:**

- On-ice groups are half projected lines (split by typical ice time per line) and half the team's last 10 games recast onto tonight's lineup. That 50/50 split isn't fitted yet: there is no archive of past projected lines to fit it on, so it waits on the saved projections.
- Without `--home-xg` / `--away-xg`, team strength comes from the model's own ratings rather than the market.
- Overtime goals use even-strength groups, though 3-on-3 leans more heavily on top players.

## Definitions

- **PP goal:** the scoring team had more skaters and both goalies were in net. Set `INCLUDE_EXTRA_ATTACKER` in `pipeline/config.py` to count pulled-goalie situations.
- **Strength states** (from the team's side, in `goals.csv` and `player_games.csv`): `ev`, `pp` and `sh` need both goalies in net; `other` covers empty-net goals either way, extra-attacker goals and penalty shots. Shootout goals are left out everywhere.
- **On ice:** a skater counts for a goal if `start < goal_time <= end` (left on the goal counts, jumped on at that second doesn't).
- **Shifts:** from the NHL shift-chart API, falling back to the HTML TOI reports when the API has no rows for a game.

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
```

| File | What it does |
|---|---|
| `model/data.py` | Loads every season's `team_games`, `player_games`, `units` and `goals` |
| `model/rates.py` | Team rates by strength (EV, PP time drawn/conceded, PP/PK, SH) and player shares of on-ice goals, recency-weighted and shrunk to league / position averages |
| `model/sim.py` | Simulates regulation EV/PP/SH goals, empty-net and extra-attacker goals by score margin, overtime and shootouts; each goal draws an on-ice group from the team's recent groups, then a scorer and assists |
| `model/price.py` | Prices player, team-win and game-total legs and their parlay |
| `model/backtest.py` | Prices each game of a season with only earlier data and scores the results |

**Backtest, 2025–26** (every game, tuned on 2024–25 only):

- **Single props:** both beat a recency-weighted points-per-game baseline on log loss.
  - Point props: 0.5956 against 0.5971 for the baseline.
  - Goal props: 0.3936 against 0.3962.
- **Team goals:** 6.15 a game predicted against 6.16 actual.
- **Teammate "both get a point":** 0.3717 with the model's joint probabilities against 0.3733 with the same marginals treated as independent. The model ranks correlation well but overstates it: realised lift over independence is about 80% of predicted. `price.py` therefore also reports a calibrated parlay probability that keeps 80% of the lift, and quotes expected value on that figure.

**Known limits:**

- Each team's lineup is the skaters who played its last game, and on-ice groups come from its last 10 games. Real projected lines and goalies come in a later step.
- Without `--home-xg` / `--away-xg`, team strength comes from the model's own ratings rather than the market.
- Overtime goals use even-strength groups, though 3-on-3 leans more heavily on top players.

## Definitions

- **PP goal:** the scoring team had more skaters and both goalies were in net. Set `INCLUDE_EXTRA_ATTACKER` in `pipeline/config.py` to count pulled-goalie situations.
- **Strength states** (from the team's side, in `goals.csv` and `player_games.csv`): `ev`, `pp` and `sh` need both goalies in net; `other` covers empty-net goals either way, extra-attacker goals and penalty shots. Shootout goals are left out everywhere.
- **On ice:** a skater counts for a goal if `start < goal_time <= end` (left on the goal counts, jumped on at that second doesn't).
- **Shifts:** from the NHL shift-chart API, falling back to the HTML TOI reports when the API has no rows for a game.

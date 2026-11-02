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
| Player games | `pipeline/player_games.py` | `data/player_games.csv`: one row per skater per game, with goals, assists, on-ice goals for/against and TOI split by strength, plus shots |
| Combinations | `pipeline/aggregate.py` | `data/scoresheet_*.csv`, `data/setup_connections.csv`, `data/onice_*.csv`, `data/player_pp_toi.csv` |
| Dashboard data | `pipeline/build_data.py` | `site/data.json` |

Earlier seasons (`HISTORY_SEASONS` in `pipeline/config.py`) are built once into `data/history/{season}/` with `python pipeline/backfill.py`; the nightly run only updates the current season.

Checks: `pipeline/check_player_games.py [season]` compares season totals per skater with the NHL stats API; `pipeline/check_scoresheet.py` compares PP goals with the NHL scoring summary; `pipeline/check_toi.py` compares player PP TOI with the NHL stats API.

## Definitions

- **PP goal:** the scoring team had more skaters and both goalies were in net. Set `INCLUDE_EXTRA_ATTACKER` in `pipeline/config.py` to count pulled-goalie situations.
- **Strength states** (from the team's side, in `goals.csv` and `player_games.csv`): `ev`, `pp` and `sh` need both goalies in net; `other` covers empty-net goals either way, extra-attacker goals and penalty shots. Shootout goals are left out everywhere.
- **On ice:** a skater counts for a goal if `start < goal_time <= end` (left on the goal counts, jumped on at that second doesn't).
- **Shifts:** from the NHL shift-chart API, falling back to the HTML TOI reports when the API has no rows for a game.

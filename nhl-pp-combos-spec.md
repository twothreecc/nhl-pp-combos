# NHL Power-Play Combinations — Build Spec

Hand this file to Claude Code as the project brief. Ask it to read it fully, then build in the phase order at the bottom, pausing after each phase for review.

## Goal

Analyze every power-play (PP) goal of the 2026–27 NHL regular season, across all 32 teams, to show which players combine most often:

1. **Scoresheet combinations:** scorer and assisters on each PP goal.
2. **On-ice units:** every skater on the ice for the scoring team when the PP goal went in, plus how much PP time each unit/pair played together.

Deliver an interactive dashboard that refreshes automatically each night.

## Data sources (verified against live data on 2026-11-02)

The NHL API is unofficial and undocumented. It needs no key but can change without notice. Community reference: https://github.com/Zmalski/NHL-API-Reference

### 1. Play-by-play
`GET https://api-web.nhle.com/v1/gamecenter/{gameId}/play-by-play`

- Game IDs for this season look like `2026020001` (season `20262027`, `gameType` 2 = regular season).
- Top-level: `id`, `season`, `gameType`, `gameDate`, `gameState` (finished games showed `"OFF"`), `awayTeam`, `homeTeam` (each with `id`, `abbrev`), `plays[]`, `rosterSpots[]`.
- Each play has `typeDescKey`, `periodDescriptor.number`, `periodDescriptor.periodType` (`REG`/`OT`), `timeInPeriod` (`"MM:SS"` elapsed), `situationCode`, and `details`.
- **Goal events:** `typeDescKey == "goal"`. Confirmed `details` fields: `scoringPlayerId`, `assist1PlayerId`, `eventOwnerTeamId` (the scoring team), `goalieInNetId`, `shotType`, `xCoord`, `yCoord`.
  - ⚠️ `assist2PlayerId` was not present in the sample goal (it only had one assist). It is very likely the field name for second assists, but **confirm on a goal that has two assists** before relying on it. Treat all assist fields as optional.
- **`rosterSpots[]`** gives names and positions: `teamId`, `playerId`, `firstName.default`, `lastName.default`, `sweaterNumber`, `positionCode` (C/L/R/D/G), `headshot` URL.

### 2. `situationCode` (strength state) — confirmed
Four digits: **[away goalie][away skaters][home skaters][home goalie]**

- `1551` = 5v5, both goalies in.
- `1541` = away 5, home 4 (away on the PP). Confirmed: appears right after a home-team minor penalty.
- `1451` = home on the PP. `1431` = home 5v3. `1331` = 3v3 OT. `0440` appeared at game end — ignore.

**PP goal rule:**
- Parse the four digits. Work out which side the scoring team (`eventOwnerTeamId`) is on by comparing it with `homeTeam.id` / `awayTeam.id`.
- It is a PP goal when the scoring team's skaters > opponent's skaters **and both goalie digits are 1**.
- Requiring both goalies in excludes empty-net situations and pulled-goalie extra-attacker goals. Keep that as a config flag (`include_extra_attacker = False`) so it can be changed later.
- Read `situationCode` from the goal event itself.
- Tag each goal with strength (`5v4`, `5v3`, `4v3`) so the dashboard can filter.

### 3. Shift charts (needed for on-ice units) — confirmed
`GET https://api.nhle.com/stats/rest/en/shiftcharts?cayenneExp=gameId={gameId}`

- Returns `{"data": [...]}`. Confirmed fields per row: `playerId`, `teamId`, `teamAbbrev`, `firstName`, `lastName`, `period`, `startTime`, `endTime`, `duration` (all `"MM:SS"`), `shiftNumber`, `typeCode`, `detailCode`.
- Regular shift rows had `typeCode: 517`. Some rows may be other event types with `eventDescription` filled. **Use only `typeCode == 517`**, and check whether any other codes appear before ignoring them.
- Goalies appear as full-period shifts. Drop goalies using `positionCode == "G"` from `rosterSpots`.

### 4. Schedule (list of game IDs)
Candidate: `GET https://api-web.nhle.com/v1/club-schedule-season/{TEAM_ABBREV}/20262027`

- Loop all 32 teams, collect `gameType == 2` IDs, and dedupe.
- ⚠️ Not verified in this session. Confirm the response shape before building on it. If another schedule endpoint is simpler (the reference lists several), use it.
- Only process finished games. Check what `gameState` values mean finished (`"OFF"` was observed; `"FINAL"` may also occur).

## Processing logic

### On-ice at goal time
- Convert period + `MM:SS` to seconds elapsed in the period.
- A skater was on the ice for a goal if `start < goal_time <= end` within the same period. Players who left on the goal count; players who jumped on at that exact second do not.
- Validate: for each PP goal, the number of on-ice skaters for the scoring team should equal the skater digit from `situationCode`.
- Log any mismatches to `data/qa_mismatches.csv` instead of failing. Shift data has known timing noise.

### PP time on ice together (for rates)
- Build PP intervals per game from the sequence of `situationCode` values.
- For each interval where a team had the skater advantage (same goalie rule), record its skaters' overlapping shift time.
- Accumulate PP seconds for every pair and every full unit.
- **This is the hardest part. Build it last** and check it against Natural Stat Trick's PP TOI for a few players.
- Simplification allowed in phase 3: approximate interval boundaries from event timestamps.

### Outputs (combinations always keyed by team)
1. `pp_goals.csv`: one row per PP goal with game, date, team, period, time, strength, scorer, A1, A2, on-ice skater IDs.
2. **Scoresheet pairs (unordered):** every pair among {scorer, A1, A2}, with count.
3. **Directed connections:** A1→scorer, A2→scorer ("who sets up whom").
4. **Scoresheet trios:** full scorer+A1+A2 groups.
5. **On-ice pairs:** every pair among on-ice skaters, with PP goals together, PP TOI together, and goals per 60.
6. **On-ice units:** full sorted set of skaters, with goals, TOI, and goals/60.
7. `players.json`: id → name, position, team, headshot.

A player traded mid-season appears under each team separately. Never link players who weren't teammates at the time.

## Dashboard

A single static HTML page reading a generated `data.json`. Charting library is open (e.g., D3 or vis-network for the graph); keep everything client-side.

**Views:**
- **Leaderboards (tabs):** scoresheet pairs, trios, setup connections, on-ice pairs, on-ice units. Sortable columns. Show goals/60 next to raw counts with a minimum-TOI filter (default 20 PP minutes), so small samples don't top the list.
- **Network graph:** players as nodes, sized by PP points; edges weighted by combinations. Toggle between scoresheet and on-ice.
- **Team view:** pick a team and see its PP goal log, top unit, and graph.
- **Player view:** search a player and see their most frequent partners.
- **Filters:** team, strength (5v4 / 5v3 / all), date range, position.
- Show a "data through [date]" stamp and the number of games processed.

## Infrastructure

- Python 3, `requests`, `pandas`. Cache every raw JSON response to `data/raw/{gameId}_{pbp|shifts}.json` and never re-fetch a finished game.
- Wait about 0.5–1 second between requests, and retry with backoff on errors.
- **Nightly refresh:** a GitHub Actions workflow (GitHub account: twothreecc) runs the pipeline around 6 a.m. Eastern, commits the updated `data.json`, and serves the dashboard on GitHub Pages.
- Repo layout: `pipeline/` (fetch, parse, aggregate), `site/` (dashboard), `data/`, `.github/workflows/`.

## Build phases (pause after each for review)

1. **Fetch + cache:** schedule, play-by-play, and shifts for all finished games so far. Print counts.
2. **Scoresheet analysis:** PP goal extraction and outputs 1–4 and 7. Spot-check ~5 PP goals against NHL.com game recaps.
3. **On-ice analysis:** on-ice-at-goal sets, validation log, and outputs 5–6 with counts only.
4. **PP TOI and rates:** interval logic, TOI together, goals/60. Cross-check a few players against Natural Stat Trick.
5. **Dashboard:** built on the generated `data.json`.
6. **Automation:** GitHub Action and GitHub Pages deployment.

## Open checks for Claude Code to confirm early
- `assist2PlayerId` field name on a two-assist goal.
- Schedule endpoint response shape.
- Any shift `typeCode` other than 517.
- `gameState` values for finished games.
- Whether penalty-shot goals appear with an unusual `situationCode` (exclude them).

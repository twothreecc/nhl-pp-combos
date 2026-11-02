"""Build data/history/{season}/ for the finished seasons in config.HISTORY_SEASONS.

Runs fetch -> parse -> pp_toi -> player_games for each one. Raw responses are cached in data/raw,
so a re-run only fetches what's missing. These seasons don't change, so this is run by hand,
not nightly.
Run from the repo root:  python pipeline/backfill.py [season ...]
"""
import sys
import time

import fetch
import parse
import player_games
import pp_toi
from config import HISTORY_SEASONS


def main(seasons):
    for season in seasons:
        for name, step in (("fetch", fetch), ("parse", parse), ("pp_toi", pp_toi), ("player_games", player_games)):
            t = time.time()
            print(f"\n== {season} {name} ==", flush=True)
            step.main(season)
            print(f"({time.time() - t:.0f}s)", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:] or HISTORY_SEASONS)

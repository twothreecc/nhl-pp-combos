"""Thin client for the (unofficial) NHL APIs, with pacing and retry."""
import time

import requests

from config import MAX_RETRIES, REQUEST_DELAY, SEASON

WEB = "https://api-web.nhle.com/v1"
STATS = "https://api.nhle.com/stats/rest/en"

_session = requests.Session()
_session.headers["User-Agent"] = "pp-combos/0.1"
_last_request = 0.0


def get_json(url):
    return _get(url).json()


def get_text(url):
    r = _get(url)
    r.encoding = r.encoding or "latin-1"
    return r.text


def _get(url):
    global _last_request
    for attempt in range(MAX_RETRIES):
        wait = REQUEST_DELAY - (time.time() - _last_request)
        if wait > 0:
            time.sleep(wait)
        _last_request = time.time()
        try:
            r = _session.get(url, timeout=30)
            if r.status_code == 200:
                return r
            if r.status_code not in (429, 500, 502, 503, 504):
                r.raise_for_status()
            reason = f"HTTP {r.status_code}"
        except (requests.ConnectionError, requests.Timeout) as e:
            reason = type(e).__name__
        backoff = 2 ** attempt * 2
        print(f"  retry {attempt + 1}/{MAX_RETRIES} in {backoff}s ({reason}): {url}")
        time.sleep(backoff)
    raise RuntimeError(f"Giving up after {MAX_RETRIES} attempts: {url}")


def team_schedule(abbrev):
    return get_json(f"{WEB}/club-schedule-season/{abbrev}/{SEASON}")


def play_by_play(game_id):
    return get_json(f"{WEB}/gamecenter/{game_id}/play-by-play")


def shift_chart(game_id):
    return get_json(f"{STATS}/shiftcharts?cayenneExp=gameId={game_id}")


def toi_report(game_id, side):
    """HTML TOI shift report; side is 'H' (home) or 'V' (visitor)."""
    gid = str(game_id)
    return get_text(f"https://www.nhl.com/scores/htmlreports/{gid[:4]}{int(gid[:4]) + 1}/T{side}{gid[4:]}.HTM")

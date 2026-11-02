"""Shared settings for the PP combinations pipeline."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"

SEASON = "20262027"
# Earlier seasons for the betting model. They're finished, so pipeline/backfill.py builds them once
# into data/history/{season}/ and the nightly run only touches SEASON.
HISTORY_SEASONS = ["20232024", "20242025", "20252026"]
REGULAR_SEASON = 2
FINISHED_STATES = {"OFF", "FINAL"}

TEAMS = [
    "ANA", "BOS", "BUF", "CAR", "CBJ", "CGY", "CHI", "COL", "DAL", "DET", "EDM",
    "FLA", "LAK", "MIN", "MTL", "NJD", "NSH", "NYI", "NYR", "OTT", "PHI", "PIT",
    "SEA", "SJS", "STL", "TBL", "TOR", "UTA", "VAN", "VGK", "WPG", "WSH",
]



def teams(season):
    """Team abbreviations for a season: Arizona became Utah in 2024-25."""
    return [("ARI" if t == "UTA" else t) for t in TEAMS] if season < "20242025" else TEAMS


def out_dir(season):
    """Where a season's derived files live: data/ for the current season, data/history/{season}/ otherwise."""
    return DATA_DIR if season == SEASON else DATA_DIR / "history" / season


# Request pacing / retry
REQUEST_DELAY = 0.6  # seconds between requests
MAX_RETRIES = 5

# PP goal rule: when False, both goalies must be in net (excludes extra-attacker / empty-net goals)
INCLUDE_EXTRA_ATTACKER = False

PERIOD_SECONDS = 20 * 60

# Shift rows with this typeCode are real shifts; 505 rows are goal markers
SHIFT_TYPE_CODE = 517

"""Shared settings for the PP combinations pipeline."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"

SEASON = "20262027"
REGULAR_SEASON = 2
FINISHED_STATES = {"OFF", "FINAL"}

TEAMS = [
    "ANA", "BOS", "BUF", "CAR", "CBJ", "CGY", "CHI", "COL", "DAL", "DET", "EDM",
    "FLA", "LAK", "MIN", "MTL", "NJD", "NSH", "NYI", "NYR", "OTT", "PHI", "PIT",
    "SEA", "SJS", "STL", "TBL", "TOR", "UTA", "VAN", "VGK", "WPG", "WSH",
]

# Request pacing / retry
REQUEST_DELAY = 0.6  # seconds between requests
MAX_RETRIES = 5

# PP goal rule: when False, both goalies must be in net (excludes extra-attacker / empty-net goals)
INCLUDE_EXTRA_ATTACKER = False

PERIOD_SECONDS = 20 * 60

# Shift rows with this typeCode are real shifts; 505 rows are goal markers
SHIFT_TYPE_CODE = 517

"""Tonight's projected lineups and starting goalies.

Sources, in order (each later one wins):
1. Our own data: the skaters from the team's most recent game, and the goalie who started most of
   its last GOALIE_GAMES games. Used when Daily Faceoff can't be read.
2. Daily Faceoff: the team's line-combinations page (forward lines, defence pairs, PP and PK units,
   injuries) and the starting-goalies page (starter plus Confirmed / Likely / Unconfirmed). Both
   are read from the page's embedded data, not their /api/ (which robots.txt disallows).
3. model/overrides.txt, edited by hand for late news (format below).

Daily Faceoff names are matched to NHL ids through the team's current NHL roster, then through
every player we've seen.

Each projection is saved to data/lineups/{date}/{TEAM}.json, so check_lineups.py can later
compare it with who actually dressed.

overrides.txt: the first line is "date: YYYY-MM-DD" and the file is ignored on any other day. Then
one change per line, names matched like the pricer's legs (any part of the name):
    EDM out: Draisaitl, Ekholm
    EDM in: Hyman F, Regula D
    EDM goalie: Jarry
"""
import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
OVERRIDES = Path(__file__).resolve().parent / "overrides.txt"
SNAPSHOTS = ROOT / "data" / "lineups"
DF = "https://www.dailyfaceoff.com"
NHL = "https://api-web.nhle.com/v1"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
GOALIE_GAMES = 10
# Daily Faceoff's abbreviations that differ from the NHL's
DF_ABBREV = {"TB": "TBL", "NJ": "NJD", "LA": "LAK", "SJ": "SJS", "VEG": "VGK", "WAS": "WSH", "MON": "MTL",
             "CLB": "CBJ", "UTAH": "UTA"}

_session = requests.Session()
_session.headers["User-Agent"] = UA
_cache = {}


@dataclass
class Lineup:
    team: str
    skaters: list                  # [(player_id, 'F'|'D'), ...]
    goalie: int = None
    goalie_status: str = "unknown"
    lines: dict = field(default_factory=dict)   # 'ev_F' / 'ev_D' / 'pp_PP' / 'sh_F' / 'sh_D' -> [[ids], ...] top first
    source: str = "last game"
    updated: str = ""
    notes: list = field(default_factory=list)


def norm(name):
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z ]", "", s.replace("-", " ")).strip()


def _get(url):
    if url not in _cache:
        r = _session.get(url, timeout=30)
        r.raise_for_status()
        _cache[url] = r
    return _cache[url]


def _next_data(url):
    html = _get(url).text
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        raise ValueError(f"no page data at {url}")
    return json.loads(m.group(1))["props"]["pageProps"]


# ---- our own data ----
def from_history(db, team, before):
    pg = db.player_games
    games = pg[(pg.team == team) & (pg.date < before)]
    if games.empty:
        raise ValueError(f"No games for {team} before {before.date()}")
    last = games[games.game_id == games.game_id.max()]
    gg = db.goalie_games
    recent = gg[(gg.team == team) & (gg.date < before) & (gg.started == 1)].sort_values("date").tail(GOALIE_GAMES)
    goalie = int(recent.goalie_id.mode().iloc[0]) if len(recent) else None
    return Lineup(team, list(zip(last.player_id, last.pos)), goalie, "guess (most starts, last 10)",
                  source=f"last game ({last.date.iloc[0].date()})")


# ---- Daily Faceoff ----
class Matcher:
    """Daily Faceoff name -> NHL id, via the team's roster and then every player we've seen."""

    def __init__(self, db, team):
        self.roster = {}
        try:
            r = _get(f"{NHL}/roster/{team}/current").json()
            for group in ("forwards", "defensemen", "goalies"):
                for p in r[group]:
                    self.roster[norm(f"{p['firstName']['default']} {p['lastName']['default']}")] = p["id"]
        except (requests.RequestException, KeyError, ValueError):
            pass
        self.known = {}
        for pid, p in db.players.items():
            self.known.setdefault(norm(p.get("name", "")), pid)

    def __call__(self, name):
        n = norm(name)
        if n in self.roster:
            return self.roster[n]
        last = n.split()[-1] if n else ""
        hits = [pid for k, pid in self.roster.items() if k.split()[-1] == last]
        if len(hits) == 1:
            return hits[0]
        return self.known.get(n)


def df_slug(team):
    """Daily Faceoff's slug for a team, from tonight's goalie page or any team page."""
    for g in df_goalies_raw():
        for side in ("home", "away"):
            if DF_ABBREV.get(g[f"{side}TeamAbbreviation"], g[f"{side}TeamAbbreviation"]) == team:
                return g[f"{side}TeamSlug"]
    teams = _next_data(f"{DF}/teams/boston-bruins/line-combinations")["sortedTeams"]
    for t in teams:
        if DF_ABBREV.get(t["shortName"], t["shortName"]) == team:
            return t["slug"]
    raise ValueError(f"no Daily Faceoff team for {team}")


def df_goalies_raw(day=None):
    url = f"{DF}/starting-goalies/" + (f"{day}/" if day else "")
    return _next_data(url)["data"]


def df_starter(team, day):
    for g in df_goalies_raw(day):
        for side in ("home", "away"):
            if DF_ABBREV.get(g[f"{side}TeamAbbreviation"], g[f"{side}TeamAbbreviation"]) == team:
                return g[f"{side}GoalieName"], g.get(f"{side}NewsStrengthName") or "Unconfirmed"
    return None, None


def from_dailyfaceoff(db, team, day):
    combos = _next_data(f"{DF}/teams/{df_slug(team)}/line-combinations")["combinations"]
    match = Matcher(db, team)
    lu = Lineup(team, [], source=f"Daily Faceoff ({combos.get('sourceName', '').strip() or 'lines'})",
                updated=combos.get("updatedAt", ""))
    groups, unmatched = {}, []
    for p in combos["players"]:
        pid = match(p["name"])
        if pid is None:
            unmatched.append(p["name"])
            continue
        if p.get("gameTimeDecision") and p["categoryIdentifier"] == "ev":
            lu.notes.append(f"{p['name']} is a game-time decision")
        groups.setdefault((p["categoryIdentifier"], p["groupIdentifier"]), []).append((pid, p["positionIdentifier"]))
    if unmatched:
        lu.notes.append("couldn't match to NHL ids: " + ", ".join(unmatched))

    def ordered(cat, prefix):
        keys = sorted(k for k in groups if k[0] == cat and re.fullmatch(prefix + r"\d", k[1]))
        return [groups[k] for k in keys]

    fwd, dee = ordered("ev", "f"), ordered("ev", "d")
    lu.skaters = [(pid, "F") for line in fwd for pid, _ in line] + [(pid, "D") for pair in dee for pid, _ in pair]
    is_d = {pid for pid, pos in lu.skaters if pos == "D"}
    lu.lines = {"ev_F": [[pid for pid, _ in line] for line in fwd],
                "ev_D": [[pid for pid, _ in pair] for pair in dee],
                "pp_PP": [[pid for pid, _ in unit] for unit in ordered("pp", "pp")],
                "sh_F": [[pid for pid, _ in unit if pid not in is_d] for unit in ordered("pk", "pk")],
                "sh_D": [[pid for pid, _ in unit if pid in is_d] for unit in ordered("pk", "pk")]}

    name, status = df_starter(team, day)
    if name:
        lu.goalie, lu.goalie_status = match(name), status
        if lu.goalie is None:
            lu.notes.append(f"couldn't match starting goalie {name}")
    else:
        g1 = groups.get(("ev", "g1"))
        lu.goalie, lu.goalie_status = (g1[0][0], "Daily Faceoff depth chart G1") if g1 else (None, "unknown")
    return lu


# ---- overrides ----
def read_overrides(day):
    """{team: [(verb, [names])]} for `day`, or {} if the file is missing or for another day."""
    if not OVERRIDES.exists():
        return {}
    lines = [ln.split("#")[0].strip() for ln in OVERRIDES.read_text().splitlines()]
    lines = [ln for ln in lines if ln]
    if not lines or not lines[0].lower().startswith("date:"):
        print(f"  {OVERRIDES.name}: first line must be 'date: YYYY-MM-DD'; ignoring it")
        return {}
    if lines[0].split(":", 1)[1].strip() != str(day):
        return {}
    out = {}
    for ln in lines[1:]:
        m = re.match(r"^([A-Za-z]{3})\s+(out|in|goalie)\s*:\s*(.+)$", ln, re.I)
        if not m:
            print(f"  {OVERRIDES.name}: can't read {ln!r}")
            continue
        out.setdefault(m.group(1).upper(), []).append((m.group(2).lower(), [x.strip() for x in m.group(3).split(",")]))
    return out


def find_player(db, text, team=None, before=None):
    """A player id from any part of their name, preferring players who've played for `team`."""
    t = text.lower()
    hits = [pid for pid, p in db.players.items() if t in p.get("name", "").lower()]
    if team is not None and len(hits) > 1:
        pg = db.player_games
        on_team = set(pg[(pg.team == team) & (pg.date < before)].player_id) | set(
            db.goalie_games[db.goalie_games.team == team].goalie_id)
        hits = [h for h in hits if h in on_team] or hits
    if len(hits) != 1:
        raise ValueError(f"{text!r} matches {len(hits)} players")
    return hits[0]


def apply_overrides(db, lu, changes, before):
    for verb, names in changes:
        for name in names:
            if verb == "goalie":
                lu.goalie, lu.goalie_status = find_player(db, name, lu.team, before), "override"
                continue
            pos = None
            m = re.match(r"^(.*?)\s+([FD])$", name, re.I)
            if m:
                name, pos = m.group(1), m.group(2).upper()
            pid = find_player(db, name, lu.team, before)
            if verb == "out":
                lu.skaters = [(p, q) for p, q in lu.skaters if p != pid]
                lu.lines = {k: [[p for p in grp if p != pid] for grp in v] for k, v in lu.lines.items()}
            elif pid not in dict(lu.skaters):
                pos = pos or ("D" if db.players.get(pid, {}).get("position") == "D" else "F")
                lu.skaters.append((pid, pos))
        lu.notes.append(f"override: {verb} {', '.join(names)}")
    lu.source += " + overrides"


# ---- everything together ----
def project(db, team, day, source="dailyfaceoff", save=True):
    """Tonight's Lineup for `team` on `day` (a date)."""
    import pandas as pd
    before = pd.Timestamp(day)
    if source == "dailyfaceoff":
        try:
            lu = from_dailyfaceoff(db, team, day)
            if len([p for p, q in lu.skaters if q == "F"]) < 10 or len([p for p, q in lu.skaters if q == "D"]) < 5:
                raise ValueError(f"only {len(lu.skaters)} skaters matched")
        except (requests.RequestException, ValueError, KeyError) as e:
            lu = from_history(db, team, before)
            lu.notes.append(f"Daily Faceoff unavailable ({e}); using our own data")
    else:
        lu = from_history(db, team, before)
    changes = read_overrides(day).get(team)
    if changes:
        apply_overrides(db, lu, changes, before)
    if save:
        path = SNAPSHOTS / str(day) / f"{team}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        snap = {**asdict(lu), "fetched": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        path.write_text(json.dumps(snap, indent=1, default=int))
    return lu

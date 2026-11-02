"""Fallback shift source: parse the NHL HTML TOI shift reports (TH/TV) into shiftcharts-API-shaped rows.

Used when the shiftcharts endpoint returns no rows for a game. Players are matched to
playerIds by sweater number via the play-by-play rosterSpots.
"""
import html
import re

import nhl_api
from config import SHIFT_TYPE_CODE

HEADING = re.compile(r'class="playerHeading[^"]*"[^>]*>([^<]+)</td>')
ROW = re.compile(
    r"<tr class=\"\s*(?:odd|even)Color\">\s*"
    r"<td[^>]*>(\d+)</td>\s*"          # shift #
    r"<td[^>]*>(\w+)</td>\s*"          # period (1-3, OT)
    r"<td[^>]*>([\d:]+) / [\d:]+</td>\s*"  # start, elapsed
    r"<td[^>]*>([\d:]+) / [\d:]+</td>\s*"  # end, elapsed
    r"<td[^>]*>([\d:]+)</td>"          # duration
)


def mmss(t):
    m, s = t.split(":")
    return f"{int(m):02d}:{int(s):02d}"


def parse_report(text, team, roster_by_number):
    """Return shift rows for one team's report. Raises if a sweater number can't be matched."""
    rows = []
    # split into per-player chunks at each heading
    parts = HEADING.split(text)
    for heading, body in zip(parts[1::2], parts[2::2]):
        number, name = html.unescape(heading).strip().split(" ", 1)
        player = roster_by_number.get(int(number))
        if player is None:
            raise ValueError(f"No roster match for #{number} {name} ({team['abbrev']})")
        for shift_no, per, start, end, dur in ROW.findall(body):
            rows.append({
                "playerId": player["playerId"],
                "teamId": team["id"],
                "teamAbbrev": team["abbrev"],
                "firstName": player["firstName"]["default"],
                "lastName": player["lastName"]["default"],
                "period": 4 if per == "OT" else int(per),
                "startTime": mmss(start),
                "endTime": mmss(end),
                "duration": mmss(dur),
                "shiftNumber": int(shift_no),
                "typeCode": SHIFT_TYPE_CODE,
                "detailCode": 0,
                "eventDescription": None,
            })
    return rows


def shifts_from_html(game_id, pbp):
    data = []
    for side, team in (("H", pbp["homeTeam"]), ("V", pbp["awayTeam"])):
        roster = {p["sweaterNumber"]: p for p in pbp["rosterSpots"] if p["teamId"] == team["id"]}
        data += parse_report(nhl_api.toi_report(game_id, side), team, roster)
    return {"data": data, "total": len(data), "source": "html_toi"}

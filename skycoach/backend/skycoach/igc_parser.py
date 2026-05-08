"""IGC file parser for paragliding flight logs.

The IGC format is the FAI standard for GNSS flight recorders. Each line begins
with a record-type letter. We care about:

  H  — header (date, pilot, glider type)
  B  — fix records (UTC time, lat/lon, pressure altitude, GPS altitude)

B record layout (35 chars + optional extensions):
  B HHMMSS DDMMmmm[N|S] DDDMMmmm[E|W] A PPPPP GGGGG ...
  0 1      7            15             24 25    30
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Iterable


@dataclass
class Fix:
    """A single GNSS fix from a B-record."""

    timestamp: datetime
    lat: float
    lon: float
    pressure_alt_m: int
    gps_alt_m: int

    @property
    def alt_m(self) -> int:
        """Best-available altitude. Pressure if present, else GPS."""
        return self.pressure_alt_m if self.pressure_alt_m > 0 else self.gps_alt_m


@dataclass
class IGCFlight:
    """Parsed IGC flight."""

    flight_date: date
    pilot: str
    glider: str
    fixes: list[Fix]


def _parse_lat(raw: str) -> float:
    # DDMMmmmH  (8 chars)
    deg = int(raw[0:2])
    minutes = int(raw[2:4]) + int(raw[4:7]) / 1000.0
    val = deg + minutes / 60.0
    return -val if raw[7] == "S" else val


def _parse_lon(raw: str) -> float:
    # DDDMMmmmH (9 chars)
    deg = int(raw[0:3])
    minutes = int(raw[3:5]) + int(raw[5:8]) / 1000.0
    val = deg + minutes / 60.0
    return -val if raw[8] == "W" else val


def _parse_header_date(line: str) -> date | None:
    # HFDTE 130624  or  HFDTEDATE:130624,01
    body = line[5:].lstrip()
    if body.upper().startswith("DATE:"):
        body = body[5:]
    body = body.split(",")[0].strip()
    if len(body) < 6 or not body[:6].isdigit():
        return None
    dd, mm, yy = int(body[0:2]), int(body[2:4]), int(body[4:6])
    year = 2000 + yy if yy < 80 else 1900 + yy
    try:
        return date(year, mm, dd)
    except ValueError:
        return None


def _parse_header_value(line: str, key: str) -> str | None:
    if not line.startswith(key):
        return None
    rest = line[len(key):]
    if ":" in rest:
        rest = rest.split(":", 1)[1]
    return rest.strip() or None


def parse_igc(text: str) -> IGCFlight:
    """Parse an IGC file into a structured flight.

    Raises ValueError if the file has no usable B-records.
    """
    flight_date: date | None = None
    pilot = "Unknown"
    glider = "Unknown"
    fixes: list[Fix] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if line.startswith("HFDTE"):
            d = _parse_header_date(line)
            if d:
                flight_date = d
            continue
        if line.startswith("HFPLTPILOT") or line.startswith("HFPLT"):
            v = _parse_header_value(line, "HFPLTPILOT") or _parse_header_value(line, "HFPLT")
            if v:
                pilot = v
            continue
        if line.startswith("HFGTYGLIDERTYPE") or line.startswith("HFGTY"):
            v = _parse_header_value(line, "HFGTYGLIDERTYPE") or _parse_header_value(line, "HFGTY")
            if v:
                glider = v
            continue

        if line[0] != "B" or len(line) < 35:
            continue

        try:
            hh, mm, ss = int(line[1:3]), int(line[3:5]), int(line[5:7])
            lat = _parse_lat(line[7:15])
            lon = _parse_lon(line[15:24])
            # line[24] is fix validity (A/V); we accept both — recorders sometimes
            # mark V on the first fixes after take-off.
            press = int(line[25:30])
            gps = int(line[30:35])
        except (ValueError, IndexError):
            continue

        if flight_date is None:
            flight_date = date.today()
        ts = datetime.combine(flight_date, time(hh, mm, ss), tzinfo=timezone.utc)
        fixes.append(Fix(ts, lat, lon, press, gps))

    if not fixes:
        raise ValueError("IGC file contains no valid B-records")

    # Handle UTC midnight rollover during a flight.
    for i in range(1, len(fixes)):
        if fixes[i].timestamp < fixes[i - 1].timestamp:
            from datetime import timedelta

            fixes[i] = Fix(
                fixes[i].timestamp + timedelta(days=1),
                fixes[i].lat,
                fixes[i].lon,
                fixes[i].pressure_alt_m,
                fixes[i].gps_alt_m,
            )

    return IGCFlight(
        flight_date=flight_date or fixes[0].timestamp.date(),
        pilot=pilot,
        glider=glider,
        fixes=fixes,
    )


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    from math import asin, cos, radians, sin, sqrt

    r = 6_371_000.0
    p1, p2 = radians(lat1), radians(lat2)
    dp = radians(lat2 - lat1)
    dl = radians(lon2 - lon1)
    a = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return 2 * r * asin(sqrt(a))


def total_track_distance_m(fixes: Iterable[Fix]) -> float:
    fixes = list(fixes)
    total = 0.0
    for a, b in zip(fixes, fixes[1:]):
        total += haversine_m(a.lat, a.lon, b.lat, b.lon)
    return total

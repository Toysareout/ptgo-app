"""Open-Meteo weather lookup for flight start position + time.

Free, no API key required. Returns hourly wind/gust/temp at the flight's
start location and time. We use this to enrich the analysis (tailwind /
strong-day signals) and for the V2 coaching personalisation. Failures are
soft — analysis still succeeds without weather.

Docs: https://open-meteo.com/en/docs
"""

from __future__ import annotations

import logging
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone

log = logging.getLogger(__name__)

OPEN_METEO_URL = "https://archive-api.open-meteo.com/v1/archive"
OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
TIMEOUT_S = 5.0


@dataclass
class WeatherSnapshot:
    wind_speed_kmh: float
    wind_gusts_kmh: float
    wind_direction_deg: int
    temperature_c: float
    source: str

    def to_dict(self) -> dict:
        return {
            "wind_speed_kmh": self.wind_speed_kmh,
            "wind_gusts_kmh": self.wind_gusts_kmh,
            "wind_direction_deg": self.wind_direction_deg,
            "temperature_c": self.temperature_c,
            "source": self.source,
        }


def _fetch(url: str) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT_S) as resp:
            import json

            return json.loads(resp.read())
    except Exception as e:
        log.warning("weather fetch failed: %s", e)
        return None


def lookup(lat: float, lon: float, when: datetime) -> WeatherSnapshot | None:
    """Look up wind/temp at (lat, lon) for the hour containing `when`.

    Uses the archive endpoint for past flights, the forecast endpoint for
    flights in the last 5 days (the archive lags). Returns None on any failure.
    """
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    age_days = (datetime.now(tz=timezone.utc) - when).days
    base = OPEN_METEO_FORECAST_URL if age_days < 5 else OPEN_METEO_URL

    params = {
        "latitude": f"{lat:.4f}",
        "longitude": f"{lon:.4f}",
        "start_date": when.date().isoformat(),
        "end_date": when.date().isoformat(),
        "hourly": "wind_speed_10m,wind_gusts_10m,wind_direction_10m,temperature_2m",
        "timezone": "UTC",
        "wind_speed_unit": "kmh",
    }
    url = f"{base}?{urllib.parse.urlencode(params)}"
    data = _fetch(url)
    if not data or "hourly" not in data:
        return None

    h = data["hourly"]
    times = h.get("time", [])
    if not times:
        return None

    target_hour = when.replace(minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M")
    try:
        idx = times.index(target_hour)
    except ValueError:
        idx = 0  # fall back to start of day if hour is missing

    def at(key: str, default: float = 0.0) -> float:
        try:
            v = h[key][idx]
            return float(v) if v is not None else default
        except (KeyError, IndexError, TypeError, ValueError):
            return default

    return WeatherSnapshot(
        wind_speed_kmh=round(at("wind_speed_10m"), 1),
        wind_gusts_kmh=round(at("wind_gusts_10m"), 1),
        wind_direction_deg=int(at("wind_direction_10m")),
        temperature_c=round(at("temperature_2m"), 1),
        source="open-meteo",
    )

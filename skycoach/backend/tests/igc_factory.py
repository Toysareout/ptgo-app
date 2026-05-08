"""Helpers for synthesising IGC files in tests.

Generates B-records that the real parser can ingest, so we can build
realistic fixtures (beginner short hops, advanced XC days, strong-conditions
flights) without needing real flight data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta


def b_record(t: datetime, lat: float, lon: float, alt: int) -> str:
    lat_d = int(abs(lat))
    lat_m = (abs(lat) - lat_d) * 60
    lat_mm = int(lat_m)
    lat_mmm = int(round((lat_m - lat_mm) * 1000))
    if lat_mmm == 1000:
        lat_mmm, lat_mm = 0, lat_mm + 1
    lat_h = "N" if lat >= 0 else "S"

    lon_d = int(abs(lon))
    lon_m = (abs(lon) - lon_d) * 60
    lon_mm = int(lon_m)
    lon_mmm = int(round((lon_m - lon_mm) * 1000))
    if lon_mmm == 1000:
        lon_mmm, lon_mm = 0, lon_mm + 1
    lon_h = "E" if lon >= 0 else "W"

    return (
        f"B{t.hour:02d}{t.minute:02d}{t.second:02d}"
        f"{lat_d:02d}{lat_mm:02d}{lat_mmm:03d}{lat_h}"
        f"{lon_d:03d}{lon_mm:02d}{lon_mmm:03d}{lon_h}"
        f"A{alt:05d}{alt:05d}"
    )


@dataclass
class Phase:
    """One leg of a synthetic flight."""

    duration_s: int
    vario_ms: float                 # vertical speed in m/s
    ground_speed_kmh: float = 30.0
    heading_deg: float = 90.0       # 0=N, 90=E


@dataclass
class FlightSpec:
    pilot: str = "Test Pilot"
    glider: str = "Ozone Rush 6"
    start_lat: float = 47.500
    start_lon: float = 11.000
    start_alt: int = 1200
    start_date: datetime = field(default_factory=lambda: datetime(2026, 5, 8, 12, 0, 0))
    fix_interval_s: int = 2
    phases: list[Phase] = field(default_factory=list)


def render(spec: FlightSpec) -> str:
    """Render a FlightSpec to IGC text."""
    from math import cos, radians, sin

    lines = [
        "AXXX001",
        f"HFDTE{spec.start_date.strftime('%d%m%y')}",
        f"HFPLTPILOT:{spec.pilot}",
        f"HFGTYGLIDERTYPE:{spec.glider}",
    ]
    t = spec.start_date
    lat, lon, alt = spec.start_lat, spec.start_lon, float(spec.start_alt)

    for phase in spec.phases:
        steps = max(1, phase.duration_s // spec.fix_interval_s)
        # Convert ground speed + heading into Δlat/Δlon per fix
        dist_m = phase.ground_speed_kmh / 3.6 * spec.fix_interval_s
        d_lat = dist_m * cos(radians(phase.heading_deg)) / 111_111
        d_lon = dist_m * sin(radians(phase.heading_deg)) / (111_111 * cos(radians(lat)))
        d_alt = phase.vario_ms * spec.fix_interval_s

        for _ in range(steps):
            lines.append(b_record(t, lat, lon, int(alt)))
            t += timedelta(seconds=spec.fix_interval_s)
            lat += d_lat
            lon += d_lon
            alt += d_alt

    return "\n".join(lines) + "\n"


# ----- preset flights ---------------------------------------------------


def beginner_short_flight() -> str:
    """Short top-to-bottom flight, no thermals, gentle parameters."""
    return render(
        FlightSpec(
            pilot="Anfänger Schüler",
            glider="Advance Alpha 7",
            phases=[
                Phase(duration_s=180, vario_ms=-1.2, ground_speed_kmh=28),
                Phase(duration_s=60, vario_ms=-1.5, ground_speed_kmh=30),
            ],
        )
    )


def intermediate_thermal_flight() -> str:
    """Solid thermal day, 1.5 m/s climb, gentle conditions."""
    return render(
        FlightSpec(
            pilot="Hobby Pilot",
            glider="Ozone Rush 6",
            phases=[
                Phase(duration_s=120, vario_ms=-1.0, ground_speed_kmh=32),
                Phase(duration_s=300, vario_ms=1.6, ground_speed_kmh=22),    # thermal
                Phase(duration_s=240, vario_ms=-1.0, ground_speed_kmh=35),
                Phase(duration_s=240, vario_ms=1.4, ground_speed_kmh=22),    # thermal
                Phase(duration_s=300, vario_ms=-1.2, ground_speed_kmh=33),
            ],
        )
    )


def xc_strong_conditions_flight() -> str:
    """XC pilot, strong climbs, fast glides, one strong sink burst."""
    return render(
        FlightSpec(
            pilot="XC Cracker",
            glider="Ozone Enzo 3",
            phases=[
                Phase(duration_s=120, vario_ms=-0.8, ground_speed_kmh=42),
                Phase(duration_s=180, vario_ms=4.2, ground_speed_kmh=20),    # strong thermal
                Phase(duration_s=300, vario_ms=-1.5, ground_speed_kmh=58),   # high-speed glide
                Phase(duration_s=40, vario_ms=-4.5, ground_speed_kmh=55),    # sink burst
                Phase(duration_s=240, vario_ms=3.5, ground_speed_kmh=22),
                Phase(duration_s=300, vario_ms=-1.6, ground_speed_kmh=52),
            ],
        )
    )


def aborted_launch() -> str:
    """Pilot aborted in 90s — should flag short-flight warning."""
    return render(
        FlightSpec(
            pilot="Vorsichtiger Pilot",
            glider="Advance Alpha 7",
            phases=[
                Phase(duration_s=90, vario_ms=-2.5, ground_speed_kmh=28),
            ],
        )
    )

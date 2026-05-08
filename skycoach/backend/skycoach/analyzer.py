"""Rule-based flight analysis.

Computes flight metrics (climb, sink, thermals, altitude reserve, distance),
a 0–100 risk score, and structured coaching hints. No ML — this is the v1
deterministic engine. ML / Claude integration replaces or augments
`generate_coaching` later.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

from .igc_parser import Fix, IGCFlight, haversine_m, total_track_distance_m


THERMAL_MIN_CLIMB_MS = 0.5      # average vario to qualify as a thermal
THERMAL_MIN_DURATION_S = 20     # ignore tiny lift bursts
SINK_ALERT_MS = -3.0            # below this we flag strong sink


@dataclass
class Thermal:
    start_ts: str
    end_ts: str
    duration_s: int
    gain_m: int
    avg_climb_ms: float
    peak_climb_ms: float
    entry_alt_m: int
    exit_alt_m: int


@dataclass
class CoachingHint:
    severity: Literal["info", "warn", "danger"]
    title: str
    detail: str


@dataclass
class FlightMetrics:
    duration_s: int
    track_distance_km: float
    straight_distance_km: float
    max_alt_m: int
    min_alt_m: int
    altitude_gain_m: int
    max_climb_ms: float
    max_sink_ms: float
    avg_ground_speed_kmh: float
    max_ground_speed_kmh: float
    thermals: list[Thermal] = field(default_factory=list)
    avg_thermal_climb_ms: float = 0.0
    best_thermal_climb_ms: float = 0.0


@dataclass
class FlightAnalysis:
    pilot: str
    glider: str
    flight_date: str
    metrics: FlightMetrics
    risk_score: int          # 0 (low) – 100 (high)
    risk_level: Literal["low", "medium", "high"]
    coaching: list[CoachingHint]
    track_preview: list[tuple[float, float]]   # decimated lat/lon for the map


def _vertical_speed(prev: Fix, cur: Fix) -> float:
    dt = (cur.timestamp - prev.timestamp).total_seconds()
    if dt <= 0:
        return 0.0
    return (cur.alt_m - prev.alt_m) / dt


def _ground_speed_kmh(prev: Fix, cur: Fix) -> float:
    dt = (cur.timestamp - prev.timestamp).total_seconds()
    if dt <= 0:
        return 0.0
    d = haversine_m(prev.lat, prev.lon, cur.lat, cur.lon)
    return d / dt * 3.6


def _detect_thermals(fixes: list[Fix]) -> list[Thermal]:
    thermals: list[Thermal] = []
    if len(fixes) < 2:
        return thermals

    in_thermal = False
    start_idx = 0
    peak_climb = 0.0

    for i in range(1, len(fixes)):
        vs = _vertical_speed(fixes[i - 1], fixes[i])
        if vs >= THERMAL_MIN_CLIMB_MS:
            if not in_thermal:
                in_thermal = True
                start_idx = i - 1
                peak_climb = vs
            else:
                peak_climb = max(peak_climb, vs)
        else:
            if in_thermal:
                end_idx = i - 1
                duration = (fixes[end_idx].timestamp - fixes[start_idx].timestamp).total_seconds()
                gain = fixes[end_idx].alt_m - fixes[start_idx].alt_m
                if duration >= THERMAL_MIN_DURATION_S and gain > 5:
                    thermals.append(
                        Thermal(
                            start_ts=fixes[start_idx].timestamp.isoformat(),
                            end_ts=fixes[end_idx].timestamp.isoformat(),
                            duration_s=int(duration),
                            gain_m=int(gain),
                            avg_climb_ms=round(gain / duration, 2) if duration else 0.0,
                            peak_climb_ms=round(peak_climb, 2),
                            entry_alt_m=fixes[start_idx].alt_m,
                            exit_alt_m=fixes[end_idx].alt_m,
                        )
                    )
                in_thermal = False
                peak_climb = 0.0

    if in_thermal:
        end_idx = len(fixes) - 1
        duration = (fixes[end_idx].timestamp - fixes[start_idx].timestamp).total_seconds()
        gain = fixes[end_idx].alt_m - fixes[start_idx].alt_m
        if duration >= THERMAL_MIN_DURATION_S and gain > 5:
            thermals.append(
                Thermal(
                    start_ts=fixes[start_idx].timestamp.isoformat(),
                    end_ts=fixes[end_idx].timestamp.isoformat(),
                    duration_s=int(duration),
                    gain_m=int(gain),
                    avg_climb_ms=round(gain / duration, 2) if duration else 0.0,
                    peak_climb_ms=round(peak_climb, 2),
                    entry_alt_m=fixes[start_idx].alt_m,
                    exit_alt_m=fixes[end_idx].alt_m,
                )
            )

    return thermals


def _smoothed_vertical_speeds(fixes: list[Fix], window: int = 3) -> list[float]:
    """Rolling-average vertical speed to suppress GPS jitter."""
    raw = [0.0]
    for i in range(1, len(fixes)):
        raw.append(_vertical_speed(fixes[i - 1], fixes[i]))
    out = []
    for i in range(len(raw)):
        lo = max(0, i - window + 1)
        chunk = raw[lo : i + 1]
        out.append(sum(chunk) / len(chunk))
    return out


def _compute_metrics(flight: IGCFlight) -> FlightMetrics:
    fixes = flight.fixes
    duration = (fixes[-1].timestamp - fixes[0].timestamp).total_seconds()
    track_m = total_track_distance_m(fixes)
    straight_m = haversine_m(fixes[0].lat, fixes[0].lon, fixes[-1].lat, fixes[-1].lon)

    alts = [f.alt_m for f in fixes]
    vs_smoothed = _smoothed_vertical_speeds(fixes)

    max_climb = max(vs_smoothed) if vs_smoothed else 0.0
    max_sink = min(vs_smoothed) if vs_smoothed else 0.0

    speeds = [_ground_speed_kmh(fixes[i - 1], fixes[i]) for i in range(1, len(fixes))]
    avg_speed = sum(speeds) / len(speeds) if speeds else 0.0
    max_speed = max(speeds) if speeds else 0.0

    altitude_gain = 0
    for i in range(1, len(alts)):
        d = alts[i] - alts[i - 1]
        if d > 0:
            altitude_gain += d

    thermals = _detect_thermals(fixes)
    thermal_climbs = [t.avg_climb_ms for t in thermals]

    return FlightMetrics(
        duration_s=int(duration),
        track_distance_km=round(track_m / 1000, 2),
        straight_distance_km=round(straight_m / 1000, 2),
        max_alt_m=max(alts),
        min_alt_m=min(alts),
        altitude_gain_m=altitude_gain,
        max_climb_ms=round(max_climb, 2),
        max_sink_ms=round(max_sink, 2),
        avg_ground_speed_kmh=round(avg_speed, 1),
        max_ground_speed_kmh=round(max_speed, 1),
        thermals=thermals,
        avg_thermal_climb_ms=round(sum(thermal_climbs) / len(thermal_climbs), 2) if thermal_climbs else 0.0,
        best_thermal_climb_ms=round(max(thermal_climbs), 2) if thermal_climbs else 0.0,
    )


def _risk_score(m: FlightMetrics) -> tuple[int, Literal["low", "medium", "high"]]:
    """Rule-based 0–100 risk score.

    Higher = more concerning. Drivers:
      - sustained strong sink
      - very high ground speed (possible tailwind / strong wind day)
      - very low altitude reserve at landing  (we approximate via min_alt_m
        relative to max_alt_m — a dedicated DEM lookup comes in v2)
      - extremely strong climb rates (turbulent thermals)
    """
    score = 0

    if m.max_sink_ms <= -5:
        score += 30
    elif m.max_sink_ms <= SINK_ALERT_MS:
        score += 18
    elif m.max_sink_ms <= -2:
        score += 8

    if m.max_ground_speed_kmh >= 65:
        score += 25
    elif m.max_ground_speed_kmh >= 55:
        score += 15
    elif m.max_ground_speed_kmh >= 45:
        score += 6

    if m.max_climb_ms >= 6:
        score += 20
    elif m.max_climb_ms >= 4:
        score += 10

    alt_band = m.max_alt_m - m.min_alt_m
    if alt_band > 1500 and m.min_alt_m < 300:
        score += 15
    elif alt_band > 800 and m.min_alt_m < 200:
        score += 10

    if m.duration_s < 180:
        score += 10  # very short flight — possible aborted launch

    score = max(0, min(100, score))
    level: Literal["low", "medium", "high"] = (
        "high" if score >= 60 else "medium" if score >= 30 else "low"
    )
    return score, level


def _generate_coaching(m: FlightMetrics, risk: int) -> list[CoachingHint]:
    hints: list[CoachingHint] = []

    if not m.thermals:
        hints.append(
            CoachingHint(
                "info",
                "Kein klares Thermikzentrum erkannt",
                "Der Flug zeigt überwiegend Gleitphasen ohne nachhaltigen Steigflug. "
                "Übe gezieltes Zentrieren — fliege beim ersten Steigsignal eine 360°-Probe und korrigiere Richtung Kernsteigen.",
            )
        )
    else:
        best = max(m.thermals, key=lambda t: t.avg_climb_ms)
        hints.append(
            CoachingHint(
                "info",
                f"Beste Thermik: {best.avg_climb_ms:.1f} m/s über {best.duration_s}s",
                f"Höhengewinn {best.gain_m} m. Wenn deine Steigwerte streuen, kreise enger und versetzte das Zentrum systematisch in Richtung höchstem Steigen.",
            )
        )

    if m.avg_thermal_climb_ms and m.avg_thermal_climb_ms < 1.0 and m.thermals:
        hints.append(
            CoachingHint(
                "info",
                "Schwache Thermikausnutzung",
                f"Durchschnittliches Thermiksteigen {m.avg_thermal_climb_ms:.1f} m/s. "
                "Achte auf konstanten Schräglagen-Winkel und vermeide Pumpen am Bremsgriff — das bremst dich aus.",
            )
        )

    if m.max_sink_ms <= SINK_ALERT_MS:
        hints.append(
            CoachingHint(
                "warn",
                f"Starkes Sinken erkannt ({m.max_sink_ms:.1f} m/s)",
                "Sinkwerte unter -3 m/s deuten auf Lee, Abwind oder turbulente Luft hin. "
                "Im Folgeflug: solche Zonen bei ähnlicher Wetterlage großräumig umfliegen, Geschwindigkeit erhöhen, Flugrichtung Richtung Luvseite ändern.",
            )
        )

    if m.max_climb_ms >= 5:
        hints.append(
            CoachingHint(
                "warn",
                f"Sehr starkes Steigen ({m.max_climb_ms:.1f} m/s)",
                "Solche Werte sind oft mit turbulenten Barträndern verbunden. "
                "Aktiv fliegen, Schirmkontrolle priorisieren, nicht zu eng zentrieren bevor das Bart stabil ist.",
            )
        )

    if m.max_ground_speed_kmh >= 55:
        hints.append(
            CoachingHint(
                "warn",
                f"Hohe Bodengeschwindigkeit ({m.max_ground_speed_kmh:.0f} km/h)",
                "Vermutlich Rückenwindkomponente. Prüfe Windprognose und Windsack vor Start. "
                "Bei Topspeed > 55 km/h Boden ist eine Landung gegen den Wind kritisch zu planen.",
            )
        )

    if m.duration_s < 180:
        hints.append(
            CoachingHint(
                "warn",
                "Sehr kurzer Flug",
                "Unter 3 Minuten Flugzeit deutet auf Startabbruch oder schnelle Außenlandung hin. "
                "Vor dem nächsten Start: Startcheck, Windrichtung, Schirmaufzug bewusst durchgehen.",
            )
        )

    if risk >= 60:
        hints.append(
            CoachingHint(
                "danger",
                "Hoher Gesamt-Risikoscore",
                "Mehrere Risikofaktoren kombiniert. Empfehlung: Flugbesprechung mit Fluglehrer, "
                "vor dem nächsten ähnlichen Tag Wetterbriefing intensiv prüfen.",
            )
        )
    elif risk >= 30:
        hints.append(
            CoachingHint(
                "info",
                "Mittlerer Risikoscore",
                "Solide Flugparameter mit einzelnen Auffälligkeiten — sieh dir die Warnungen oben an.",
            )
        )
    else:
        hints.append(
            CoachingHint(
                "info",
                "Niedriger Risikoscore",
                "Sauberer Flug ohne kritische Werte. Fokus für nächsten Flug: Thermikausnutzung verfeinern.",
            )
        )

    return hints


def _decimate_track(fixes: list[Fix], max_points: int = 400) -> list[tuple[float, float]]:
    if len(fixes) <= max_points:
        return [(f.lat, f.lon) for f in fixes]
    step = len(fixes) // max_points
    return [(fixes[i].lat, fixes[i].lon) for i in range(0, len(fixes), step)]


def analyze_flight(flight: IGCFlight) -> FlightAnalysis:
    """End-to-end analysis: parsed IGC -> structured analysis."""
    metrics = _compute_metrics(flight)
    risk, level = _risk_score(metrics)
    coaching = _generate_coaching(metrics, risk)

    return FlightAnalysis(
        pilot=flight.pilot,
        glider=flight.glider,
        flight_date=flight.flight_date.isoformat(),
        metrics=metrics,
        risk_score=risk,
        risk_level=level,
        coaching=coaching,
        track_preview=_decimate_track(flight.fixes),
    )


def analysis_to_dict(a: FlightAnalysis) -> dict:
    """Serialise to a JSON-friendly dict (dataclasses + nested dataclasses)."""
    return asdict(a)

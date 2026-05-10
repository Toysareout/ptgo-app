"""Analyzer unit tests."""

from __future__ import annotations

from skycoach.analyzer import analyze_flight
from skycoach.igc_parser import parse_igc

from .igc_factory import (
    aborted_launch,
    beginner_short_flight,
    intermediate_thermal_flight,
    xc_strong_conditions_flight,
)


def _analyze(text: str):
    return analyze_flight(parse_igc(text))


def test_beginner_short_flight_has_low_risk() -> None:
    a = _analyze(beginner_short_flight())
    assert a.risk_level == "low"
    assert a.risk_score < 30
    assert len(a.metrics.thermals) == 0


def test_thermal_day_detects_thermals() -> None:
    a = _analyze(intermediate_thermal_flight())
    assert len(a.metrics.thermals) >= 2
    # 1.4–1.6 m/s thermals should round to roughly that range
    assert 1.0 <= a.metrics.avg_thermal_climb_ms <= 2.0


def test_strong_conditions_detect_high_climb_and_sink() -> None:
    a = _analyze(xc_strong_conditions_flight())
    assert a.metrics.max_climb_ms >= 3.5
    assert a.metrics.max_sink_ms <= -3.0
    assert a.risk_level in {"medium", "high"}


def test_strong_conditions_flag_warnings() -> None:
    a = _analyze(xc_strong_conditions_flight())
    titles = [h.title for h in a.coaching]
    assert any("Sinken" in t for t in titles)
    assert any("Bodengeschwindigkeit" in t for t in titles)


def test_aborted_launch_flagged() -> None:
    a = _analyze(aborted_launch())
    titles = [h.title for h in a.coaching]
    assert any("kurzer Flug" in t for t in titles)


def test_thermal_day_metrics_consistent() -> None:
    a = _analyze(intermediate_thermal_flight())
    m = a.metrics
    assert m.duration_s > 0
    assert m.track_distance_km > 0
    assert m.max_alt_m > m.min_alt_m
    assert m.max_climb_ms > 0
    assert m.max_sink_ms < 0
    assert m.altitude_gain_m > 0


def test_track_preview_decimated_to_400_points() -> None:
    """Long flights still produce a manageable map preview."""
    from .igc_factory import FlightSpec, Phase, render

    long = render(FlightSpec(phases=[Phase(duration_s=3600, vario_ms=0.3, ground_speed_kmh=30)]))
    a = _analyze(long)
    assert len(a.track_preview) <= 400


def test_coaching_always_has_summary_hint() -> None:
    """Every analysis should produce at least one coaching hint."""
    for fixture in [beginner_short_flight, intermediate_thermal_flight, xc_strong_conditions_flight]:
        a = _analyze(fixture())
        assert len(a.coaching) >= 1

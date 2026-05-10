"""V2 personalisation tests: pilot-context + weather-aware analysis."""

from __future__ import annotations

from skycoach.analyzer import PilotContext, analyze_flight
from skycoach.igc_parser import parse_igc

from .igc_factory import (
    intermediate_thermal_flight,
    xc_strong_conditions_flight,
)


def _analyze(text: str, **kwargs):
    return analyze_flight(parse_igc(text), **kwargs)


def test_beginner_gets_higher_risk_than_xc_for_same_flight() -> None:
    text = xc_strong_conditions_flight()
    beginner = _analyze(text, ctx=PilotContext(level="beginner"))
    xc = _analyze(text, ctx=PilotContext(level="xc"))
    assert beginner.risk_score >= xc.risk_score


def test_beginner_coaching_recommends_instructor_when_risky() -> None:
    a = _analyze(xc_strong_conditions_flight(), ctx=PilotContext(level="beginner"))
    titles = " | ".join(h.title for h in a.coaching)
    assert "Erfahrungslevel" in titles or "Fluglehrer" in " | ".join(h.detail for h in a.coaching)


def test_xc_coaching_focuses_on_performance() -> None:
    a = _analyze(intermediate_thermal_flight(), ctx=PilotContext(level="xc"))
    titles = " ".join(h.title for h in a.coaching)
    assert "Performance" in titles or "Linienwahl" in titles or "Optimierungshebel" in titles


def test_high_class_wing_gets_extra_warning_in_strong_thermal() -> None:
    a = _analyze(
        xc_strong_conditions_flight(),
        ctx=PilotContext(level="advanced", wing_class="EN-D"),
    )
    titles = " ".join(h.title for h in a.coaching)
    assert "EN-D" in titles


def test_weather_strong_wind_raises_risk_and_adds_hint() -> None:
    text = intermediate_thermal_flight()
    base = _analyze(text)
    windy = _analyze(text, weather={"wind_speed_kmh": 28, "wind_gusts_kmh": 40})
    assert windy.risk_score > base.risk_score
    titles = " ".join(h.title for h in windy.coaching)
    assert "Böen" in titles


def test_weather_attached_to_analysis_output() -> None:
    a = _analyze(
        intermediate_thermal_flight(),
        weather={"wind_speed_kmh": 10, "wind_gusts_kmh": 15, "source": "open-meteo"},
    )
    assert a.weather is not None
    assert a.weather["source"] == "open-meteo"


def test_no_context_no_weather_still_works() -> None:
    """Backwards-compatible: pre-V2 calls still produce a sensible analysis."""
    a = _analyze(intermediate_thermal_flight())
    assert a.risk_score >= 0
    assert a.weather is None
    assert len(a.coaching) >= 1

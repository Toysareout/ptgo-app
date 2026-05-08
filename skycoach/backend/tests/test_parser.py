"""Parser unit tests."""

from __future__ import annotations

import pytest

from skycoach.igc_parser import parse_igc

from .igc_factory import beginner_short_flight, intermediate_thermal_flight


def test_parse_extracts_pilot_and_glider() -> None:
    flight = parse_igc(beginner_short_flight())
    assert flight.pilot == "Anfänger Schüler"
    assert flight.glider == "Advance Alpha 7"
    assert len(flight.fixes) > 0


def test_parse_extracts_date() -> None:
    flight = parse_igc(intermediate_thermal_flight())
    assert flight.flight_date.year == 2026
    assert flight.flight_date.month == 5
    assert flight.flight_date.day == 8


def test_parse_rejects_empty_file() -> None:
    with pytest.raises(ValueError):
        parse_igc("")


def test_parse_rejects_no_b_records() -> None:
    with pytest.raises(ValueError):
        parse_igc("AXXX001\nHFDTE150624\n")


def test_parse_handles_dateformat_dialect() -> None:
    igc = (
        "AXXX001\n"
        "HFDTEDATE:150624,01\n"
        "HFPLTPILOT:Test\n"
        "B1200004730000N01100000EA0120001200\n"
    )
    flight = parse_igc(igc)
    assert flight.flight_date.day == 15


def test_parse_skips_invalid_b_records() -> None:
    igc = (
        "AXXX001\n"
        "HFDTE150624\n"
        "BNOTAVALIDRECORD\n"
        "B1200004730000N01100000EA0120001200\n"
    )
    flight = parse_igc(igc)
    assert len(flight.fixes) == 1


def test_parse_handles_southern_western_hemispheres() -> None:
    igc = (
        "AXXX001\n"
        "HFDTE150624\n"
        "B1200003330000S07100000WA0050000500\n"
    )
    flight = parse_igc(igc)
    assert flight.fixes[0].lat < 0
    assert flight.fixes[0].lon < 0


def test_parse_uses_pressure_alt_when_available() -> None:
    igc = (
        "AXXX001\n"
        "HFDTE150624\n"
        "B1200004730000N01100000EA0150001200\n"
    )
    flight = parse_igc(igc)
    assert flight.fixes[0].alt_m == 1500


def test_parse_falls_back_to_gps_alt_when_pressure_zero() -> None:
    igc = (
        "AXXX001\n"
        "HFDTE150624\n"
        "B1200004730000N01100000EA0000001200\n"
    )
    flight = parse_igc(igc)
    assert flight.fixes[0].alt_m == 1200

#!/usr/bin/env python3
"""Calibration CLI.

Process a folder of real IGC files, run them through the analyzer, and emit a
CSV + summary so the flight-instructor partner can compare the engine output to
his manual corrections.

Usage:
    python -m scripts.calibrate /path/to/igc/folder
    python -m scripts.calibrate /path/to/igc/folder --out report.csv
    python -m scripts.calibrate file1.igc file2.igc --json
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path

# Allow running both as `python -m scripts.calibrate` (from backend/) and
# `python scripts/calibrate.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skycoach.analyzer import analyze_flight, analysis_to_dict   # noqa: E402
from skycoach.igc_parser import parse_igc                          # noqa: E402


CSV_FIELDS = [
    "file",
    "pilot",
    "glider",
    "date",
    "duration_min",
    "track_km",
    "max_alt_m",
    "max_climb_ms",
    "max_sink_ms",
    "thermals",
    "avg_thermal_ms",
    "max_speed_kmh",
    "risk_score",
    "risk_level",
    "warnings",
]


def collect_paths(args: list[str]) -> list[Path]:
    paths: list[Path] = []
    for raw in args:
        p = Path(raw)
        if p.is_dir():
            paths.extend(sorted(p.rglob("*.igc")))
        elif p.is_file():
            paths.append(p)
        else:
            print(f"warning: {raw} not found", file=sys.stderr)
    return paths


def analyse_one(path: Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="ignore")
    flight = parse_igc(text)
    a = analyze_flight(flight)
    warnings = [h.title for h in a.coaching if h.severity in ("warn", "danger")]
    return {
        "file": path.name,
        "pilot": a.pilot,
        "glider": a.glider,
        "date": a.flight_date,
        "duration_min": round(a.metrics.duration_s / 60, 1),
        "track_km": a.metrics.track_distance_km,
        "max_alt_m": a.metrics.max_alt_m,
        "max_climb_ms": a.metrics.max_climb_ms,
        "max_sink_ms": a.metrics.max_sink_ms,
        "thermals": len(a.metrics.thermals),
        "avg_thermal_ms": a.metrics.avg_thermal_climb_ms,
        "max_speed_kmh": a.metrics.max_ground_speed_kmh,
        "risk_score": a.risk_score,
        "risk_level": a.risk_level,
        "warnings": " | ".join(warnings),
        "_full": analysis_to_dict(a),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="SkyCoach analyzer calibration tool")
    parser.add_argument("paths", nargs="+", help="IGC files or directories")
    parser.add_argument("--out", default="calibration.csv", help="CSV output path")
    parser.add_argument("--json", action="store_true", help="Also emit JSON next to CSV")
    args = parser.parse_args()

    paths = collect_paths(args.paths)
    if not paths:
        print("No IGC files found.", file=sys.stderr)
        return 1

    rows: list[dict] = []
    failures: list[tuple[str, str]] = []
    for p in paths:
        try:
            rows.append(analyse_one(p))
            print(f"✓ {p.name}")
        except Exception as e:
            failures.append((p.name, str(e)))
            print(f"✗ {p.name}: {e}", file=sys.stderr)

    if rows:
        with open(args.out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            w.writeheader()
            for r in rows:
                w.writerow({k: r[k] for k in CSV_FIELDS})
        print(f"\nWrote {len(rows)} rows to {args.out}")

        if args.json:
            json_path = os.path.splitext(args.out)[0] + ".json"
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump([r["_full"] for r in rows], f, indent=2, ensure_ascii=False)
            print(f"Wrote full analyses to {json_path}")

    print()
    print("=== Summary ===")
    print(f"Analysed:        {len(rows)}")
    print(f"Failed:          {len(failures)}")
    if rows:
        risks = [r["risk_score"] for r in rows]
        print(f"Risk score avg:  {sum(risks) / len(risks):.1f}")
        print(f"Risk score max:  {max(risks)}")
        print(f"Total thermals:  {sum(r['thermals'] for r in rows)}")
        for level in ("low", "medium", "high"):
            count = sum(1 for r in rows if r["risk_level"] == level)
            print(f"  {level:6s}        {count}")

    if failures:
        print("\nFailures:")
        for name, err in failures:
            print(f"  {name}: {err}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

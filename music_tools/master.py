#!/usr/bin/env python3
"""
MASTER.PY — Auto-Mastering Engine
==================================
Automatisches Mastering für ungemasterte Songs.
Verwendet Spotify Pedalboard (EQ, Kompressor, Limiter) + pyloudnorm (LUFS).
Optional: Reference-based Mastering via Matchering.

Usage:
    python master.py                          # Interaktiver Modus
    python master.py input.wav                # Einzelner Song
    python master.py ./songs/                 # Ganzer Ordner
    python master.py input.wav -r ref.wav     # Mit Reference-Track
    python master.py ./songs/ --preset loud   # Mit Preset
"""

import argparse
import os
import sys
import glob
import numpy as np
import soundfile as sf

try:
    import pedalboard
    from pedalboard import (
        Pedalboard, Compressor, Gain, Limiter,
        HighpassFilter, LowShelfFilter, HighShelfFilter, PeakFilter,
    )
    HAS_PEDALBOARD = True
except ImportError:
    HAS_PEDALBOARD = False

try:
    import pyloudnorm as pyln
    HAS_PYLOUDNORM = True
except ImportError:
    HAS_PYLOUDNORM = False

try:
    import matchering as mg
    HAS_MATCHERING = True
except ImportError:
    HAS_MATCHERING = False


# ─── PRESETS ─────────────────────────────────────────────────────────────────

PRESETS = {
    "streaming": {
        "name": "Streaming (Spotify/Apple Music)",
        "target_lufs": -14.0,
        "eq": {
            "highpass_hz": 30,
            "low_shelf_hz": 80, "low_shelf_db": 1.5,
            "presence_hz": 3000, "presence_db": 1.0, "presence_q": 0.7,
            "air_hz": 12000, "air_db": 2.0,
        },
        "compressor": {
            "threshold_db": -18.0,
            "ratio": 3.0,
            "attack_ms": 10.0,
            "release_ms": 100.0,
        },
        "limiter_db": -1.0,
    },
    "loud": {
        "name": "Loud (YouTube/SoundCloud)",
        "target_lufs": -10.0,
        "eq": {
            "highpass_hz": 25,
            "low_shelf_hz": 60, "low_shelf_db": 2.5,
            "presence_hz": 2500, "presence_db": 2.0, "presence_q": 0.8,
            "air_hz": 10000, "air_db": 3.0,
        },
        "compressor": {
            "threshold_db": -14.0,
            "ratio": 4.5,
            "attack_ms": 5.0,
            "release_ms": 80.0,
        },
        "limiter_db": -0.3,
    },
    "gentle": {
        "name": "Gentle (Podcast/Acoustic)",
        "target_lufs": -16.0,
        "eq": {
            "highpass_hz": 40,
            "low_shelf_hz": 100, "low_shelf_db": 0.5,
            "presence_hz": 4000, "presence_db": 0.5, "presence_q": 0.5,
            "air_hz": 14000, "air_db": 1.0,
        },
        "compressor": {
            "threshold_db": -22.0,
            "ratio": 2.0,
            "attack_ms": 20.0,
            "release_ms": 150.0,
        },
        "limiter_db": -1.5,
    },
    "rock": {
        "name": "Rock/Metal (Aggressive)",
        "target_lufs": -11.0,
        "eq": {
            "highpass_hz": 30,
            "low_shelf_hz": 80, "low_shelf_db": 3.0,
            "presence_hz": 2000, "presence_db": 2.5, "presence_q": 1.0,
            "air_hz": 8000, "air_db": 2.5,
        },
        "compressor": {
            "threshold_db": -12.0,
            "ratio": 6.0,
            "attack_ms": 3.0,
            "release_ms": 60.0,
        },
        "limiter_db": -0.1,
    },
}


# ─── AUDIO I/O ───────────────────────────────────────────────────────────────

SUPPORTED_EXTENSIONS = {".wav", ".mp3", ".flac", ".ogg", ".aiff", ".aif"}


def find_audio_files(path: str) -> list[str]:
    """Findet alle Audio-Dateien in einem Pfad (Datei oder Ordner)."""
    if os.path.isfile(path):
        ext = os.path.splitext(path)[1].lower()
        if ext in SUPPORTED_EXTENSIONS:
            return [path]
        print(f"  ⚠ Nicht unterstütztes Format: {ext}")
        return []

    if os.path.isdir(path):
        files = []
        for ext in SUPPORTED_EXTENSIONS:
            files.extend(glob.glob(os.path.join(path, f"*{ext}")))
            files.extend(glob.glob(os.path.join(path, f"*{ext.upper()}")))
        files.sort()
        return files

    print(f"  ⚠ Pfad nicht gefunden: {path}")
    return []


def load_audio(filepath: str) -> tuple[np.ndarray, int]:
    """Lädt eine Audiodatei und gibt (samples, samplerate) zurück."""
    audio, sr = sf.read(filepath, dtype="float32")
    # Mono → Stereo
    if audio.ndim == 1:
        audio = np.column_stack([audio, audio])
    return audio, sr


def save_audio(filepath: str, audio: np.ndarray, sr: int, fmt: str = "wav"):
    """Speichert Audio als WAV oder MP3."""
    os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)

    if fmt == "wav":
        sf.write(filepath, audio, sr, subtype="PCM_24")
        return

    if fmt == "mp3":
        try:
            from pedalboard.io import AudioFile
            with AudioFile(filepath, "w", samplerate=sr, num_channels=audio.shape[1] if audio.ndim > 1 else 1, quality="320k") as f:
                f.write(audio.T if audio.ndim > 1 else audio.reshape(1, -1))
        except Exception:
            # Fallback: als WAV speichern
            wav_path = filepath.replace(".mp3", ".wav")
            sf.write(wav_path, audio, sr, subtype="PCM_24")
            print(f"  ℹ MP3-Export fehlgeschlagen, gespeichert als: {wav_path}")


# ─── MASTERING ENGINE ────────────────────────────────────────────────────────

def measure_loudness(audio: np.ndarray, sr: int) -> float:
    """Misst die integrierte Lautheit in LUFS."""
    if not HAS_PYLOUDNORM:
        return -14.0  # Fallback
    meter = pyln.Meter(sr)
    return meter.integrated_loudness(audio)


def build_mastering_chain(preset: dict, sr: int) -> "Pedalboard":
    """Baut die Mastering-Signalkette aus einem Preset."""
    if not HAS_PEDALBOARD:
        raise RuntimeError("pedalboard ist nicht installiert. Bitte: pip install pedalboard")

    eq = preset["eq"]
    comp = preset["compressor"]

    board = Pedalboard([
        # 1. High-Pass: Subsonic-Frequenzen entfernen
        HighpassFilter(cutoff_frequency_hz=eq["highpass_hz"]),

        # 2. Low Shelf: Bass-Boost
        LowShelfFilter(
            cutoff_frequency_hz=eq["low_shelf_hz"],
            gain_db=eq["low_shelf_db"],
        ),

        # 3. Presence-Boost: Klarheit
        PeakFilter(
            cutoff_frequency_hz=eq["presence_hz"],
            gain_db=eq["presence_db"],
            q=eq["presence_q"],
        ),

        # 4. Air/High Shelf: Brillanz
        HighShelfFilter(
            cutoff_frequency_hz=eq["air_hz"],
            gain_db=eq["air_db"],
        ),

        # 5. Kompressor
        Compressor(
            threshold_db=comp["threshold_db"],
            ratio=comp["ratio"],
            attack_ms=comp["attack_ms"],
            release_ms=comp["release_ms"],
        ),

        # 6. Limiter: Peaks begrenzen
        Limiter(threshold_db=preset["limiter_db"]),
    ])

    return board


def apply_loudness_normalization(audio: np.ndarray, sr: int, target_lufs: float) -> np.ndarray:
    """Normalisiert Audio auf Ziel-LUFS."""
    if not HAS_PYLOUDNORM:
        print("  ⚠ pyloudnorm nicht installiert, überspringe Loudness-Normalisierung")
        return audio

    meter = pyln.Meter(sr)
    current_lufs = meter.integrated_loudness(audio)

    if np.isinf(current_lufs):
        print("  ⚠ Audio ist still, überspringe Normalisierung")
        return audio

    # Gain berechnen
    gain_db = target_lufs - current_lufs
    gain_linear = 10.0 ** (gain_db / 20.0)
    normalized = audio * gain_linear

    # Clipping verhindern
    peak = np.max(np.abs(normalized))
    if peak > 1.0:
        normalized = normalized / peak * 0.99

    return normalized


def master_track(filepath: str, preset_name: str = "streaming",
                 output_dir: str | None = None,
                 reference_path: str | None = None) -> str | None:
    """Mastert einen einzelnen Track."""

    filename = os.path.basename(filepath)
    name, ext = os.path.splitext(filename)
    print(f"\n{'='*60}")
    print(f"  🎵 Mastering: {filename}")
    print(f"{'='*60}")

    # Reference-based Mastering (Matchering)
    if reference_path and HAS_MATCHERING:
        return _master_with_reference(filepath, reference_path, output_dir)

    if reference_path and not HAS_MATCHERING:
        print("  ⚠ matchering nicht installiert, verwende Standard-Mastering")

    preset = PRESETS.get(preset_name, PRESETS["streaming"])
    print(f"  Preset: {preset['name']}")

    # Audio laden
    print("  → Audio laden...")
    audio, sr = load_audio(filepath)
    duration = len(audio) / sr
    print(f"  → Dauer: {duration:.1f}s | Sample Rate: {sr}Hz | Channels: {audio.shape[1]}")

    # Lautheit vorher
    lufs_before = measure_loudness(audio, sr)
    print(f"  → Lautheit vorher: {lufs_before:.1f} LUFS")

    # Mastering-Chain anwenden
    print("  → Mastering-Chain anwenden...")
    board = build_mastering_chain(preset, sr)
    # Pedalboard erwartet (channels, samples)
    processed = board(audio.T, sr).T

    # Loudness-Normalisierung
    print(f"  → Loudness-Normalisierung auf {preset['target_lufs']} LUFS...")
    processed = apply_loudness_normalization(processed, sr, preset["target_lufs"])

    # Lautheit nachher
    lufs_after = measure_loudness(processed, sr)
    print(f"  → Lautheit nachher: {lufs_after:.1f} LUFS")

    # Speichern
    if output_dir is None:
        output_dir = os.path.join(os.path.dirname(filepath), "mastered")

    out_wav = os.path.join(output_dir, f"{name}_mastered.wav")
    out_mp3 = os.path.join(output_dir, f"{name}_mastered.mp3")

    print(f"  → Speichere WAV: {out_wav}")
    save_audio(out_wav, processed, sr, "wav")

    print(f"  → Speichere MP3: {out_mp3}")
    save_audio(out_mp3, processed, sr, "mp3")

    print(f"  ✅ Fertig! Δ LUFS: {lufs_after - lufs_before:+.1f}")
    return out_wav


def _master_with_reference(filepath: str, reference_path: str,
                           output_dir: str | None = None) -> str | None:
    """Mastering mit Reference-Track via Matchering."""
    filename = os.path.basename(filepath)
    name, _ = os.path.splitext(filename)

    if output_dir is None:
        output_dir = os.path.join(os.path.dirname(filepath), "mastered")
    os.makedirs(output_dir, exist_ok=True)

    out_wav = os.path.join(output_dir, f"{name}_mastered.wav")

    print(f"  Modus: Reference-based Mastering")
    print(f"  Reference: {os.path.basename(reference_path)}")
    print("  → Matchering läuft...")

    try:
        mg.process(
            target=filepath,
            reference=reference_path,
            results=[
                mg.Result(out_wav, subtype="PCM_24"),
            ],
        )
        print(f"  ✅ Fertig! → {out_wav}")
        return out_wav
    except Exception as e:
        print(f"  ❌ Matchering-Fehler: {e}")
        return None


# ─── INTERACTIVE MODE ────────────────────────────────────────────────────────

def interactive_mode():
    """Interaktiver Modus — fragt nach Input, Preset, etc."""
    print("\n" + "="*60)
    print("  🎧 MASTER.PY — Auto-Mastering Engine")
    print("  Powered by Spotify Pedalboard + pyloudnorm")
    print("="*60)

    # Status der Libraries
    print("\n  Libraries:")
    print(f"    pedalboard:  {'✅' if HAS_PEDALBOARD else '❌ nicht installiert'}")
    print(f"    pyloudnorm:  {'✅' if HAS_PYLOUDNORM else '❌ nicht installiert'}")
    print(f"    matchering:  {'✅' if HAS_MATCHERING else '⬜ optional'}")

    if not HAS_PEDALBOARD:
        print("\n  ❌ pedalboard wird benötigt! pip install pedalboard")
        sys.exit(1)

    # Input
    print("\n  Gib den Pfad zu deinem Song oder Ordner ein:")
    input_path = input("  → ").strip().strip("'\"")

    if not input_path:
        print("  ❌ Kein Pfad angegeben.")
        sys.exit(1)

    files = find_audio_files(input_path)
    if not files:
        print(f"  ❌ Keine Audio-Dateien gefunden in: {input_path}")
        sys.exit(1)

    print(f"\n  Gefunden: {len(files)} Datei(en)")
    for f in files:
        print(f"    • {os.path.basename(f)}")

    # Preset
    print("\n  Wähle ein Mastering-Preset:")
    for i, (key, preset) in enumerate(PRESETS.items(), 1):
        print(f"    {i}. {preset['name']} ({preset['target_lufs']} LUFS)")

    choice = input("  → [1-4, default=1]: ").strip()
    preset_keys = list(PRESETS.keys())
    preset_name = preset_keys[int(choice) - 1] if choice.isdigit() and 1 <= int(choice) <= 4 else "streaming"

    # Reference-Track
    reference_path = None
    if HAS_MATCHERING:
        print("\n  Hast du einen Reference-Track? (Leer lassen für Standard-Mastering)")
        ref = input("  → ").strip().strip("'\"")
        if ref and os.path.isfile(ref):
            reference_path = ref

    # Output-Verzeichnis
    print("\n  Output-Verzeichnis (Leer = ./mastered/):")
    output_dir = input("  → ").strip().strip("'\"") or None

    # Los geht's!
    print(f"\n  🚀 Starte Mastering von {len(files)} Datei(en)...")
    results = []
    for f in files:
        result = master_track(f, preset_name, output_dir, reference_path)
        if result:
            results.append(result)

    # Summary
    print(f"\n{'='*60}")
    print(f"  ✅ FERTIG! {len(results)}/{len(files)} Dateien gemastert.")
    if results:
        print(f"  📁 Output: {os.path.dirname(results[0])}")
    print(f"{'='*60}\n")

    return results


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="🎧 Auto-Mastering Engine — Mastere deine Songs automatisch",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Beispiele:
  python master.py                              # Interaktiver Modus
  python master.py song.wav                     # Einzelner Song
  python master.py ./songs/                     # Ganzer Ordner
  python master.py song.wav -r reference.wav    # Mit Reference-Track
  python master.py ./songs/ --preset rock       # Rock-Preset
  python master.py song.wav -o ./output/        # Custom Output-Ordner
        """,
    )
    parser.add_argument("input", nargs="?", help="Audio-Datei oder Ordner")
    parser.add_argument("-r", "--reference", help="Reference-Track für Matchering")
    parser.add_argument("-p", "--preset", default="streaming",
                        choices=list(PRESETS.keys()),
                        help="Mastering-Preset (default: streaming)")
    parser.add_argument("-o", "--output", help="Output-Verzeichnis")

    args = parser.parse_args()

    if args.input is None:
        interactive_mode()
        return

    files = find_audio_files(args.input)
    if not files:
        print(f"❌ Keine Audio-Dateien gefunden: {args.input}")
        sys.exit(1)

    print(f"\n🎧 Mastering {len(files)} Datei(en) mit Preset '{args.preset}'...")

    results = []
    for f in files:
        result = master_track(f, args.preset, args.output, args.reference)
        if result:
            results.append(result)

    print(f"\n✅ {len(results)}/{len(files)} Dateien gemastert.")


if __name__ == "__main__":
    main()

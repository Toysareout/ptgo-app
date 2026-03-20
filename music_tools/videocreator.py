#!/usr/bin/env python3
"""
VIDEOCREATOR.PY — Music Video Generator
=========================================
Erstellt Videos aus Audio + Bildern in drei Styles:
1. Slideshow — Bilder wechseln mit smooth Transitions
2. Waveform — Animierte Waveform + Bilder
3. Lyrics — Text erscheint synchron zum Song

Usage:
    python videocreator.py                              # Interaktiver Modus
    python videocreator.py song.wav --style slideshow   # Slideshow
    python videocreator.py song.wav --style waveform    # Waveform-Visualizer
    python videocreator.py song.wav --style lyrics      # Lyrics-Video
"""

import argparse
import os
import sys
import json
import numpy as np

try:
    from moviepy import (
        AudioFileClip, ImageClip, TextClip, ColorClip,
        CompositeVideoClip, concatenate_videoclips,
    )
    HAS_MOVIEPY = True
except ImportError:
    try:
        from moviepy.editor import (
            AudioFileClip, ImageClip, TextClip, ColorClip,
            CompositeVideoClip, concatenate_videoclips,
        )
        HAS_MOVIEPY = True
    except ImportError:
        HAS_MOVIEPY = False

try:
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    import soundfile as sf
    HAS_SOUNDFILE = True
except ImportError:
    HAS_SOUNDFILE = False


# ─── CONSTANTS ───────────────────────────────────────────────────────────────

VIDEO_WIDTH = 1920
VIDEO_HEIGHT = 1080
FPS = 30
SUPPORTED_IMG = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tiff"}
SUPPORTED_AUDIO = {".wav", ".mp3", ".flac", ".ogg", ".aiff"}


# ─── IMAGE HELPERS ───────────────────────────────────────────────────────────

def prepare_image(img_path: str, width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT) -> str:
    """Skaliert und croppt ein Bild auf die Videogröße."""
    if not HAS_PIL:
        return img_path

    img = Image.open(img_path).convert("RGB")
    img_ratio = img.width / img.height
    target_ratio = width / height

    if img_ratio > target_ratio:
        # Bild ist breiter → Höhe anpassen, Seiten croppen
        new_height = height
        new_width = int(height * img_ratio)
    else:
        # Bild ist höher → Breite anpassen, oben/unten croppen
        new_width = width
        new_height = int(width / img_ratio)

    img = img.resize((new_width, new_height), Image.LANCZOS)

    # Center-Crop
    left = (new_width - width) // 2
    top = (new_height - height) // 2
    img = img.crop((left, top, left + width, top + height))

    # Temp-Datei
    temp_path = img_path + f"_prepared_{width}x{height}.jpg"
    img.save(temp_path, "JPEG", quality=95)
    return temp_path


def create_gradient_background(width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT,
                                color1: tuple = (20, 20, 40),
                                color2: tuple = (60, 10, 80)) -> str:
    """Erstellt einen Gradient-Hintergrund."""
    img = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(img)

    for y in range(height):
        ratio = y / height
        r = int(color1[0] * (1 - ratio) + color2[0] * ratio)
        g = int(color1[1] * (1 - ratio) + color2[1] * ratio)
        b = int(color1[2] * (1 - ratio) + color2[2] * ratio)
        draw.line([(0, y), (width, y)], fill=(r, g, b))

    path = "/tmp/gradient_bg.jpg"
    img.save(path, "JPEG", quality=95)
    return path


# ─── WAVEFORM HELPERS ────────────────────────────────────────────────────────

def generate_waveform_frames(audio_path: str, duration: float,
                              width: int = VIDEO_WIDTH,
                              height: int = 200) -> list[np.ndarray]:
    """Generiert Waveform-Frames als numpy Arrays."""
    if not HAS_SOUNDFILE:
        return []

    audio, sr = sf.read(audio_path, dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)  # Mono

    total_frames = int(duration * FPS)
    samples_per_frame = len(audio) / total_frames
    frames = []

    for i in range(total_frames):
        # Aktuelles Fenster (ca. 0.1s um den aktuellen Punkt)
        center = int(i * samples_per_frame)
        window_size = int(sr * 0.1)
        start = max(0, center - window_size // 2)
        end = min(len(audio), center + window_size // 2)
        chunk = audio[start:end]

        # Waveform als Bild
        img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        if len(chunk) > 0:
            # Downsampling auf Bildbreite
            num_bars = width // 3  # 3px pro Bar
            bar_samples = max(1, len(chunk) // num_bars)

            for j in range(min(num_bars, len(chunk) // max(1, bar_samples))):
                segment = chunk[j * bar_samples:(j + 1) * bar_samples]
                amplitude = min(np.max(np.abs(segment)), 1.0)
                bar_height = int(amplitude * height * 0.8)

                x = j * 3
                y_center = height // 2

                # Farbe basierend auf Amplitude (cyan → magenta)
                r = int(amplitude * 255)
                g = int((1 - amplitude) * 255)
                b = 255

                draw.rectangle(
                    [x, y_center - bar_height // 2, x + 2, y_center + bar_height // 2],
                    fill=(r, g, b, 200),
                )

        frames.append(np.array(img))

    return frames


# ─── VIDEO STYLES ────────────────────────────────────────────────────────────

def create_slideshow(audio_path: str, images: list[str], output_path: str,
                     title: str = "", artist: str = "",
                     transition_duration: float = 1.0):
    """Style 1: Slideshow — Bilder wechseln mit Crossfade-Transitions."""
    print("  🎬 Style: Slideshow mit Crossfade")

    audio = AudioFileClip(audio_path)
    duration = audio.duration

    if not images:
        print("  ⚠ Keine Bilder, erstelle Gradient-Hintergrund")
        bg_path = create_gradient_background()
        images = [bg_path]

    # Dauer pro Bild berechnen
    num_images = len(images)
    time_per_image = duration / num_images

    print(f"  → {num_images} Bilder, je {time_per_image:.1f}s")

    # Clips erstellen
    clips = []
    for i, img_path in enumerate(images):
        prepared = prepare_image(img_path)
        clip = (
            ImageClip(prepared)
            .with_duration(time_per_image + transition_duration)
            .with_start(max(0, i * time_per_image - transition_duration / 2))
            .resized((VIDEO_WIDTH, VIDEO_HEIGHT))
        )

        # Crossfade (außer erstes Bild)
        if i > 0:
            clip = clip.crossfadein(transition_duration)

        clips.append(clip)

    video = CompositeVideoClip(clips, size=(VIDEO_WIDTH, VIDEO_HEIGHT))
    video = video.with_duration(duration)

    # Text-Overlays
    overlay_clips = [video]
    if title or artist:
        overlay_clips.extend(_create_text_overlays(title, artist, duration))

    final = CompositeVideoClip(overlay_clips, size=(VIDEO_WIDTH, VIDEO_HEIGHT))
    final = final.with_audio(audio).with_duration(duration)

    print(f"  → Rendering: {output_path}")
    final.write_videofile(
        output_path, fps=FPS, codec="libx264",
        audio_codec="aac", audio_bitrate="320k",
        preset="medium",
        logger="bar",
    )
    print(f"  ✅ Video erstellt: {output_path}")


def create_waveform_video(audio_path: str, images: list[str], output_path: str,
                           title: str = "", artist: str = ""):
    """Style 2: Waveform — Animierte Waveform + Hintergrundbild."""
    print("  🎬 Style: Waveform-Visualizer")

    audio = AudioFileClip(audio_path)
    duration = audio.duration

    # Hintergrundbild (erstes Bild oder Gradient)
    if images:
        bg_path = prepare_image(images[0])
    else:
        bg_path = create_gradient_background()

    bg_clip = ImageClip(bg_path).with_duration(duration).resized((VIDEO_WIDTH, VIDEO_HEIGHT))

    # Dunkler Overlay für bessere Lesbarkeit
    dark_overlay = ColorClip(
        size=(VIDEO_WIDTH, VIDEO_HEIGHT),
        color=(0, 0, 0),
    ).with_duration(duration).with_opacity(0.5)

    # Waveform-Frames generieren
    print("  → Generiere Waveform-Frames...")
    waveform_height = 200
    waveform_frames = generate_waveform_frames(audio_path, duration, VIDEO_WIDTH, waveform_height)

    if waveform_frames:
        def make_frame(t):
            frame_idx = min(int(t * FPS), len(waveform_frames) - 1)
            return waveform_frames[frame_idx][:, :, :3]  # RGB only

        from moviepy import VideoClip
        waveform_clip = (
            VideoClip(make_frame, duration=duration)
            .with_position(("center", VIDEO_HEIGHT - waveform_height - 50))
        )

        clips = [bg_clip, dark_overlay, waveform_clip]
    else:
        clips = [bg_clip, dark_overlay]

    # Text-Overlays
    if title or artist:
        clips.extend(_create_text_overlays(title, artist, duration, y_pos="center"))

    final = CompositeVideoClip(clips, size=(VIDEO_WIDTH, VIDEO_HEIGHT))
    final = final.with_audio(audio).with_duration(duration)

    print(f"  → Rendering: {output_path}")
    final.write_videofile(
        output_path, fps=FPS, codec="libx264",
        audio_codec="aac", audio_bitrate="320k",
        preset="medium",
        logger="bar",
    )
    print(f"  ✅ Video erstellt: {output_path}")


def create_lyrics_video(audio_path: str, images: list[str], output_path: str,
                         title: str = "", artist: str = "",
                         lyrics: list[dict] | None = None):
    """
    Style 3: Lyrics-Video — Text erscheint synchron zur Musik.

    lyrics format: [{"time": 0.0, "text": "Erste Zeile"}, {"time": 5.0, "text": "Zweite Zeile"}, ...]
    """
    print("  🎬 Style: Lyrics-Video")

    audio = AudioFileClip(audio_path)
    duration = audio.duration

    # Hintergrundbild
    if images:
        bg_path = prepare_image(images[0])
    else:
        bg_path = create_gradient_background()

    bg_clip = ImageClip(bg_path).with_duration(duration).resized((VIDEO_WIDTH, VIDEO_HEIGHT))

    dark_overlay = ColorClip(
        size=(VIDEO_WIDTH, VIDEO_HEIGHT),
        color=(0, 0, 0),
    ).with_duration(duration).with_opacity(0.6)

    clips = [bg_clip, dark_overlay]

    # Lyrics als TextClips
    if lyrics:
        print(f"  → {len(lyrics)} Lyrics-Zeilen")
        for i, line in enumerate(lyrics):
            start_time = line["time"]
            text = line["text"]

            # Dauer bis zur nächsten Zeile
            if i + 1 < len(lyrics):
                line_duration = lyrics[i + 1]["time"] - start_time
            else:
                line_duration = duration - start_time

            line_duration = max(0.5, line_duration)

            try:
                txt_clip = (
                    TextClip(
                        text=text,
                        font_size=60,
                        color="white",
                        font="Arial-Bold",
                        stroke_color="black",
                        stroke_width=2,
                        size=(VIDEO_WIDTH - 200, None),
                        method="caption",
                        text_align="center",
                    )
                    .with_duration(line_duration)
                    .with_start(start_time)
                    .with_position(("center", "center"))
                    .crossfadein(0.3)
                    .crossfadeout(0.3)
                )
                clips.append(txt_clip)
            except Exception as e:
                print(f"  ⚠ TextClip-Fehler für '{text}': {e}")

    # Title/Artist Overlay (oben)
    if title or artist:
        clips.extend(_create_text_overlays(title, artist, duration, y_pos=80))

    final = CompositeVideoClip(clips, size=(VIDEO_WIDTH, VIDEO_HEIGHT))
    final = final.with_audio(audio).with_duration(duration)

    print(f"  → Rendering: {output_path}")
    final.write_videofile(
        output_path, fps=FPS, codec="libx264",
        audio_codec="aac", audio_bitrate="320k",
        preset="medium",
        logger="bar",
    )
    print(f"  ✅ Video erstellt: {output_path}")


# ─── TEXT OVERLAYS ───────────────────────────────────────────────────────────

def _create_text_overlays(title: str, artist: str, duration: float,
                           y_pos: int | str = 80) -> list:
    """Erstellt Titel/Artist-Overlays."""
    overlays = []

    try:
        if title:
            title_clip = (
                TextClip(
                    text=title,
                    font_size=72,
                    color="white",
                    font="Arial-Bold",
                    stroke_color="black",
                    stroke_width=2,
                )
                .with_duration(duration)
                .with_position(("center", y_pos if isinstance(y_pos, int) else 400))
            )
            overlays.append(title_clip)

        if artist:
            y_artist = (y_pos + 90) if isinstance(y_pos, int) else 490
            artist_clip = (
                TextClip(
                    text=artist,
                    font_size=42,
                    color="#cccccc",
                    font="Arial",
                    stroke_color="black",
                    stroke_width=1,
                )
                .with_duration(duration)
                .with_position(("center", y_artist))
            )
            overlays.append(artist_clip)
    except Exception as e:
        print(f"  ⚠ Text-Overlay Fehler: {e}")

    return overlays


# ─── LYRICS INPUT ────────────────────────────────────────────────────────────

def input_lyrics_interactive() -> list[dict]:
    """Lyrics interaktiv eingeben mit Timestamps."""
    print("\n  📝 Lyrics eingeben (Format: SEKUNDEN TEXT)")
    print("  Beispiel: 0 Erste Zeile des Songs")
    print("  Beispiel: 5.5 Zweite Zeile")
    print("  Leere Zeile = fertig\n")

    lyrics = []
    while True:
        line = input("  → ").strip()
        if not line:
            break

        parts = line.split(maxsplit=1)
        if len(parts) < 2:
            print("  ⚠ Format: SEKUNDEN TEXT")
            continue

        try:
            time = float(parts[0])
            text = parts[1]
            lyrics.append({"time": time, "text": text})
        except ValueError:
            print("  ⚠ Ungültige Zeitangabe")

    return lyrics


def load_lyrics_file(filepath: str) -> list[dict]:
    """Lädt Lyrics aus einer JSON-Datei."""
    with open(filepath) as f:
        return json.load(f)


# ─── INTERACTIVE MODE ────────────────────────────────────────────────────────

def interactive_mode():
    """Interaktiver Modus."""
    print("\n" + "="*60)
    print("  🎬 VIDEOCREATOR.PY — Music Video Generator")
    print("  Styles: Slideshow | Waveform | Lyrics")
    print("="*60)

    # Libraries prüfen
    print("\n  Libraries:")
    print(f"    moviepy:    {'✅' if HAS_MOVIEPY else '❌ nicht installiert'}")
    print(f"    Pillow:     {'✅' if HAS_PIL else '⚠ optional'}")
    print(f"    soundfile:  {'✅' if HAS_SOUNDFILE else '⚠ optional (für Waveform)'}")

    if not HAS_MOVIEPY:
        print("\n  ❌ moviepy wird benötigt! pip install moviepy")
        sys.exit(1)

    # Audio-Datei
    print("\n  🎵 Pfad zur Audio-Datei:")
    audio_path = input("  → ").strip().strip("'\"")

    if not audio_path or not os.path.isfile(audio_path):
        print("  ❌ Audio-Datei nicht gefunden.")
        sys.exit(1)

    # Song-Info
    print("\n  📋 Song-Titel (Leer = kein Overlay):")
    title = input("  → ").strip()

    print("  👤 Artist-Name (Leer = kein Overlay):")
    artist = input("  → ").strip()

    # Style wählen
    print("\n  🎨 Video-Style:")
    print("    1. Slideshow — Bilder wechseln mit Crossfade")
    print("    2. Waveform  — Animierte Waveform + Hintergrundbild")
    print("    3. Lyrics    — Text erscheint synchron zur Musik")

    style_choice = input("  → [1-3, default=1]: ").strip()
    style_map = {"1": "slideshow", "2": "waveform", "3": "lyrics"}
    style = style_map.get(style_choice, "slideshow")

    # Bilder
    print(f"\n  🖼  Bilder eingeben (eins pro Zeile, Leer = fertig):")
    if style == "slideshow":
        print("     Tipp: Mehr Bilder = kürzere Anzeigedauer pro Bild")
    elif style in ("waveform", "lyrics"):
        print("     Tipp: Das erste Bild wird als Hintergrund verwendet")

    images = []
    while True:
        img = input("  → ").strip().strip("'\"")
        if not img:
            break
        if os.path.isfile(img):
            ext = os.path.splitext(img)[1].lower()
            if ext in SUPPORTED_IMG:
                images.append(img)
                print(f"     ✅ {os.path.basename(img)}")
            else:
                print(f"     ⚠ Nicht unterstützt: {ext}")
        elif os.path.isdir(img):
            # Alle Bilder im Ordner
            for f in sorted(os.listdir(img)):
                if os.path.splitext(f)[1].lower() in SUPPORTED_IMG:
                    full_path = os.path.join(img, f)
                    images.append(full_path)
                    print(f"     ✅ {f}")
        else:
            print(f"     ⚠ Datei nicht gefunden: {img}")

    print(f"\n  📊 {len(images)} Bild(er) geladen")

    # Lyrics (nur bei Lyrics-Style)
    lyrics = None
    if style == "lyrics":
        print("\n  Lyrics laden von Datei? (JSON mit [{time, text}, ...])")
        lyrics_file = input("  → Datei-Pfad (Leer = manuell eingeben): ").strip().strip("'\"")

        if lyrics_file and os.path.isfile(lyrics_file):
            lyrics = load_lyrics_file(lyrics_file)
            print(f"  ✅ {len(lyrics)} Zeilen geladen")
        else:
            lyrics = input_lyrics_interactive()

    # Output
    name = os.path.splitext(os.path.basename(audio_path))[0]
    default_output = f"{name}_{style}.mp4"
    print(f"\n  💾 Output-Datei (default: {default_output}):")
    output_path = input("  → ").strip().strip("'\"") or default_output

    # Erstellen
    print(f"\n  🚀 Erstelle {style.upper()} Video...")
    print(f"     Audio:  {os.path.basename(audio_path)}")
    print(f"     Bilder: {len(images)}")
    print(f"     Output: {output_path}")

    if style == "slideshow":
        create_slideshow(audio_path, images, output_path, title, artist)
    elif style == "waveform":
        create_waveform_video(audio_path, images, output_path, title, artist)
    elif style == "lyrics":
        create_lyrics_video(audio_path, images, output_path, title, artist, lyrics)

    return output_path


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="🎬 Music Video Generator — Erstelle Videos aus Audio + Bildern",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Beispiele:
  python videocreator.py                                    # Interaktiv
  python videocreator.py song.wav --style slideshow -i *.jpg
  python videocreator.py song.wav --style waveform -i cover.jpg
  python videocreator.py song.wav --style lyrics --lyrics lyrics.json
        """,
    )
    parser.add_argument("audio", nargs="?", help="Audio-Datei")
    parser.add_argument("-s", "--style", default="slideshow",
                        choices=["slideshow", "waveform", "lyrics"],
                        help="Video-Style (default: slideshow)")
    parser.add_argument("-i", "--images", nargs="+", help="Bilder für das Video")
    parser.add_argument("-t", "--title", default="", help="Song-Titel")
    parser.add_argument("-a", "--artist", default="", help="Artist-Name")
    parser.add_argument("-l", "--lyrics", help="Lyrics-Datei (JSON)")
    parser.add_argument("-o", "--output", help="Output-Datei")

    args = parser.parse_args()

    if args.audio is None:
        interactive_mode()
        return

    images = args.images or []
    output = args.output or f"{os.path.splitext(os.path.basename(args.audio))[0]}_{args.style}.mp4"

    lyrics = None
    if args.lyrics and os.path.isfile(args.lyrics):
        lyrics = load_lyrics_file(args.lyrics)

    if args.style == "slideshow":
        create_slideshow(args.audio, images, output, args.title, args.artist)
    elif args.style == "waveform":
        create_waveform_video(args.audio, images, output, args.title, args.artist)
    elif args.style == "lyrics":
        create_lyrics_video(args.audio, images, output, args.title, args.artist, lyrics)


if __name__ == "__main__":
    main()

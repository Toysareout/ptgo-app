#!/usr/bin/env python3
"""
YOUTUBE_UPLOAD.PY — YouTube Automation
=======================================
Erstellt YouTube-Upload-Pakete: Titel, Beschreibung, Tags, Thumbnail.
Uploadet Videos direkt via YouTube Data API v3.
AI-generierte SEO-optimierte Beschreibungen via Claude.

Usage:
    python youtube_upload.py                              # Interaktiver Modus
    python youtube_upload.py video.mp4                    # Upload mit AI-Beschreibung
    python youtube_upload.py video.mp4 --generate-only    # Nur Metadaten generieren
    python youtube_upload.py video.mp4 --schedule "2026-03-25T18:00:00Z"
"""

import argparse
import json
import os
import sys
import re
from datetime import datetime, timezone

# ─── YouTube API ─────────────────────────────────────────────────────────────

try:
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    HAS_YOUTUBE_API = True
except ImportError:
    HAS_YOUTUBE_API = False

try:
    import anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False


# ─── CONSTANTS ───────────────────────────────────────────────────────────────

YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
                  "https://www.googleapis.com/auth/youtube"]
TOKEN_FILE = os.path.expanduser("~/.youtube_token.json")
CLIENT_SECRETS_FILE = os.path.expanduser("~/.youtube_client_secrets.json")

CATEGORY_IDS = {
    "music": "10",
    "entertainment": "24",
    "people_blogs": "22",
    "education": "27",
    "gaming": "20",
}

# YouTube-optimierte Video-Kategorien
MUSIC_GENRES = [
    "Rock", "Pop", "Hip Hop", "Electronic", "R&B", "Jazz",
    "Classical", "Metal", "Punk", "Indie", "Alternative",
    "Folk", "Country", "Reggae", "Latin", "Blues",
]


# ─── AI METADATA GENERATION ─────────────────────────────────────────────────

def generate_metadata_with_ai(song_title: str, artist: str,
                                genre: str = "", mood: str = "",
                                extra_info: str = "") -> dict:
    """Generiert YouTube-Metadaten via Claude AI."""
    if not HAS_ANTHROPIC:
        print("  ⚠ anthropic nicht installiert, verwende Standard-Template")
        return _generate_fallback_metadata(song_title, artist, genre)

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("  ⚠ ANTHROPIC_API_KEY nicht gesetzt, verwende Standard-Template")
        return _generate_fallback_metadata(song_title, artist, genre)

    client = anthropic.Anthropic(api_key=api_key)

    prompt = f"""Du bist ein YouTube Music Marketing Experte. Erstelle optimale YouTube-Metadaten für diesen Song.

Song: "{song_title}" von {artist}
Genre: {genre or "nicht angegeben"}
Stimmung: {mood or "nicht angegeben"}
Weitere Infos: {extra_info or "keine"}

Erstelle ein JSON mit exakt diesem Format:
{{
    "title": "YouTube-Titel (max 100 Zeichen, SEO-optimiert, mit Emoji)",
    "description": "Vollständige YouTube-Beschreibung mit:\\n- Intro-Text über den Song\\n- Timestamps (0:00 Start etc.)\\n- Credits\\n- Social Media Links (Platzhalter)\\n- Hashtags\\n- Copyright-Info",
    "tags": ["tag1", "tag2", "..."],  // 15-25 relevante Tags
    "category": "music"
}}

Wichtig:
- Titel soll klickbar aber authentisch sein
- Beschreibung mit SEO-Keywords natürlich einbauen
- Tags: Mix aus spezifisch + breit (Englisch + Deutsch)
- Denke an Trending-Keywords für Music auf YouTube
- Nutze aktuelle YouTube SEO Best Practices 2026

Antworte NUR mit dem JSON, kein anderer Text."""

    try:
        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        content = response.content[0].text.strip()

        # JSON extrahieren
        json_match = re.search(r'\{[\s\S]*\}', content)
        if json_match:
            return json.loads(json_match.group())
    except Exception as e:
        print(f"  ⚠ AI-Fehler: {e}")

    return _generate_fallback_metadata(song_title, artist, genre)


def _generate_fallback_metadata(title: str, artist: str, genre: str = "") -> dict:
    """Fallback-Metadaten ohne AI."""
    genre_tags = [genre.lower()] if genre else ["music"]
    return {
        "title": f"{artist} - {title} (Official Audio)",
        "description": f"""🎵 {artist} - {title}

Hör dir den neuesten Track von {artist} an!

⏱ Timestamps:
0:00 Start

📱 Social Media:
[Links hier einfügen]

🎵 Streaming:
[Spotify/Apple Music Links hier einfügen]

#{''.join(w.capitalize() for w in artist.split())} #{title.replace(' ', '')} #NewMusic #{''.join(w.capitalize() for w in genre.split()) if genre else 'Music'}

© {datetime.now().year} {artist}. All rights reserved.""",
        "tags": [
            artist, title, f"{artist} {title}", f"{artist} new song",
            "new music", "official audio", "new release",
            *genre_tags, "musik", "neu",
        ],
        "category": "music",
    }


# ─── YOUTUBE AUTH ────────────────────────────────────────────────────────────

def get_youtube_service():
    """Authentifiziert und gibt den YouTube API Service zurück."""
    if not HAS_YOUTUBE_API:
        raise RuntimeError(
            "google-api-python-client nicht installiert.\n"
            "pip install google-api-python-client google-auth-oauthlib"
        )

    creds = None

    # Gespeicherte Credentials laden
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, YOUTUBE_SCOPES)

    # Refresh oder neu authentifizieren
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CLIENT_SECRETS_FILE):
                print(f"\n  ❌ Client Secrets nicht gefunden: {CLIENT_SECRETS_FILE}")
                print("  So richtest du YouTube API ein:")
                print("  1. Gehe zu https://console.cloud.google.com/")
                print("  2. Erstelle ein Projekt")
                print("  3. Aktiviere 'YouTube Data API v3'")
                print("  4. Erstelle OAuth 2.0 Credentials (Desktop App)")
                print(f"  5. Lade die JSON-Datei herunter und speichere sie als:")
                print(f"     {CLIENT_SECRETS_FILE}")
                raise FileNotFoundError("YouTube Client Secrets fehlen")

            flow = InstalledAppFlow.from_client_secrets_file(
                CLIENT_SECRETS_FILE, YOUTUBE_SCOPES
            )
            creds = flow.run_local_server(port=0)

        # Token speichern
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())

    return build("youtube", "v3", credentials=creds)


# ─── UPLOAD ──────────────────────────────────────────────────────────────────

def upload_video(video_path: str, metadata: dict,
                 thumbnail_path: str | None = None,
                 schedule_time: str | None = None,
                 playlist_id: str | None = None) -> str | None:
    """Uploadet ein Video zu YouTube."""
    print(f"\n  📤 Uploading: {os.path.basename(video_path)}")
    print(f"     Titel: {metadata['title']}")

    try:
        youtube = get_youtube_service()
    except (RuntimeError, FileNotFoundError) as e:
        print(f"  ❌ {e}")
        return None

    # Privacy Status
    privacy = "private"
    if schedule_time:
        privacy = "private"  # Scheduled videos müssen privat sein
        print(f"     Geplant für: {schedule_time}")
    else:
        privacy = "public"

    body = {
        "snippet": {
            "title": metadata["title"][:100],  # YouTube max 100 chars
            "description": metadata["description"][:5000],  # YouTube max 5000 chars
            "tags": metadata.get("tags", [])[:500],  # YouTube max 500 tags
            "categoryId": CATEGORY_IDS.get(metadata.get("category", "music"), "10"),
            "defaultLanguage": "de",
            "defaultAudioLanguage": "de",
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
        },
    }

    if schedule_time:
        body["status"]["publishAt"] = schedule_time

    # Upload
    media = MediaFileUpload(
        video_path,
        mimetype="video/mp4",
        resumable=True,
        chunksize=50 * 1024 * 1024,  # 50MB Chunks
    )

    request = youtube.videos().insert(
        part=",".join(body.keys()),
        body=body,
        media_body=media,
    )

    print("  → Upload läuft...")
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            progress = int(status.progress() * 100)
            print(f"  → {progress}% hochgeladen...")

    video_id = response["id"]
    video_url = f"https://youtube.com/watch?v={video_id}"
    print(f"  ✅ Upload erfolgreich!")
    print(f"  🔗 {video_url}")

    # Thumbnail setzen
    if thumbnail_path and os.path.isfile(thumbnail_path):
        try:
            youtube.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(thumbnail_path, mimetype="image/jpeg"),
            ).execute()
            print(f"  🖼  Thumbnail gesetzt")
        except Exception as e:
            print(f"  ⚠ Thumbnail-Fehler: {e}")

    # Zu Playlist hinzufügen
    if playlist_id:
        try:
            youtube.playlistItems().insert(
                part="snippet",
                body={
                    "snippet": {
                        "playlistId": playlist_id,
                        "resourceId": {
                            "kind": "youtube#video",
                            "videoId": video_id,
                        },
                    },
                },
            ).execute()
            print(f"  📋 Zur Playlist hinzugefügt")
        except Exception as e:
            print(f"  ⚠ Playlist-Fehler: {e}")

    return video_url


# ─── SAVE METADATA ──────────────────────────────────────────────────────────

def save_metadata_package(metadata: dict, output_dir: str, video_name: str):
    """Speichert Metadaten als JSON + Textdateien für manuelle Nutzung."""
    os.makedirs(output_dir, exist_ok=True)

    # JSON (komplett)
    json_path = os.path.join(output_dir, f"{video_name}_youtube.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)
    print(f"  📄 JSON: {json_path}")

    # Titel (Copy-Paste ready)
    title_path = os.path.join(output_dir, f"{video_name}_title.txt")
    with open(title_path, "w", encoding="utf-8") as f:
        f.write(metadata["title"])
    print(f"  📄 Titel: {title_path}")

    # Beschreibung (Copy-Paste ready)
    desc_path = os.path.join(output_dir, f"{video_name}_description.txt")
    with open(desc_path, "w", encoding="utf-8") as f:
        f.write(metadata["description"])
    print(f"  📄 Beschreibung: {desc_path}")

    # Tags (kommasepariert, Copy-Paste ready)
    tags_path = os.path.join(output_dir, f"{video_name}_tags.txt")
    with open(tags_path, "w", encoding="utf-8") as f:
        f.write(", ".join(metadata.get("tags", [])))
    print(f"  📄 Tags: {tags_path}")

    return json_path


# ─── INTERACTIVE MODE ────────────────────────────────────────────────────────

def interactive_mode():
    """Interaktiver Modus."""
    print("\n" + "="*60)
    print("  📺 YOUTUBE_UPLOAD.PY — YouTube Automation")
    print("  AI-generierte Metadaten + Direct Upload")
    print("="*60)

    # Libraries
    print("\n  Libraries:")
    print(f"    YouTube API:  {'✅' if HAS_YOUTUBE_API else '⚠ nicht installiert (nur Metadaten-Export)'}")
    print(f"    Anthropic:    {'✅' if HAS_ANTHROPIC else '⚠ ohne AI, Standard-Templates'}")

    # Video-Datei
    print("\n  🎬 Pfad zur Video-Datei (MP4):")
    video_path = input("  → ").strip().strip("'\"")

    if not video_path or not os.path.isfile(video_path):
        print("  ⚠ Kein Video angegeben — erstelle nur Metadaten")
        video_path = None

    # Song-Infos
    print("\n  🎵 Song-Titel:")
    song_title = input("  → ").strip()
    if not song_title:
        song_title = os.path.splitext(os.path.basename(video_path))[0] if video_path else "Untitled"

    print("  👤 Artist/Band:")
    artist = input("  → ").strip() or "Unknown Artist"

    print(f"\n  🎸 Genre:")
    for i, genre in enumerate(MUSIC_GENRES[:8], 1):
        print(f"    {i}. {genre}", end="  ")
        if i % 4 == 0:
            print()
    print()

    genre_input = input("  → [Nummer oder eigenes Genre]: ").strip()
    if genre_input.isdigit() and 1 <= int(genre_input) <= len(MUSIC_GENRES):
        genre = MUSIC_GENRES[int(genre_input) - 1]
    else:
        genre = genre_input or ""

    print("  💭 Stimmung/Mood (z.B. energetisch, melancholisch, aggressiv):")
    mood = input("  → ").strip()

    print("  📝 Weitere Infos (z.B. Album, Features, Story hinter dem Song):")
    extra_info = input("  → ").strip()

    # AI-Metadaten generieren
    print("\n  🤖 Generiere YouTube-Metadaten...")
    metadata = generate_metadata_with_ai(song_title, artist, genre, mood, extra_info)

    # Preview
    print(f"\n{'='*60}")
    print("  📋 PREVIEW")
    print(f"{'='*60}")
    print(f"\n  Titel: {metadata['title']}")
    print(f"\n  Beschreibung:\n  {'-'*40}")
    for line in metadata['description'].split('\n'):
        print(f"  {line}")
    print(f"  {'-'*40}")
    print(f"\n  Tags: {', '.join(metadata.get('tags', [])[:10])}...")

    # Bearbeiten?
    print("\n  Zufrieden mit den Metadaten?")
    print("    1. Ja, weiter")
    print("    2. Titel ändern")
    print("    3. Beschreibung ändern")
    print("    4. Neu generieren")

    edit_choice = input("  → [1-4, default=1]: ").strip()

    if edit_choice == "2":
        print("  Neuer Titel:")
        metadata["title"] = input("  → ").strip() or metadata["title"]
    elif edit_choice == "3":
        print("  Neue Beschreibung (mehrzeilig, leere Zeile = fertig):")
        lines = []
        while True:
            line = input("  ")
            if not line:
                break
            lines.append(line)
        if lines:
            metadata["description"] = "\n".join(lines)
    elif edit_choice == "4":
        metadata = generate_metadata_with_ai(song_title, artist, genre, mood, extra_info)

    # Metadaten speichern
    video_name = os.path.splitext(os.path.basename(video_path))[0] if video_path else song_title.replace(" ", "_")
    output_dir = os.path.join(os.path.dirname(video_path) if video_path else ".", "youtube_package")

    print(f"\n  💾 Speichere Metadaten-Paket...")
    save_metadata_package(metadata, output_dir, video_name)

    # Upload?
    if video_path and HAS_YOUTUBE_API:
        print("\n  📤 Video jetzt zu YouTube hochladen?")
        print("    1. Ja, jetzt hochladen (öffentlich)")
        print("    2. Ja, aber als privat")
        print("    3. Ja, geplante Veröffentlichung")
        print("    4. Nein, nur Metadaten speichern")

        upload_choice = input("  → [1-4, default=4]: ").strip()

        thumbnail_path = None
        print("\n  🖼  Thumbnail (Leer = kein Thumbnail):")
        thumb = input("  → ").strip().strip("'\"")
        if thumb and os.path.isfile(thumb):
            thumbnail_path = thumb

        playlist_id = None
        print("  📋 Playlist-ID (Leer = keine Playlist):")
        pl = input("  → ").strip()
        if pl:
            playlist_id = pl

        schedule_time = None
        if upload_choice == "3":
            print("  📅 Veröffentlichungszeitpunkt (ISO 8601, z.B. 2026-03-25T18:00:00Z):")
            schedule_time = input("  → ").strip()

        if upload_choice in ("1", "2", "3"):
            if upload_choice == "2":
                # Privat Upload — modifiziere metadata temporär
                pass

            video_url = upload_video(
                video_path, metadata,
                thumbnail_path=thumbnail_path,
                schedule_time=schedule_time,
                playlist_id=playlist_id,
            )

            if video_url:
                print(f"\n  🎉 Alles fertig! Video-URL: {video_url}")
        else:
            print("\n  ✅ Metadaten gespeichert. Du kannst sie manuell in YouTube Studio verwenden.")

    elif video_path and not HAS_YOUTUBE_API:
        print("\n  ℹ  YouTube API nicht installiert.")
        print("     Du kannst die gespeicherten Metadaten manuell in YouTube Studio einfügen.")
        print("     Für direkten Upload: pip install google-api-python-client google-auth-oauthlib")
    else:
        print("\n  ✅ Metadaten-Paket erstellt!")

    print(f"\n{'='*60}")
    print(f"  📁 Alle Dateien in: {output_dir}")
    print(f"{'='*60}\n")


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="📺 YouTube Automation — Upload + AI-Metadaten",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Beispiele:
  python youtube_upload.py                                # Interaktiv
  python youtube_upload.py video.mp4                      # Upload mit AI-Beschreibung
  python youtube_upload.py video.mp4 --generate-only      # Nur Metadaten
  python youtube_upload.py video.mp4 --title "Mein Song" --artist "Band"
  python youtube_upload.py video.mp4 --schedule "2026-03-25T18:00:00Z"

Setup für YouTube Upload:
  1. Google Cloud Console: https://console.cloud.google.com/
  2. Projekt erstellen + YouTube Data API v3 aktivieren
  3. OAuth 2.0 Credentials erstellen (Desktop App)
  4. JSON herunterladen → ~/.youtube_client_secrets.json
        """,
    )
    parser.add_argument("video", nargs="?", help="Video-Datei (MP4)")
    parser.add_argument("-t", "--title", default="", help="Song-Titel")
    parser.add_argument("-a", "--artist", default="", help="Artist/Band")
    parser.add_argument("-g", "--genre", default="", help="Genre")
    parser.add_argument("-m", "--mood", default="", help="Stimmung/Mood")
    parser.add_argument("--thumbnail", help="Thumbnail-Bild")
    parser.add_argument("--playlist", help="YouTube Playlist-ID")
    parser.add_argument("--schedule", help="Veröffentlichungszeitpunkt (ISO 8601)")
    parser.add_argument("--generate-only", action="store_true",
                        help="Nur Metadaten generieren, kein Upload")

    args = parser.parse_args()

    if args.video is None:
        interactive_mode()
        return

    # Metadaten generieren
    title = args.title or os.path.splitext(os.path.basename(args.video))[0]
    artist = args.artist or "Unknown Artist"

    print(f"\n📺 YouTube Automation für: {os.path.basename(args.video)}")
    print("→ Generiere Metadaten...")

    metadata = generate_metadata_with_ai(title, artist, args.genre, args.mood)

    # Metadaten speichern
    video_name = os.path.splitext(os.path.basename(args.video))[0]
    output_dir = os.path.join(os.path.dirname(args.video) or ".", "youtube_package")
    save_metadata_package(metadata, output_dir, video_name)

    if args.generate_only:
        print(f"\n✅ Metadaten gespeichert in: {output_dir}")
        return

    # Upload
    video_url = upload_video(
        args.video, metadata,
        thumbnail_path=args.thumbnail,
        schedule_time=args.schedule,
        playlist_id=args.playlist,
    )

    if video_url:
        print(f"\n🎉 Fertig! {video_url}")


if __name__ == "__main__":
    main()
